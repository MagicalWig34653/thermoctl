"""OAuth-Datenmodell mit eigenen Tabellen und Herkunft bestehender API-Token.

Revision ID: b721e49c6a30
Revises: b7d2e5a40c18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b721e49c6a30"
down_revision: str | Sequence[str] | None = "b7d2e5a40c18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "oauth_client",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("client_id", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "oauth_redirect_uri",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("oauth_client.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 512 statt 1024: der Unique-Index (client_id, uri) muss auf MariaDB mit utf8mb4
        # unter 3072 Byte bleiben (512 Zeichen = 2048 Byte).
        sa.Column("uri", sa.String(512), nullable=False),
        sa.UniqueConstraint("client_id", "uri", name="uri_je_oauth_client"),
    )
    op.create_table(
        "oauth_authorization_request",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("oauth_client.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column("state", sa.String(512), nullable=True),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(1024), nullable=False),
        sa.Column("scope_preset", sa.String(32), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "oauth_authorization_code",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("oauth_client.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("redirect_uri", sa.String(1024), nullable=False),
        sa.Column("code_challenge", sa.String(128), nullable=False),
        sa.Column("resource", sa.String(1024), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "oauth_code_permission",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "code_id",
            sa.Integer(),
            sa.ForeignKey("oauth_authorization_code.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("permission_id", sa.Integer(), sa.ForeignKey("permission.id"), nullable=False),
        sa.Column(
            "zone_id", sa.Integer(), sa.ForeignKey("zone.id", ondelete="CASCADE"), nullable=True
        ),
        sa.UniqueConstraint("code_id", "permission_id", "zone_id", name="recht_je_oauth_code"),
    )
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.add_column(
            sa.Column("source", sa.String(16), server_default="bearer", nullable=False)
        )
        batch_op.add_column(sa.Column("oauth_client_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_api_token_oauth_client_id_oauth_client",
            "oauth_client",
            ["oauth_client_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "oauth_refresh_token",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("family", sa.String(32), nullable=False),
        sa.Column(
            "client_id",
            sa.Integer(),
            sa.ForeignKey("oauth_client.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "api_token_id",
            sa.Integer(),
            sa.ForeignKey("api_token.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "replaced_by_id",
            sa.Integer(),
            sa.ForeignKey("oauth_refresh_token.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_oauth_refresh_token_family", "oauth_refresh_token", ["family"])


def downgrade() -> None:
    # Ohne die Spalte `source` ließen sich OAuth-Token nicht mehr von Bearer-Token
    # unterscheiden und würden als dauerhafte Bearer-Token weitergelten. Deshalb werden sie
    # vorher gelöscht: erst ihre Rechte, dann die Token (oauth_refresh_token hängt per
    # SET NULL an api_token und verschwindet gleich mit der Tabelle).
    verbindung = op.get_bind()
    verbindung.execute(
        sa.text(
            "DELETE FROM api_token_permission WHERE api_token_id IN "
            "(SELECT id FROM api_token WHERE source = 'oauth')"
        )
    )
    verbindung.execute(sa.text("DELETE FROM api_token WHERE source = 'oauth'"))
    op.drop_index("ix_oauth_refresh_token_family", table_name="oauth_refresh_token")
    op.drop_table("oauth_refresh_token")
    with op.batch_alter_table("api_token") as batch_op:
        batch_op.drop_constraint("fk_api_token_oauth_client_id_oauth_client", type_="foreignkey")
        batch_op.drop_column("oauth_client_id")
        batch_op.drop_column("source")
    op.drop_table("oauth_code_permission")
    op.drop_table("oauth_authorization_code")
    op.drop_table("oauth_authorization_request")
    op.drop_table("oauth_redirect_uri")
    op.drop_table("oauth_client")
