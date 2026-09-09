"""Add configuration change review and baseline workflow.

Revision ID: 20260902_0002
Revises: 20260902_0001
"""
from alembic import op
import sqlalchemy as sa


revision = "20260902_0002"
down_revision = "20260902_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("config_backups") as batch_op:
        batch_op.add_column(
            sa.Column("review_status", sa.String(length=20), nullable=False, server_default="pending")
        )
        batch_op.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_by", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(
            sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.create_index("ix_config_backups_review_status", ["review_status"], unique=False)
        batch_op.create_index("ix_config_backups_is_baseline", ["is_baseline"], unique=False)

    # Give every existing device a deterministic starting baseline without
    # changing or deleting any historical backup records.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE config_backups SET is_baseline = 1, review_status = 'ignored' "
            "WHERE id IN (SELECT MIN(id) FROM config_backups GROUP BY device_id)"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("config_backups") as batch_op:
        batch_op.drop_index("ix_config_backups_is_baseline")
        batch_op.drop_index("ix_config_backups_review_status")
        batch_op.drop_column("is_baseline")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("review_note")
        batch_op.drop_column("review_status")
