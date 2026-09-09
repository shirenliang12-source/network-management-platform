"""Add revocable sessions and external-sync field ownership.

Revision ID: 20260908_0005
Revises: 20260907_0004
"""
from alembic import op
import sqlalchemy as sa


revision = "20260908_0005"
down_revision = "20260907_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("session_version", sa.Integer(), nullable=False, server_default="1")
        )
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.add_column(
            sa.Column("sync_locked_fields", sa.Text(), nullable=False, server_default="[]")
        )


def downgrade() -> None:
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.drop_column("sync_locked_fields")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("session_version")
