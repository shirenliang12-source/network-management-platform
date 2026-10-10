"""Add ISP bandwidth, grouping, purpose and dashboard catalog support."""
from alembic import op
import sqlalchemy as sa

revision = '20260925_0010'
down_revision = '20260924_0009'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'isp_groups' not in tables:
        op.create_table(
            'isp_groups',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('kind', sa.String(20), nullable=False),
            sa.Column('name', sa.String(120), nullable=False),
            sa.UniqueConstraint('kind', 'name', name='uq_isp_group_kind_name'),
        )
        op.create_index('ix_isp_groups_kind', 'isp_groups', ['kind'])
    columns = {row['name'] for row in sa.inspect(bind).get_columns('isp_lines')}
    additions = (
        ('bandwidth_mbps', sa.Integer(), '0'),
        ('company_group', sa.String(120), ''),
        ('function_group', sa.String(120), ''),
        ('region_group', sa.String(120), ''),
        ('purpose', sa.String(500), ''),
    )
    for name, kind, default in additions:
        if name not in columns:
            op.add_column('isp_lines', sa.Column(name, kind, nullable=False, server_default=default))


def downgrade():
    raise RuntimeError('为保留ISP线路、分组和仪表盘配置，不自动删除新增字段')
