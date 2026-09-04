"""The two WebAuthn ceremonies: registering a passkey and logging in with one.

The rules live here and not in the adapter, so that they apply to every path. Five of
them are the actual substance of this module — without them, WebAuthn is just a
cumbersome password:

1. **The challenge is held exclusively by the service.** Whoever can set their own
   can resubmit an old response.
2. **It is consumed in every case**, even if verification fails. A reusable challenge
   removes the very protection it is meant to provide.
3. **It is bound to its ceremony.** A challenge issued for login must not be
   submittable for a registration.
4. **Relying-party ID and origin come from configuration**, never from the request.
   The `Host` header is set by the caller; a relying-party ID under their control
   would make the check worthless.
5. **A counter that has gone backward ends the login.** Authenticators count their
   uses upward. If one counts backward, the key exists twice — that is the only
   sign of a cloned authenticator this procedure knows of at all.

From the outside, every failed login looks the same. Whether a credential id is
unknown, an account is locked, or a signature is wrong is recorded in the audit log and
nowhere else; otherwise the responses would reveal which accounts exist.
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

# A challenge is valid for two minutes. Long enough to find and tap an authenticator;
# short enough that an intercepted one is worthless by the time anyone could use it.
CHALLENGE_GUELTIG = timedelta(minutes=2)


class PasskeyError(Exception):
    """The ceremony has failed. The reason belongs in the log, not out to the caller."""


def _remember_challenge(
    session: Session, ceremony: str, user_id: int | None = None
) -> bytes:
    """Generates a challenge, stores it, and returns it."""
    raw_entry = secrets.token_bytes(32)
    session.add(
        PasskeyChallenge(
            challenge=bytes_to_base64url(raw_entry), ceremony=ceremony, user_id=user_id
        )
    )
    session.flush()
    return raw_entry


def _redeem_challenge(
    session: Session, ceremony: str, client_data: dict[str, Any]
) -> bytes:
    """Takes the challenge out of the response, checks it, and **deletes it in every case**.

    The value comes from the authenticator's `clientDataJSON`. It is not trusted here,
    only used to find the stored row — the signature check afterward compares it
    itself against what we issued.
    """
    candidate = client_data.get("challenge")
    if not isinstance(candidate, str):
        raise PasskeyError("Die Antwort enthält keine Challenge.")

    entry = session.scalar(
        select(PasskeyChallenge).where(PasskeyChallenge.challenge == candidate)
    )
    if entry is None:
        raise PasskeyError("Unbekannte oder bereits verbrauchte Challenge.")

    # Delete first, judge after: even an expired or misused challenge is consumed by
    # then. Otherwise it could be resubmitted an arbitrary number of times.
    ceremony_of_the_row = entry.ceremony
    age = utcnow() - entry.created_at
    session.delete(entry)
    session.flush()

    if ceremony_of_the_row != ceremony:
        raise PasskeyError(
            f"Challenge war für '{ceremony_of_the_row}' ausgegeben, nicht für "
            f"'{ceremony}'."
        )
    if age > CHALLENGE_GUELTIG:
        raise PasskeyError("Challenge ist abgelaufen.")
    return base64url_to_bytes(candidate)


def cleanup_old_challenges(session: Session) -> int:
    """Removes expired challenges. They are worthless, but they pile up."""
    limit = utcnow() - CHALLENGE_GUELTIG
    result = session.execute(
        delete(PasskeyChallenge).where(PasskeyChallenge.created_at < limit)
    )
    # `rowcount` only exists on CursorResult, not on the general Result type; for a
    # DELETE it is always a CursorResult.
    return int(result.rowcount)  # type: ignore[attr-defined]


def begin_registration(
    session: Session, settings: Settings, user: User
) -> dict[str, Any]:
    """The arguments for `navigator.credentials.create()`."""
    existing = session.scalars(
        select(UserPasskey).where(UserPasskey.user_id == user.id)
    ).all()
    challenge = _remember_challenge(session, REGISTRIERUNG, user.id)
    options = generate_registration_options(
        rp_id=settings.passkey_rp_id or "",
        rp_name=settings.passkey_rp_name,
        # The user's id, not their name: a renamed account keeps its passkeys, and the
        # authenticator stores nothing that can change.
        user_id=str(user.id).encode(),
        user_name=user.username,
        user_display_name=user.display_name,
        challenge=challenge,
        # `exclude_credentials` prevents the same authenticator from being registered
        # a second time for the same account -- otherwise there would be two entries,
        # one of which is never used and nobody knows which.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(p.credential_id))
            for p in existing
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            # `required`: the key must live in the authenticator itself. Only then can
            # someone log in without naming their username first -- and that is
            # exactly the gain over a password.
            resident_key=ResidentKeyRequirement.REQUIRED,
            # `required`: PIN or fingerprint are mandatory. Without this, a stolen
            # security key would be complete access.
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    result: dict[str, Any] = json.loads(options_to_json(options))
    return result


def finish_registration(
    session: Session,
    settings: Settings,
    user: User,
    response: dict[str, Any],
    label: str,
) -> UserPasskey:
    """Verifies the authenticator's response and stores the passkey."""
    client_data = _client_data(response)
    challenge = _redeem_challenge(session, REGISTRIERUNG, client_data)

    try:
        checked = verify_registration_response(
            credential=response,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id or "",
            expected_origin=settings.passkey_allowed_origin(),
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
        label=(label.strip() or "Passkey")[:120],
    )
    session.add(entry)
    session.flush()
    audit.record(
        session, source="web", action="passkey.registered", object_type="user_passkey",
        object_id=str(entry.id), summary=f"Passkey '{entry.label}' hinterlegt",
        user_id=user.id,
    )
    return entry


def begin_authentication(session: Session, settings: Settings) -> dict[str, Any]:
    """The arguments for `navigator.credentials.get()`.

    **Without `allow_credentials`.** A list of allowed keys would first have to know
    who is logging in -- and would thereby reveal whether an account exists and how
    many passkeys it has. The authenticator names the account itself (discoverable
    credential).
    """
    challenge = _remember_challenge(session, LOGIN)
    options = generate_authentication_options(
        rp_id=settings.passkey_rp_id or "",
        challenge=challenge,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    result: dict[str, Any] = json.loads(options_to_json(options))
    return result


def verify_authentication(
    session: Session, settings: Settings, response: dict[str, Any]
) -> User:
    """Verifies the assertion and returns the logged-in user.

    Every failure raises the same `PasskeyFehler`; the reason is recorded in the audit
    log.
    """
    client_data = _client_data(response)
    challenge = _redeem_challenge(session, LOGIN, client_data)

    identifier = response.get("id") or response.get("rawId")
    if not isinstance(identifier, str):
        raise PasskeyError("Die Antwort nennt keine Credential-ID.")

    passkey = session.scalar(
        select(UserPasskey).where(UserPasskey.credential_id == identifier)
    )
    if passkey is None:
        _log(session, None, "Unbekannter Passkey")
        raise PasskeyError("Unbekannter Passkey.")

    try:
        checked = verify_authentication_response(
            credential=response,
            expected_challenge=challenge,
            expected_rp_id=settings.passkey_rp_id or "",
            expected_origin=settings.passkey_allowed_origin(),
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        _log(session, passkey.user_id, f"Signatur nicht bestanden: {exc}")
        raise PasskeyError("Signatur nicht bestanden.") from exc

    # The counter only becomes available here. An authenticator that does not track
    # it at all permanently reports 0 -- that is allowed and not a sign of cloning.
    if checked.new_sign_count and checked.new_sign_count <= passkey.sign_count:
        _log(
            session, passkey.user_id,
            f"Zähler zurückgefallen ({checked.new_sign_count} <= {passkey.sign_count})",
        )
        raise PasskeyError("Der Zähler des Authenticators ist zurückgefallen.")

    # The lock is evaluated AFTER the signature check -- the same ordering as in the
    # password path, so that behavior cannot reveal which accounts exist.
    user = session.get(User, passkey.user_id)
    if user is None or not user.is_active:
        _log(session, passkey.user_id, "Konto gesperrt oder gelöscht")
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
    """Removes a passkey belonging to the caller's own account."""
    if passkey.user_id != user.id:
        raise PasskeyError("Dieser Passkey gehört einem anderen Konto.")
    label = passkey.label
    session.delete(passkey)
    session.flush()
    audit.record(
        session, source="web", action="passkey.removed", object_type="user_passkey",
        object_id=str(passkey.id), summary=f"Passkey '{label}' entfernt",
        user_id=user.id,
    )


def _client_data(response: dict[str, Any]) -> dict[str, Any]:
    """Reads `clientDataJSON` from the response -- only to find the challenge."""
    raw_entry = (response.get("response") or {}).get("clientDataJSON")
    if not isinstance(raw_entry, str):
        raise PasskeyError("Die Antwort enthält keine clientDataJSON.")
    try:
        data = json.loads(base64url_to_bytes(raw_entry))
    except Exception as exc:
        raise PasskeyError("clientDataJSON ist nicht lesbar.") from exc
    if not isinstance(data, dict):
        raise PasskeyError("clientDataJSON ist kein Objekt.")
    return data


def _log(session: Session, user_id: int | None, reason: str) -> None:
    audit.record(
        session, source="web", action="login_failed", object_type="user",
        object_id=None if user_id is None else str(user_id),
        summary="Passkey-Anmeldung fehlgeschlagen", detail=reason, user_id=user_id,
    )
