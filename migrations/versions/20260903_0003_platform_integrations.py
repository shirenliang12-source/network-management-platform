"""Add external inventory provenance to virtual machines.

Revision ID: 20260903_0003
Revises: 20260902_0002
"""
from alembic import op
import sqlalchemy as sa


revision = "20260903_0003"
down_revision = "20260902_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.add_column(
            sa.Column("source_type", sa.String(length=20), nullable=False, server_default="manual")
        )
        batch_op.add_column(sa.Column("external_id", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("source_endpoint", sa.String(length=500), nullable=False, server_default="")
        )
        batch_op.add_column(sa.Column("last_synced_at", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_vm_instances_source_type", ["source_type"], unique=False)
        batch_op.create_index("ix_vm_instances_external_id", ["external_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.drop_index("ix_vm_instances_external_id")
        batch_op.drop_index("ix_vm_instances_source_type")
        batch_op.drop_column("last_synced_at")
        batch_op.drop_column("source_endpoint")
        batch_op.drop_column("external_id")
        batch_op.drop_column("source_type")
