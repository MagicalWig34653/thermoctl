"""Passkeys

Revision ID: 53e02fa9682b
Revises: 9c3f1a44b2e0
Create Date: 2026-08-29 22:16:57.409645

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '53e02fa9682b'
down_revision: str | Sequence[str] | None = '9c3f1a44b2e0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_passkey",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("credential_id", sa.String(length=255), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("bezeichnung", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE",
            name=op.f("fk_user_passkey_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_passkey")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_user_passkey_credential_id")),
    )
    op.create_index(op.f("ix_user_passkey_user_id"), "user_passkey", ["user_id"])

    op.create_table(
        "passkey_challenge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("challenge", sa.String(length=255), nullable=False),
        sa.Column("zeremonie", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], ondelete="CASCADE",
            name=op.f("fk_passkey_challenge_user_id_user"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_passkey_challenge")),
        sa.UniqueConstraint("challenge", name=op.f("uq_passkey_challenge_challenge")),
    )


def downgrade() -> None:
    op.drop_table("passkey_challenge")
    # Der Index verschwindet mit der Tabelle. Ihn einzeln zu entfernen scheitert unter
    # MariaDB, wenn er zur Durchsetzung des Fremdschluessels gebraucht wird — derselbe
    # Fehler 1901, der schon die Migration des Schattenbetriebs betraf.
    op.drop_table("user_passkey")
