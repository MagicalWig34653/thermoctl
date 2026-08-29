from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class UserPasskey(Base):
    """Ein hinterlegter Passkey (WebAuthn-Credential) eines Benutzers.

    `credential_id` und `public_key` stehen als base64url-Text und nicht als Binaerspalte:
    Ein UNIQUE ueber eine Binaerspalte verhaelt sich zwischen SQLite und MariaDB
    unterschiedlich, und der Wert ist ohnehin nur ein Bezeichner, mit dem nie gerechnet
    wird. Grundsatz 3 — was ueber beide Datenbanken gleich sein soll, wird gleich abgelegt.

    Der oeffentliche Schluessel ist **kein Geheimnis**. Er darf im Klartext stehen; was
    zaehlt, ist der private Teil, und der verlaesst den Authenticator nie.
    """

    __tablename__ = "user_passkey"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 255 Zeichen: base64url einer Credential-ID, die die Spezifikation auf 1023 Byte
    # begrenzt. In der Praxis sind es 16 bis 64 Byte; 255 reicht mit Abstand und bleibt
    # unter der indizierbaren Schluessellaenge von MariaDB unter utf8mb4.
    credential_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    # Der Zaehler des Authenticators. Faellt er zurueck, ist der Schluessel geklont —
    # dann wird die Anmeldung abgelehnt, siehe thermoctl/domain/passkey.py.
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bezeichnung: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasskeyChallenge(Base):
    """Eine ausgegebene Challenge, bis sie eingeloest oder abgelaufen ist.

    Sie liegt **ausschliesslich hier**, nie beim Aufrufer: Wer seine eigene Challenge
    setzen kann, kann eine alte Antwort erneut einreichen.

    `zeremonie` bindet sie an ihren Zweck. Ohne diese Bindung liesse sich eine Challenge,
    die fuer eine Anmeldung ausgegeben wurde, fuer eine Registrierung einreichen.

    Zeilen werden beim Einloesen geloescht — **auch wenn die Pruefung scheitert.** Eine
    wiederverwendbare Challenge hebt den Schutz auf, den sie geben soll.
    """

    __tablename__ = "passkey_challenge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    zeremonie: Mapped[str] = mapped_column(String(16), nullable=False)
    # Nur bei der Registrierung gesetzt: dort steht schon fest, wer registriert. Bei der
    # Anmeldung nennt erst der Authenticator das Konto.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
