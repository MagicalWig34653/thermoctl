"""Aenderungsprotokoll: Bestaetigungsmarke fuer das Erzwingen eines Rueckgaengigmachens."""
import hmac
from hashlib import sha256

from fastapi import Request

from thermoctl.auth.sessions import COOKIE_NAME
from thermoctl.config import get_settings


def confirmation_token(request: Request, change_id: int) -> str:
    secret = get_settings().secret_key.get_secret_value().encode()
    cookie = request.cookies.get(COOKIE_NAME, "")
    return hmac.new(secret, f"force:{cookie}:{change_id}".encode(), sha256).hexdigest()
