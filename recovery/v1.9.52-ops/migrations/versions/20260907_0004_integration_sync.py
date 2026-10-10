"""Add durable external synchronization, storage relations and stale state.

Revision ID: 20260907_0004
Revises: 20260903_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "20260907_0004"
down_revision = "20260903_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.add_column(
            sa.Column("sync_state", sa.String(length=20), nullable=False, server_default="active")
        )
        batch_op.add_column(sa.Column("stale_since", sa.DateTime(), nullable=True))
        batch_op.create_index("ix_vm_instances_sync_state", ["sync_state"], unique=False)

    # Old releases did not enforce source identity uniqueness. Preserve every
    # row while isolating any historical duplicate for manual reconciliation.
    op.execute(
        """
        UPDATE vm_instances
        SET external_id = external_id || '#duplicate-' || id,
            sync_state = 'stale_confirmed'
        WHERE external_id IS NOT NULL
          AND id NOT IN (
            SELECT MIN(id) FROM vm_instances
            WHERE external_id IS NOT NULL
            GROUP BY source_type, external_id
          )
        """
    )
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.create_unique_constraint(
            "uq_vm_instances_source_external", ["source_type", "external_id"]
        )

    op.create_table(
        "integration_storage_assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_endpoint", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("storage_type", sa.String(length=100), nullable=True),
        sa.Column("capacity", sa.String(length=100), nullable=True),
        sa.Column("used_space", sa.String(length=100), nullable=True),
        sa.Column("free_space", sa.String(length=100), nullable=True),
        sa.Column("accessible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("vm_count", sa.Integer(), nullable=True),
        sa.Column("sync_state", sa.String(length=20), nullable=False, server_default="active"),
        sa.Column("stale_since", sa.DateTime(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_type", "external_id", name="uq_integration_storage_source_external"
        ),
    )
    op.create_index("ix_integration_storage_assets_id", "integration_storage_assets", ["id"])
    op.create_index("ix_integration_storage_assets_source_type", "integration_storage_assets", ["source_type"])
    op.create_index("ix_integration_storage_assets_name", "integration_storage_assets", ["name"])
    op.create_index("ix_integration_storage_assets_accessible", "integration_storage_assets", ["accessible"])
    op.create_index("ix_integration_storage_assets_sync_state", "integration_storage_assets", ["sync_state"])

    op.create_table(
        "vm_storage_links",
        sa.Column("vm_id", sa.Integer(), nullable=False),
        sa.Column("storage_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storage_id"], ["integration_storage_assets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vm_id"], ["vm_instances.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("vm_id", "storage_id"),
    )

    op.create_table(
        "integration_sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="running"),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("discovered_vms", sa.Integer(), nullable=True),
        sa.Column("discovered_storage", sa.Integer(), nullable=True),
        sa.Column("created_count", sa.Integer(), nullable=True),
        sa.Column("updated_count", sa.Integer(), nullable=True),
        sa.Column("unchanged_count", sa.Integer(), nullable=True),
        sa.Column("stale_count", sa.Integer(), nullable=True),
        sa.Column("diff_summary", sa.JSON(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_integration_sync_runs_id", "integration_sync_runs", ["id"])
    op.create_index("ix_integration_sync_runs_source_type", "integration_sync_runs", ["source_type"])
    op.create_index("ix_integration_sync_runs_mode", "integration_sync_runs", ["mode"])
    op.create_index("ix_integration_sync_runs_status", "integration_sync_runs", ["status"])
    op.create_index("ix_integration_sync_runs_started_at", "integration_sync_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("integration_sync_runs")
    op.drop_table("vm_storage_links")
    op.drop_table("integration_storage_assets")
    with op.batch_alter_table("vm_instances") as batch_op:
        batch_op.drop_constraint("uq_vm_instances_source_external", type_="unique")
        batch_op.drop_index("ix_vm_instances_sync_state")
        batch_op.drop_column("stale_since")
        batch_op.drop_column("sync_state")
