"""Unit 10 durable plan revisions and specialist handoffs."""
from alembic import op
import sqlalchemy as sa
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('agent_plans',
        sa.Column('run_id',sa.String(32),sa.ForeignKey('runs.id'),primary_key=True),
        sa.Column('revision',sa.Integer,primary_key=True),sa.Column('payload',sa.JSON,nullable=False),
        sa.Column('created_at',sa.Float,nullable=False))
    op.create_table('agent_steps',
        sa.Column('run_id',sa.String(32),sa.ForeignKey('runs.id'),primary_key=True),
        sa.Column('revision',sa.Integer,primary_key=True),sa.Column('node_id',sa.String(40),primary_key=True),
        sa.Column('role',sa.String(20),nullable=False),sa.Column('status',sa.String(30),nullable=False),
        sa.Column('payload',sa.JSON,nullable=False),sa.Column('updated_at',sa.Float,nullable=False))

def downgrade():
    op.drop_table('agent_steps');op.drop_table('agent_plans')
