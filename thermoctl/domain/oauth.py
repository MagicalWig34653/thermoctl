"""OAuth-2.1-Kern für den MCP-Zugang: Clients, Anfragen, Codes, Refresh-Token.

thermoctl ist hier selbst der Authorization Server. Dieses Modul kennt kein HTTP: Es nimmt
geprüfte Werte entgegen, schreibt Zeilen und wirft `OAuthError`, dessen `error` dem
Fehlercode aus RFC 6749/7591/8707 entspricht. Die Adapter (`thermoctl/mcp/oauth_routes.py`,
`thermoctl/web/oauth_views.py`) übersetzen das nur.

Grundsatz 7 -- das hier entscheidet, wer die Heizung bedienen darf. Daraus folgt:

* Codes, Anfrage-Ids und Refresh-Token liegen nur als SHA-256 in der Datenbank
  (`auth.secrets.hash_secret`), nie im Klartext und nie im Protokoll.
* Ein OAuth-Access-Token ist eine gewöhnliche `ApiToken`-Zeile mit `source="oauth"`; die
  Rechteprüfung bleibt `principal_for_token` (Schnitt aus Tokenrechten und aktuellen Rechten
  des Nutzers, zur Laufzeit).
* **Wer einen Fehler wirft, hat vorher geschrieben, was bestehen bleiben muss** (ein
  wiederverwendeter Code widerruft, was er ausgestellt hat). Der Aufrufer muss deshalb bei
  `OAuthError` die Sitzung **committen**, nicht zurückrollen -- sonst geht genau diese
  Gegenmaßnahme verloren.
"""

import base64
import hashlib
import hmac
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from thermoctl.auth.secrets import hash_secret, new_secret
from thermoctl.auth.tokens import issue_token, revoke_token
from thermoctl.db.base import utcnow
from thermoctl.db.models.credential import ApiToken, ApiTokenPermission
from thermoctl.db.models.identity import User
from thermoctl.db.models.lookup import Permission
from thermoctl.db.models.oauth import (
    OAuthAuthorizationCode,
    OAuthAuthorizationRequest,
    OAuthClient,
    OAuthCodePermission,
    OAuthRedirectUri,
    OAuthRefreshToken,
)
from thermoctl.domain.authz import Forbidden, has_permission, principal_for_user

log = logging.getLogger("thermoctl.oauth")

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(minutes=5)
REQUEST_TTL = timedelta(minutes=10)

MAX_REDIRECT_URIS = 10
MAX_URI_LENGTH = 512  # Unique-Index (client_id, uri): 512 Zeichen = 2048 Byte unter utf8mb4
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
# Schemata, die als "App-Schema" für eine Umleitung nie in Frage kommen.
_FORBIDDEN_SCHEMES = frozenset(
    {"javascript", "data", "vbscript", "file", "about", "blob", "ftp", "http", "https"}
)
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_\-]{43,128}$")
_VERIFIER = re.compile(r"^[A-Za-z0-9._~\-]{43,128}$")


class OAuthError(Exception):
    """Ein OAuth-Fehler mit dem Code aus der jeweiligen RFC."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(f"{error}: {description}")
        self.error = error
        self.description = description


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _log(event: str, outcome: str, **extra: object) -> None:
    """Strukturierter Eintrag. Schlüssel `key_prefix` statt `token_*`: Die Maskierung in
    `thermoctl/logging.py` blendet jeden Schlüssel mit dem Segment "token" aus."""
    log.info(event, extra={"event": event, "outcome": outcome, **extra})


# --- Prüfung von Eingaben --------------------------------------------------------


def _check_redirect_uri(uri: str) -> None:
    def bad(reason: str) -> OAuthError:
        return OAuthError("invalid_redirect_uri", reason)

    if not uri or len(uri) > MAX_URI_LENGTH:
        raise bad("Redirect-URI fehlt oder ist zu lang")
    if any(ord(char) < 33 or ord(char) == 127 or char == "\\" for char in uri):
        # Der Backslash gehört dazu: Parser behandeln ihn verschieden (`urlsplit` liest in
        # `http://evil.example\@localhost/` den Host "localhost", ein Browser oder Node
        # den Host "evil.example"). Eine geprüfte und eine tatsächlich angesteuerte
        # Adresse dürfen nicht auseinanderfallen.
        raise bad("Redirect-URI enthält Leer-, Steuer- oder Rückstrichzeichen")
    try:
        parts = urlsplit(uri)
        host = parts.hostname
    except ValueError as exc:
        raise bad("Redirect-URI ist keine gültige URL") from exc
    if parts.fragment or "#" in uri:
        raise bad("Redirect-URI darf kein Fragment enthalten")
    if parts.username is not None or parts.password is not None:
        raise bad("Redirect-URI darf keine Zugangsdaten enthalten")
    scheme = parts.scheme
    if scheme == "https":
        if not host:
            raise bad("Redirect-URI braucht einen Host")
    elif scheme == "http":
        if host not in _LOOPBACK:
            raise bad("http ist nur auf localhost oder Loopback erlaubt")
    elif not _SCHEME.match(scheme) or scheme in _FORBIDDEN_SCHEMES or parts.netloc:
        raise bad("Redirect-URI hat kein zulässiges Schema")


def _check_challenge(challenge: str) -> None:
    if not _CHALLENGE.match(challenge):
        raise OAuthError(
            "invalid_request", "code_challenge muss ein S256-Wert (43-128 Zeichen) sein"
        )


def _expected_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --- Clients ---------------------------------------------------------------------


def register_client(session: Session, name: str, redirect_uris: list[str]) -> OAuthClient:
    """Legt einen öffentlichen Client an (Dynamic Client Registration). Gewährt keine Rechte."""
    name = name.strip()
    if not name or len(name) > 128:
        raise OAuthError("invalid_client_metadata", "client_name fehlt oder ist zu lang")
    if not 1 <= len(redirect_uris) <= MAX_REDIRECT_URIS:
        raise OAuthError(
            "invalid_redirect_uri", f"1 bis {MAX_REDIRECT_URIS} Redirect-URIs sind erlaubt"
        )
    for uri in redirect_uris:
        _check_redirect_uri(uri)
    client = OAuthClient(client_id=secrets.token_urlsafe(24), name=name)
    session.add(client)
    session.flush()
    for uri in dict.fromkeys(redirect_uris):
        session.add(OAuthRedirectUri(client_id=client.id, uri=uri))
    session.flush()
    _log("oauth.register", "ok", client_id=client.client_id)
    return client


def client_redirect_uris(session: Session, client: OAuthClient) -> list[str]:
    return list(
        session.scalars(
            select(OAuthRedirectUri.uri)
            .where(OAuthRedirectUri.client_id == client.id)
            .order_by(OAuthRedirectUri.id)
        )
    )


def find_client(session: Session, client_id: str) -> OAuthClient | None:
    return session.scalar(select(OAuthClient).where(OAuthClient.client_id == client_id))


# --- Anfrage und Zustimmung ------------------------------------------------------


def start_request(
    session: Session,
    client: OAuthClient,
    redirect_uri: str,
    code_challenge: str,
    resource: str,
    state: str | None,
    expected_resource: str,
) -> str:
    """Merkt sich eine noch unbestätigte Anfrage. Liefert die Anfrage-Id (Klartext, einmalig)."""
    if redirect_uri not in client_redirect_uris(session, client):
        raise OAuthError("invalid_request", "redirect_uri ist für diesen Client nicht registriert")
    _check_challenge(code_challenge)
    if resource != expected_resource:
        raise OAuthError("invalid_target", "resource ist nicht der MCP-Server dieser Anlage")
    if state is not None and len(state) > 512:
        raise OAuthError("invalid_request", "state ist zu lang")
    request_id = new_secret()
    session.add(
        OAuthAuthorizationRequest(
            request_hash=hash_secret(request_id),
            client_id=client.id,
            redirect_uri=redirect_uri,
            state=state,
            code_challenge=code_challenge,
            resource=resource,
            expires_at=utcnow() + REQUEST_TTL,
        )
    )
    session.flush()
    _log("oauth.authorize", "pending", client_id=client.client_id)
    return request_id


def load_request(session: Session, request_id: str) -> OAuthAuthorizationRequest | None:
    request = session.scalar(
        select(OAuthAuthorizationRequest).where(
            OAuthAuthorizationRequest.request_hash == hash_secret(request_id)
        )
    )
    if request is None or request.consumed_at is not None or request.expires_at <= utcnow():
        return None
    return request


def _claim(session: Session, row: Any, column: str, *extra: Any) -> bool:
    """Beansprucht eine Zeile atomar: genau ein Aufrufer bekommt `True`.

    Ein Lesen-dann-Schreiben (`if row.consumed_at is None: row.consumed_at = jetzt`) lässt zwei
    gleichzeitige Anfragen beide durch -- beide lesen "unverbraucht". Der bedingte `UPDATE ...
    WHERE spalte IS NULL` entscheidet in der Datenbank; wer 0 Zeilen trifft, hat verloren.
    `extra` sind weitere Bedingungen (z. B. "nicht ersetzt").
    """
    result = session.execute(
        update(type(row))
        .where(type(row).id == row.id, getattr(type(row), column).is_(None), *extra)
        .values({column: utcnow()})
        .execution_options(synchronize_session=False)
    )
    session.refresh(row)
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


def _open(request: OAuthAuthorizationRequest) -> None:
    if request.consumed_at is not None or request.expires_at <= utcnow():
        raise OAuthError("invalid_request", "Die Anfrage ist abgelaufen oder schon verbraucht")


def approve(
    session: Session,
    request: OAuthAuthorizationRequest,
    user: User,
    permissions: list[tuple[str, int | None]],
) -> str:
    """Bestätigt die Anfrage für `user` mit genau diesen Rechten. Liefert den Code (Klartext)."""
    _open(request)
    principal = principal_for_user(session, user)
    unique = list(dict.fromkeys(permissions))
    for code, zone_id in unique:
        if not has_permission(principal, code, zone_id):
            raise Forbidden(f"{user.username} kann das Recht {code} nicht weitergeben")
    ids = {c: i for c, i in session.execute(select(Permission.code, Permission.id)).all()}
    if not _claim(session, request, "consumed_at"):
        raise OAuthError("invalid_request", "Die Anfrage ist abgelaufen oder schon verbraucht")
    plaintext = new_secret()
    row = OAuthAuthorizationCode(
        code_hash=hash_secret(plaintext),
        client_id=request.client_id,
        user_id=user.id,
        redirect_uri=request.redirect_uri,
        code_challenge=request.code_challenge,
        resource=request.resource,
        expires_at=utcnow() + CODE_TTL,
    )
    session.add(row)
    session.flush()
    for code, zone_id in unique:
        session.add(OAuthCodePermission(code_id=row.id, permission_id=ids[code], zone_id=zone_id))
    session.flush()
    client = session.get(OAuthClient, request.client_id)
    _log("oauth.approve", "ok", client_id=client.client_id if client else None, user_id=user.id)
    return plaintext


def deny(session: Session, request: OAuthAuthorizationRequest) -> None:
    request.consumed_at = utcnow()
    session.flush()
    _log("oauth.deny", "denied")


# --- Austausch und Erneuerung ----------------------------------------------------


def _family(code_hash: str) -> str:
    return code_hash[:32]


def _revoke_family(session: Session, family: str) -> None:
    now = utcnow()
    for row in session.scalars(select(OAuthRefreshToken).where(OAuthRefreshToken.family == family)):
        if row.revoked_at is None:
            row.revoked_at = now
        token = session.get(ApiToken, row.api_token_id) if row.api_token_id else None
        if token is not None and token.revoked_at is None:
            revoke_token(session, token)
    session.flush()


def _issue_pair(
    session: Session,
    client: OAuthClient,
    user: User,
    permissions: list[tuple[str, int | None]],
    family: str,
) -> tuple[TokenPair, OAuthRefreshToken]:
    token, access = issue_token(
        session, user, f"OAuth: {client.name}"[:128], permissions, utcnow() + ACCESS_TTL
    )
    token.source = "oauth"
    token.oauth_client_id = client.id
    refresh_plain = new_secret()
    row = OAuthRefreshToken(
        token_hash=hash_secret(refresh_plain),
        family=family,
        client_id=client.id,
        user_id=user.id,
        api_token_id=token.id,
        expires_at=utcnow() + REFRESH_TTL,
    )
    session.add(row)
    session.flush()
    client.last_used_at = utcnow()
    return TokenPair(access, refresh_plain, int(ACCESS_TTL.total_seconds())), row


def _fail(event: str, reason: str, error: str = "invalid_grant", **extra: object) -> OAuthError:
    _log(event, "denied", reason=reason, **extra)
    return OAuthError(error, reason)


def exchange_code(
    session: Session,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    resource: str | None,
    expected_resource: str,
) -> TokenPair:
    """Tauscht einen Code gegen Access- und Refresh-Token. Ein Code gilt genau einmal."""
    event = "oauth.token.code"
    row = session.scalar(
        select(OAuthAuthorizationCode).where(OAuthAuthorizationCode.code_hash == hash_secret(code))
    )
    client = find_client(session, client_id)
    if row is None or client is None or row.client_id != client.id:
        raise _fail(event, "Code unbekannt oder gehört einem anderen Client", client_id=client_id)
    if row.consumed_at is not None:
        # Wiederverwendung: wer den Code ein zweites Mal vorlegt, hat ihn vermutlich
        # abgefangen. Alles, was aus ihm entstand, wird widerrufen.
        _revoke_family(session, _family(row.code_hash))
        raise _fail(event, "Code wurde schon eingelöst", client_id=client_id)
    # Auch bei Fehlschlag verbraucht (kein Raten am selben Code). Verliert dieser Aufruf
    # die Beanspruchung, hat ein zweiter denselben Code gleichzeitig vorgelegt: wie jede
    # Wiederverwendung behandeln.
    if not _claim(session, row, "consumed_at"):
        _revoke_family(session, _family(row.code_hash))
        raise _fail(event, "Code wurde schon eingelöst", client_id=client_id)
    if row.expires_at <= utcnow():
        raise _fail(event, "Code ist abgelaufen", client_id=client_id)
    if redirect_uri != row.redirect_uri:
        raise _fail(event, "redirect_uri weicht von der Anfrage ab", client_id=client_id)
    if resource is not None and resource != row.resource:
        raise _fail(
            event, "resource weicht von der Anfrage ab", "invalid_target", client_id=client_id
        )
    if row.resource != expected_resource:
        raise _fail(
            event,
            "resource ist nicht mehr dieser MCP-Server",
            "invalid_target",
            client_id=client_id,
        )
    if not _VERIFIER.match(code_verifier) or not hmac.compare_digest(
        _expected_challenge(code_verifier), row.code_challenge
    ):
        raise _fail(event, "PKCE-Prüfung fehlgeschlagen", client_id=client_id)
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise _fail(event, "Nutzer nicht mehr aktiv", client_id=client_id)
    permissions = [
        (c, z)
        for c, z in session.execute(
            select(Permission.code, OAuthCodePermission.zone_id)
            .join(OAuthCodePermission, OAuthCodePermission.permission_id == Permission.id)
            .where(OAuthCodePermission.code_id == row.id)
        ).all()
    ]
    pair, _refresh = _issue_pair(session, client, user, permissions, _family(row.code_hash))
    _log(
        "oauth.token.code",
        "ok",
        client_id=client_id,
        user_id=user.id,
        key_prefix=_prefix(session, _refresh),
    )
    return pair


def _prefix(session: Session, refresh: OAuthRefreshToken) -> str | None:
    token = session.get(ApiToken, refresh.api_token_id) if refresh.api_token_id else None
    return token.prefix if token else None


def refresh(
    session: Session,
    client_id: str,
    refresh_token: str,
    resource: str | None,
    expected_resource: str,
) -> TokenPair:
    """Erneuert das Token. Das Refresh-Token rotiert; seine Wiederverwendung sperrt die Familie."""
    event = "oauth.token.refresh"
    row = session.scalar(
        select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == hash_secret(refresh_token))
    )
    client = find_client(session, client_id)
    if row is None or client is None or row.client_id != client.id:
        raise _fail(
            event, "Refresh-Token unbekannt oder gehört einem anderen Client", client_id=client_id
        )
    if row.revoked_at is not None or row.replaced_by_id is not None:
        _revoke_family(session, row.family)
        raise _fail(event, "Refresh-Token wurde schon benutzt oder widerrufen", client_id=client_id)
    if row.expires_at <= utcnow():
        raise _fail(event, "Refresh-Token ist abgelaufen", client_id=client_id)
    if resource is not None and resource != expected_resource:
        raise _fail(
            event, "resource ist nicht dieser MCP-Server", "invalid_target", client_id=client_id
        )
    user = session.get(User, row.user_id)
    old = session.get(ApiToken, row.api_token_id) if row.api_token_id else None
    if old is not None and old.revoked_at is not None:
        # Das zugehörige Access-Token wurde von Hand widerrufen (Token-Verwaltung, /revoke):
        # Das Refresh-Token darf es nicht wieder aufleben lassen.
        _revoke_family(session, row.family)
        raise _fail(event, "Das Token dieser Sitzung wurde widerrufen", client_id=client_id)
    if user is None or not user.is_active or old is None:
        raise _fail(
            event, "Nutzer oder ursprüngliches Token nicht mehr vorhanden", client_id=client_id
        )
    granted = session.execute(
        select(Permission.code, ApiTokenPermission.zone_id)
        .join(ApiTokenPermission, ApiTokenPermission.permission_id == Permission.id)
        .where(ApiTokenPermission.api_token_id == old.id)
    ).all()
    principal = principal_for_user(session, user)
    kept = [(c, z) for c, z in granted if has_permission(principal, c, z)]
    if not kept:
        _revoke_family(session, row.family)
        raise _fail(
            event, "Dem Nutzer ist keines der vergebenen Rechte geblieben", client_id=client_id
        )
    # Rotation atomar beanspruchen (siehe `_claim`): Zwei gleichzeitige Erneuerungen mit
    # demselben Token dürfen nicht beide ein neues Paar bekommen.
    if not _claim(session, row, "revoked_at", OAuthRefreshToken.replaced_by_id.is_(None)):
        _revoke_family(session, row.family)
        raise _fail(event, "Refresh-Token wurde schon benutzt oder widerrufen", client_id=client_id)
    pair, new_row = _issue_pair(session, client, user, kept, row.family)
    row.replaced_by_id = new_row.id
    if old.revoked_at is None:
        revoke_token(session, old)
    session.flush()
    _log(event, "ok", client_id=client_id, user_id=user.id, key_prefix=_prefix(session, new_row))
    return pair


def revoke(session: Session, token_plaintext: str) -> None:
    """RFC 7009: widerruft ein Refresh-Token (samt Familie) oder ein OAuth-Access-Token.

    Unbekannte Werte und Bearer-Token bleiben unberührt und ohne Fehler -- der Aufrufer
    antwortet immer gleich, damit sich nichts über gültige Token erkennen lässt.
    """
    row = session.scalar(
        select(OAuthRefreshToken).where(
            OAuthRefreshToken.token_hash == hash_secret(token_plaintext)
        )
    )
    if row is not None:
        _revoke_family(session, row.family)
        _log("oauth.revoke", "ok", reason="refresh")
        return
    parts = token_plaintext.split("_", 2)
    if len(parts) == 3 and parts[0] == "tctl":
        token = session.scalar(
            select(ApiToken).where(
                ApiToken.token_hash == hash_secret(parts[2]), ApiToken.source == "oauth"
            )
        )
        if token is not None:
            revoke_access_token(session, token)


def revoke_access_token(session: Session, token: ApiToken) -> None:
    """Widerruft ein OAuth-Access-Token anhand seiner Zeile (Token-Verwaltung).

    Sperrt auch die Refresh-Familie derselben Sitzung, sonst ließe sich das widerrufene
    Token mit einer Erneuerung sofort ersetzen. Ein Bearer-Token ist hier kein Ziel.
    """
    if token.source != "oauth":
        raise ValueError("Nur OAuth-Token werden hier widerrufen")
    for family in session.scalars(
        select(OAuthRefreshToken.family).where(OAuthRefreshToken.api_token_id == token.id)
    ).all():
        _revoke_family(session, family)
    if token.revoked_at is None:
        revoke_token(session, token)
    session.flush()
    _log("oauth.revoke", "ok", reason="access", key_prefix=token.prefix)
