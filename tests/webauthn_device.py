"""A software authenticator for the tests -- about seventy lines instead of a dependency.

Why build it ourselves: the obvious package `soft-webauthn` depends on `fido2`, which
requires `cryptography < 45`, while `webauthn 3.0` needs a newer one. Rather than pin
the service's library to a test tool, this module builds the handful of structures
itself. It uses only what is already there: `cryptography` and `cbor2`.

And it is not a stub substitute: it really signs with a P-256 key. A test using it
proves that a **correct** response is accepted -- without that, the suite could only
show that everything is rejected, and would stay green even if login had not been
built at all.
"""

import hashlib
import json
import os
import struct
from typing import Any

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url


# Not `TestAuthenticator`: pytest collects every class starting with "Test"
# and then complains about the constructor.
class WebAuthnDevice:
    """Behaves like a security key, just without the hardware."""

    def __init__(self) -> None:
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0

    # -- shared building blocks -------------------------------------------------------

    def _client_data(self, type_: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": type_, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def _cose_key(self) -> bytes:
        numbers = self._private_key.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,     # kty: EC2
            3: -7,    # alg: ES256
            -1: 1,    # crv: P-256
            -2: numbers.x.to_bytes(32, "big"),
            -3: numbers.y.to_bytes(32, "big"),
        })

    def _authenticator_data(self, rp_id: str, *, with_credential: bool) -> bytes:
        # We always set the UP (user present) and UV (user verified) flags --
        # the service requires both. AT (attested credential data) only on registration.
        flags = 0x01 | 0x04 | (0x40 if with_credential else 0x00)
        data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags])
        data += struct.pack(">I", self.counter)
        if with_credential:
            data += bytes(16)  # AAGUID: none, as with a platform authenticator
            data += struct.pack(">H", len(self.credential_id)) + self.credential_id
            data += self._cose_key()
        return data

    # -- the two ceremonies ------------------------------------------------------

    def register(self, arguments: dict[str, Any], origin: str) -> dict[str, Any]:
        rp_id = arguments["rp"]["id"]
        client_data = self._client_data(
            "webauthn.create", arguments["challenge"], origin
        )
        auth_data = self._authenticator_data(rp_id, with_credential=True)
        attestation = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": auth_data}
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "attestationObject": bytes_to_base64url(attestation),
            },
        }

    def log_in(
        self, arguments: dict[str, Any], origin: str, *, counter: int | None = None
    ) -> dict[str, Any]:
        rp_id = arguments["rpId"]
        # A real authenticator counts up on every use. The parameter lets us
        # simulate a counter that has fallen behind -- the only clue to a
        # cloned key that the protocol knows.
        self.counter = self.counter + 1 if counter is None else counter
        client_data = self._client_data("webauthn.get", arguments["challenge"], origin)
        auth_data = self._authenticator_data(rp_id, with_credential=False)
        signature = self._private_key.sign(
            auth_data + hashlib.sha256(client_data).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(signature),
                "userHandle": None,
            },
        }


__all__ = ["WebAuthnDevice", "base64url_to_bytes", "bytes_to_base64url"]
