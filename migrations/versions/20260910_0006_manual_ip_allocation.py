"""Persist explicit VM address ownership without changing existing allocations."""
from alembic import op
import sqlalchemy as sa

revision = '20260910_0006'
down_revision = '20260908_0005'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ipam_ip_addresses') as batch:
        batch.add_column(sa.Column('assigned_vm_id', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_ipam_vm', 'vm_instances', ['assigned_vm_id'], ['id'])
        batch.create_index('ix_ipam_ip_addresses_assigned_vm_id', ['assigned_vm_id'])


def downgrade():
    with op.batch_alter_table('ipam_ip_addresses') as batch:
        batch.drop_index('ix_ipam_ip_addresses_assigned_vm_id')
        batch.drop_constraint('fk_ipam_vm', type_='foreignkey')
        batch.drop_column('assigned_vm_id')
