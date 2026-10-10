"""Add ISP inventory without modifying existing business tables."""
from alembic import op
import sqlalchemy as sa

revision = '20260922_0007'
down_revision = '20260910_0006'
branch_labels = None
depends_on = None


def upgrade():
    # Legacy startup may have created this table before baselining Alembic.
    if sa.inspect(op.get_bind()).has_table('isp_lines'):
        return
    op.create_table(
        'isp_lines',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('identity_key', sa.String(64), nullable=False, unique=True),
        sa.Column('provider', sa.String(120), nullable=False),
        sa.Column('circuit_number', sa.String(120), nullable=False),
        sa.Column('networks_json', sa.Text(), nullable=False),
        sa.Column('used_json', sa.Text(), nullable=False),
        sa.Column('reserved_json', sa.Text(), nullable=False),
        sa.Column('location', sa.String(500), nullable=False, server_default=''),
        sa.Column('notes', sa.Text(), nullable=False, server_default=''),
    )


def downgrade():
    raise RuntimeError('ISP数据迁移禁止自动删除表；请恢复升级前的完整备份')
