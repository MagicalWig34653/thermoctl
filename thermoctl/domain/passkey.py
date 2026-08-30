"""Die beiden WebAuthn-Zeremonien: einen Passkey hinterlegen und sich damit anmelden.

Die Regeln stehen hier und nicht im Adapter, damit sie fuer jeden Weg gelten. Fuenf davon
sind der eigentliche Inhalt dieses Moduls — ohne sie ist WebAuthn nur ein umstaendliches
Passwort:

1. **Die Challenge liegt ausschliesslich beim Dienst.** Wer seine eigene setzen kann, kann
   eine alte Antwort erneut einreichen.
2. **Sie wird in jedem Fall verbraucht**, auch wenn die Pruefung scheitert. Eine
   wiederverwendbare Challenge hebt den Schutz auf, den sie geben soll.
3. **Sie ist an ihre Zeremonie gebunden.** Eine fuer die Anmeldung ausgegebene Challenge
   darf sich nicht fuer eine Registrierung einreichen lassen.
4. **Relying-Party-ID und Origin kommen aus der Konfiguration**, nie aus der Anfrage. Die
   `Host`-Kopfzeile setzt der Aufrufer; eine Relying-Party-ID unter seiner Kontrolle macht
   die Pruefung wertlos.
5. **Ein zurueckgefallener Zaehler beendet die Anmeldung.** Authenticatoren zaehlen ihre
   Benutzungen hoch. Zaehlt einer zurueck, gibt es den Schluessel zweimal — das ist der
   einzige Hinweis auf einen geklonten Authenticator, den das Verfahren ueberhaupt kennt.

Nach aussen sieht jede gescheiterte Anmeldung gleich aus. Ob eine Credential-ID unbekannt
ist, ein Konto gesperrt oder eine Signatur falsch, steht im Audit-Protokoll und nirgends
sonst; sonst liesse sich an den Antworten ablesen, welche Konten es gibt.
"""

import json
import secrets
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from thermoctl import audit
from thermoctl.config import Settings
from thermoctl.db.base import utcnow
from thermoctl.db.models.identity import User
from thermoctl.db.models.passkey import PasskeyChallenge, UserPasskey

LOGIN = "anmeldung"
REGISTRIERUNG = "registrierung"

# Eine Challenge gilt zwei Minuten. Lang genug, um einen Authenticator zu suchen und
# anzutippen; kurz genug, dass eine abgefangene nicht spaeter noch etwas wert ist.
CHALLENGE_GUELTIG = timedelta(minutes=2)


class PasskeyError(Exception):
    """Die Zeremonie ist gescheitert. Der Grund gehoert ins Protokoll, nicht nach aussen."""


def _challenge_merken(
    session: Session, zeremonie: str, user_id: int | None = None
) -> bytes:
    """Erzeugt eine Challenge, legt sie ab und gibt sie zurueck."""
    roh = secrets.token_bytes(32)
    session.add(
        PasskeyChallenge(
            challenge=bytes_to_base64url(roh), zeremonie=zeremonie, user_id=user_id
        )
    )
    session.flush()
    return roh


def _challenge_einloesen(
    session: Session, zeremonie: str, clientdaten: dict[str, Any]
) -> bytes:
    """Nimmt die Challenge aus der Antwort, prueft sie und **loescht sie in jedem Fall**.

    Der Wert kommt aus `clientDataJSON` des Authenticators. Er wird hier nicht geglaubt,
    sondern nur benutzt, um die abgelegte Zeile zu finden — die Signaturpruefung vergleicht
    ihn anschliessend selbst gegen das, was wir ausgegeben haben.
    """
    kandidat = clientdaten.get("challenge")
    if not isinstance(kandidat, str):
        raise PasskeyError("Die Antwort enthaelt keine Challenge.")

    entry = session.scalar(
        select(PasskeyChallenge).where(PasskeyChallenge.challenge == kandidat)
    )
    if entry is None:
        raise PasskeyError("Unbekannte oder bereits verbrauchte Challenge.")

    # Erst loeschen, dann urteilen: Auch eine abgelaufene oder zweckfremde Challenge ist
    # danach verbraucht. Sonst liesse sie sich beliebig oft erneut einreichen.
    ceremony_of_the_row = entry.zeremonie
    age = utcnow() - entry.created_at
    session.delete(entry)
    session.flush()

    if ceremony_of_the_row != zeremonie:
        raise PasskeyError(
            f"Challenge war fuer '{ceremony_of_the_row}' ausgegeben, nicht fuer "
            f"'{zeremonie}'."
        )
    if age > CHALLENGE_GUELTIG:
        raise PasskeyError("Challenge ist abgelaufen.")
    return base64url_to_bytes(kandidat)


def cleanup_old_challenges(session: Session) -> int:
    """Entfernt abgelaufene Challenges. Sie sind wertlos, aber sie sammeln sich an."""
    limit = utcnow() - CHALLENGE_GUELTIG
    result = session.execute(
        delete(PasskeyChallenge).where(PasskeyChallenge.created_at < limit)
    )
    # `rowcount` steht nur auf CursorResult, nicht auf dem allgemeinen Result-Typ;
    # bei einem DELETE ist es immer ein CursorResult.
    return int(result.rowcount)  # type: ignore[attr-defined]


def begin_registration(
    session: Session, settings: Settings, user: User
) -> dict[str, Any]:
    """Die Argumente fuer `navigator.credentials.create()`."""
    vorhandene = session.scalars(
        select(UserPasskey).where(UserPasskey.user_id == user.id)
    ).all()
    challenge = _challenge_merken(session, REGISTRIERUNG, user.id)
    optionen = generate_registration_options(
        rp_id=settings.passkey_rp_id or "",
        rp_name=settings.passkey_rp_name,
        # Die Kennung des Benutzers, nicht sein Name: Ein umbenanntes Konto behaelt seine
        # Passkeys, und der Authenticator speichert nichts, was sich aendern kann.
        user_id=str(user.id).encode(),
        user_name=user.username,
        user_display_name=user.display_name,
        challenge=challenge,
        # `exclude_credentials` verhindert, dass derselbe Authenticator ein zweites Mal
        # fuer dasselbe Konto registriert wird — sonst haette man zwei Eintraege, von
        # denen einer nie benutzt wird und niemand weiss, welcher.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
            for p in vorhandene
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            # `required`: Der Schluessel muss im Authenticator selbst liegen. Nur dann
            # kann sich jemand anmelden, ohne vorher seinen Benutzernamen zu nennen —
            # und genau das ist der Gewinn gegenueber einem Passwort.
            resident_key=ResidentKeyRequirement.REQUIRED,
            # `required`: PIN oder Fingerabdruck sind Pflicht. Ohne das waere ein
            # gestohlener Sicherheitsschluessel ein vollstaendiger Zugang.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    result: dict[str, Any] = json.loads(options_to_json(optionen))
    return result


def finish_registration(
    session: Session,
    settings: Settings,
    user: User,
    response: dict[str, Any],
    bezeichnung: str,
) -> UserPasskey:
    """Prueft die Antwort des Authenticators und legt den Passkey ab."""
    clientdaten = _clientdaten(response)
    challenge = _challenge_einloesen(session, REGISTRIERUNG, clientdaten)

    try:
        checked = verify_registration_response(
            credential=response,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id or "",
            expected_origin=settings.passkey_erlaubte_origin(),
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError(f"Registrierung nicht bestanden: {exc}") from exc

    identifier = bytes_to_base64url(checked.credential_id)
    if session.scalar(
        select(UserPasskey).where(UserPasskey.credential_id == identifier)
    ) is not None:
        raise PasskeyError("Dieser Passkey ist bereits hinterlegt.")

    entry = UserPasskey(
        user_id=user.id,
        credential_id=identifier,
        public_key=bytes_to_base64url(checked.credential_public_key),
        sign_count=checked.sign_count,
        bezeichnung=(bezeichnung.strip() or "Passkey")[:120],
    )
    session.add(entry)
    session.flush()
    audit.record(
        session, source="web", action="passkey.registered", object_type="user_passkey",
        object_id=str(entry.id), summary=f"Passkey '{entry.bezeichnung}' hinterlegt",
        user_id=user.id,
    )
    return entry


def begin_authentication(session: Session, settings: Settings) -> dict[str, Any]:
    """Die Argumente fuer `navigator.credentials.get()`.

    **Ohne `allow_credentials`.** Eine Liste erlaubter Schluessel muesste vorher wissen,
    wer sich anmeldet — und wuerde damit verraten, ob es ein Konto gibt und wie viele
    Passkeys es hat. Der Authenticator nennt das Konto selbst (Discoverable Credential).
    """
    challenge = _challenge_merken(session, LOGIN)
    optionen = generate_authentication_options(
        rp_id=settings.passkey_rp_id or "",
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    result: dict[str, Any] = json.loads(options_to_json(optionen))
    return result


def verify_authentication(
    session: Session, settings: Settings, response: dict[str, Any]
) -> User:
    """Prueft die Assertion und liefert den angemeldeten Benutzer.

    Jeder Fehlschlag wirft denselben `PasskeyFehler`; der Grund steht im Audit-Protokoll.
    """
    clientdaten = _clientdaten(response)
    challenge = _challenge_einloesen(session, LOGIN, clientdaten)

    identifier = response.get("id") or response.get("rawId")
    if not isinstance(identifier, str):
        raise PasskeyError("Die Antwort nennt keine Credential-ID.")

    passkey = session.scalar(
        select(UserPasskey).where(UserPasskey.credential_id == identifier)
    )
    if passkey is None:
        _protokoll(session, None, "Unbekannter Passkey")
        raise PasskeyError("Unbekannter Passkey.")

    try:
        checked = verify_authentication_response(
            credential=response,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id or "",
            expected_origin=settings.passkey_erlaubte_origin(),
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        _protokoll(session, passkey.user_id, f"Signatur nicht bestanden: {exc}")
        raise PasskeyError("Signatur nicht bestanden.") from exc

    # Der Zaehler steht erst hier zur Verfuegung. Ein Authenticator, der ihn gar nicht
    # fuehrt, meldet dauerhaft 0 — das ist erlaubt und kein Klonhinweis.
    if checked.new_sign_count and checked.new_sign_count <= passkey.sign_count:
        _protokoll(
            session, passkey.user_id,
            f"Zaehler zurueckgefallen ({checked.new_sign_count} <= {passkey.sign_count})",
        )
        raise PasskeyError("Der Zaehler des Authenticators ist zurueckgefallen.")

    # Die Sperre wird NACH der Signaturpruefung ausgewertet — dieselbe Reihenfolge wie im
    # Passwortweg, damit sich am Verhalten nicht ablesen laesst, welche Konten es gibt.
    user = session.get(User, passkey.user_id)
    if user is None or not user.is_active:
        _protokoll(session, passkey.user_id, "Konto gesperrt oder geloescht")
        raise PasskeyError("Konto nicht nutzbar.")

    passkey.sign_count = checked.new_sign_count
    passkey.last_used_at = utcnow()
    session.flush()
    audit.record(
        session, source="web", action="login", object_type="user",
        object_id=str(user.id),
        summary=f"Anmeldung als '{user.username}' per Passkey",
        user_id=user.id,
    )
    return user


def remove_passkey(
    session: Session, user: User, passkey: UserPasskey
) -> None:
    """Entfernt einen Passkey des eigenen Kontos."""
    if passkey.user_id != user.id:
        raise PasskeyError("Dieser Passkey gehoert einem anderen Konto.")
    bezeichnung = passkey.bezeichnung
    session.delete(passkey)
    session.flush()
    audit.record(
        session, source="web", action="passkey.removed", object_type="user_passkey",
        object_id=str(passkey.id), summary=f"Passkey '{bezeichnung}' entfernt",
        user_id=user.id,
    )


def _clientdaten(response: dict[str, Any]) -> dict[str, Any]:
    """Liest `clientDataJSON` aus der Antwort — nur, um die Challenge zu finden."""
    roh = (response.get("response") or {}).get("clientDataJSON")
    if not isinstance(roh, str):
        raise PasskeyError("Die Antwort enthaelt keine clientDataJSON.")
    try:
        daten = json.loads(base64url_to_bytes(roh))
    except Exception as exc:
        raise PasskeyError("clientDataJSON ist nicht lesbar.") from exc
    if not isinstance(daten, dict):
        raise PasskeyError("clientDataJSON ist kein Objekt.")
    return daten


def _protokoll(session: Session, user_id: int | None, grund: str) -> None:
    audit.record(
        session, source="web", action="login_failed", object_type="user",
        object_id=None if user_id is None else str(user_id),
        summary="Passkey-Anmeldung fehlgeschlagen", detail=grund, user_id=user_id,
    )
