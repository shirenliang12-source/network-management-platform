"""Add structured rack U placement to network device bindings."""
import re
from alembic import op
import sqlalchemy as sa

revision='20260924_0009'
down_revision='20260923_0008'
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    inspector=sa.inspect(bind)
    columns={row['name'] for row in inspector.get_columns('device_rack_locations')}
    if 'u_start' not in columns:
        op.add_column('device_rack_locations',sa.Column('u_start',sa.Integer(),nullable=False,server_default='1'))
    if 'u_size' not in columns:
        op.add_column('device_rack_locations',sa.Column('u_size',sa.Integer(),nullable=False,server_default='1'))
    # Preserve placements created by older releases which only stored free text.
    rows=bind.execute(sa.text('SELECT device_id, position FROM device_rack_locations')).mappings()
    for row in rows:
        value=str(row['position'] or '').strip().upper().replace('－','-').replace('—','-').replace('–','-').replace('~','-')
        match=re.search(r'U?\s*(\d+)\s*U?\s*(?:-\s*U?\s*(\d+)\s*U?)?',value)
        if not match:
            continue
        start=int(match.group(1)); end=int(match.group(2) or start)
        if start < 1 or end < start:
            continue
        bind.execute(sa.text('UPDATE device_rack_locations SET u_start=:start, u_size=:size WHERE device_id=:device_id'),
                     {'start':start,'size':end-start+1,'device_id':row['device_id']})

def downgrade():
    raise RuntimeError('为保留设备机柜U位数据，不自动删除新增列')
