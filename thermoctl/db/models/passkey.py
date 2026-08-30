from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class UserPasskey(Base):
    """A stored passkey (WebAuthn credential) of a user.

    `credential_id` and `public_key` are stored as base64url text and not as a binary
    column: a UNIQUE over a binary column behaves differently between SQLite and
    MariaDB, and the value is only ever an identifier that is never computed with
    anyway. Principle 3 — whatever should be the same across both databases is stored
    the same way.

    The public key is **not a secret**. It may be stored in plain text; what matters is
    the private part, and that never leaves the authenticator.
    """

    __tablename__ = "user_passkey"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 255 characters: base64url of a credential ID, which the specification limits to
    # 1023 bytes. In practice it is 16 to 64 bytes; 255 is comfortably enough and stays
    # under the indexable key length of MariaDB under utf8mb4.
    credential_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    # The authenticator's counter. If it falls back, the key has been cloned — then the
    # sign-in is rejected, see thermoctl/domain/passkey.py.
    sign_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PasskeyChallenge(Base):
    """An issued challenge, until it is redeemed or expires.

    It lives **exclusively here**, never with the caller: whoever can set their own
    challenge can resubmit an old answer.

    `ceremony` binds it to its purpose. Without this binding, a challenge issued for a
    sign-in could be submitted for a registration.

    Rows are deleted upon redemption — **even if the check fails.** A reusable
    challenge cancels out the protection it is meant to provide.
    """

    __tablename__ = "passkey_challenge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenge: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    ceremony: Mapped[str] = mapped_column(String(16), nullable=False)
    # Only set during registration: there it is already fixed who is registering. At
    # sign-in, only the authenticator names the account.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
