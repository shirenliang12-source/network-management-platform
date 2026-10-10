"""Add rack bindings, backup alert acknowledgements and ISP address metadata."""
from alembic import op
import sqlalchemy as sa
revision='20260923_0008'
down_revision='20260922_0007'
branch_labels=None
depends_on=None

def upgrade():
    existing=set(sa.inspect(op.get_bind()).get_table_names())
    if 'device_rack_locations' not in existing:
        op.create_table('device_rack_locations',
            sa.Column('device_id',sa.Integer(),sa.ForeignKey('devices.id',ondelete='CASCADE'),primary_key=True),
            sa.Column('rack_id',sa.Integer(),sa.ForeignKey('dc_racks.id'),nullable=False),
            sa.Column('position',sa.String(100)))
        op.create_index('ix_device_rack_locations_rack_id','device_rack_locations',['rack_id'])
    if 'backup_alert_states' not in existing:
        op.create_table('backup_alert_states',sa.Column('task_id',sa.Integer(),primary_key=True),
            sa.Column('item_index',sa.Integer(),primary_key=True),sa.Column('state',sa.String(20),nullable=False),
            sa.Column('operator',sa.String(100)),sa.Column('updated_at',sa.DateTime()))
    if 'isp_addresses' not in existing:
        op.create_table('isp_addresses',
            sa.Column('id',sa.Integer(),primary_key=True),
            sa.Column('line_id',sa.Integer(),sa.ForeignKey('isp_lines.id',ondelete='CASCADE'),nullable=False),
            sa.Column('address',sa.String(45),nullable=False),
            sa.Column('status',sa.String(20),nullable=False),
            sa.Column('purpose',sa.String(500)),sa.Column('ports',sa.Text()),
            sa.Column('owner',sa.String(200)),sa.Column('notes',sa.Text()),
            sa.UniqueConstraint('line_id','address',name='uq_isp_line_address'))
        op.create_index('ix_isp_addresses_line_id','isp_addresses',['line_id'])

def downgrade():
    raise RuntimeError('为保留台账和审计数据，不自动删除新增表')
