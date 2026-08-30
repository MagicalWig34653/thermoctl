"""Ein Software-Authenticator fuer die Tests — rund siebzig Zeilen statt einer Abhaengigkeit.

Warum selbst gebaut: Das naheliegende Paket `soft-webauthn` haengt an `fido2`, das
`cryptography < 45` verlangt, waehrend `webauthn 3.0` ein neueres braucht. Statt die
Bibliothek des Dienstes an ein Testwerkzeug zu binden, erzeugt dieses Modul die paar
Strukturen selbst. Es benutzt nur, was ohnehin da ist: `cryptography` und `cbor2`.

Und es ist kein Attrappen-Ersatz: Es signiert wirklich mit einem P-256-Schluessel. Ein
Test damit belegt, dass eine **richtige** Antwort angenommen wird — ohne das koennte die
Suite nur zeigen, dass alles abgelehnt wird, und waere auch dann gruen, wenn die Anmeldung
gar nicht gebaut waere.
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


# Nicht `TestAuthenticator`: pytest sammelt jede Klasse ein, die mit "Test" beginnt,
# und beschwert sich dann ueber den Konstruktor.
class WebAuthnDevice:
    """Verhaelt sich wie ein Sicherheitsschluessel, nur ohne Hardware."""

    def __init__(self) -> None:
        self._schluessel = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.counter = 0

    # -- gemeinsame Bausteine -------------------------------------------------------

    def _clientdaten(self, typ: str, challenge: str, origin: str) -> bytes:
        return json.dumps(
            {"type": typ, "challenge": challenge, "origin": origin, "crossOrigin": False},
            separators=(",", ":"),
        ).encode()

    def _cose_schluessel(self) -> bytes:
        zahlen = self._schluessel.public_key().public_numbers()
        return cbor2.dumps({
            1: 2,     # kty: EC2
            3: -7,    # alg: ES256
            -1: 1,    # crv: P-256
            -2: zahlen.x.to_bytes(32, "big"),
            -3: zahlen.y.to_bytes(32, "big"),
        })

    def _authenticator_daten(self, rp_id: str, *, mit_credential: bool) -> bytes:
        # Flags: UP (Benutzer anwesend) und UV (Benutzer verifiziert) setzen wir immer —
        # der Dienst verlangt beides. AT (attested credential data) nur beim Anlegen.
        flags = 0x01 | 0x04 | (0x40 if mit_credential else 0x00)
        daten = hashlib.sha256(rp_id.encode()).digest() + bytes([flags])
        daten += struct.pack(">I", self.counter)
        if mit_credential:
            daten += bytes(16)  # AAGUID: keiner, wie bei einem Plattform-Authenticator
            daten += struct.pack(">H", len(self.credential_id)) + self.credential_id
            daten += self._cose_schluessel()
        return daten

    # -- die beiden Zeremonien ------------------------------------------------------

    def registrieren(self, argumente: dict[str, Any], origin: str) -> dict[str, Any]:
        rp_id = argumente["rp"]["id"]
        clientdaten = self._clientdaten(
            "webauthn.create", argumente["challenge"], origin
        )
        authdaten = self._authenticator_daten(rp_id, mit_credential=True)
        attestation = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": authdaten}
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(clientdaten),
                "attestationObject": bytes_to_base64url(attestation),
            },
        }

    def log_in(
        self, argumente: dict[str, Any], origin: str, *, counter: int | None = None
    ) -> dict[str, Any]:
        rp_id = argumente["rpId"]
        # Ein echter Authenticator zaehlt bei jeder Benutzung hoch. Der Parameter erlaubt
        # es, einen zurueckgefallenen Zaehler nachzustellen — den einzigen Hinweis auf
        # einen geklonten Schluessel, den das Verfahren kennt.
        self.counter = self.counter + 1 if counter is None else counter
        clientdaten = self._clientdaten("webauthn.get", argumente["challenge"], origin)
        authdaten = self._authenticator_daten(rp_id, mit_credential=False)
        signatur = self._schluessel.sign(
            authdaten + hashlib.sha256(clientdaten).digest(),
            ec.ECDSA(hashes.SHA256()),
        )
        return {
            "id": bytes_to_base64url(self.credential_id),
            "rawId": bytes_to_base64url(self.credential_id),
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(clientdaten),
                "authenticatorData": bytes_to_base64url(authdaten),
                "signature": bytes_to_base64url(signatur),
                "userHandle": None,
            },
        }


__all__ = ["WebAuthnDevice", "base64url_to_bytes", "bytes_to_base64url"]
