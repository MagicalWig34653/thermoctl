"""Persistenz für OAuth-Clients, offene Anfragen, Codes und Refresh-Token."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from thermoctl.db.base import Base, utcnow


class OAuthClient(Base):
    __tablename__ = "oauth_client"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthRedirectUri(Base):
    __tablename__ = "oauth_redirect_uri"
    __table_args__ = (UniqueConstraint("client_id", "uri", name="uri_je_oauth_client"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("oauth_client.id", ondelete="CASCADE"), nullable=False
    )
    uri: Mapped[str] = mapped_column(String(1024), nullable=False)


class OAuthAuthorizationRequest(Base):
    __tablename__ = "oauth_authorization_request"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("oauth_client.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    state: Mapped[str | None] = mapped_column(String(512), nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    scope_preset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthAuthorizationCode(Base):
    __tablename__ = "oauth_authorization_code"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("oauth_client.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    code_challenge: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OAuthCodePermission(Base):
    __tablename__ = "oauth_code_permission"
    __table_args__ = (
        UniqueConstraint("code_id", "permission_id", "zone_id", name="recht_je_oauth_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code_id: Mapped[int] = mapped_column(
        ForeignKey("oauth_authorization_code.id", ondelete="CASCADE"), nullable=False
    )
    permission_id: Mapped[int] = mapped_column(ForeignKey("permission.id"), nullable=False)
    zone_id: Mapped[int | None] = mapped_column(
        ForeignKey("zone.id", ondelete="CASCADE"), nullable=True
    )


class OAuthRefreshToken(Base):
    __tablename__ = "oauth_refresh_token"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    family: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    client_id: Mapped[int] = mapped_column(
        ForeignKey("oauth_client.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    api_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("api_token.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    replaced_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("oauth_refresh_token.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
