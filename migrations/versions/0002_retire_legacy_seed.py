"""Retire unchanged legacy demo documents; preserve user records and run history."""
import hashlib
import json
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

LEGACY = {'documents': {'handbook': 'd025dd67bd3ec3681b6b7a3e2798561ded1881ad3646d1f9e6a2214f1a55cd26', 'atlas-overview': '6d97644d5ca5d57182230c6de38b0412b8900969af0b3773fd8e55d11b2b2b7f', 'atlas-engineering': '04775333c5602b26db0a5d7f9e05b60d596b4bf03fe322e028e2a6950e939902', 'atlas-sales': '265e4cba521bdc4964437d7cd1a7e8c752a6e49a45952cbe20a748a4d4cf5d4c', 'atlas-budget': 'b6761d56893c9cf77a2f89eeebdfa58c70fa887b3620d0c69154777e4528ce44', 'mei-notes': 'a6e94bb33164426992149927b9a45c1bb1b2306c6c3b12ce8e37f6e5f5ef2d54', 'atlas-meeting': '305c592a700428d8407cc7e1c3beebf8f34d996951e1138f5d2189bad26a7727', 'other-atlas-plan': 'b5a9916a5ac85046635e8806ab88e6a81b8cb7be18d515ad2cbf6dc96db47b75'}, 'projects': {'atlas': '95c40caf3687e87163cf68266d5e96df391f68ab9c79e1f310c910acae03d8b6', 'other-atlas': '7461d5bf7939eeb28b0c773ffc3c5a9a4c045c61226c3810a0f011793c3c1874'}}


def upgrade():
    connection = op.get_bind()
    documents = sa.table('documents', sa.column('id'), sa.column('payload', sa.JSON))
    projects = sa.table('projects', sa.column('id'), sa.column('payload', sa.JSON))
    for name, table in [('documents', documents), ('projects', projects)]:
        for row in connection.execute(sa.select(table)).mappings():
            digest = hashlib.sha256(json.dumps(row['payload'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if LEGACY[name].get(row['id']) != digest:
                continue
            if name == 'projects':
                remaining = connection.execute(sa.select(documents.c.payload)).scalars()
                if any(doc.get('project_id') == row['id'] for doc in remaining):
                    continue
            connection.execute(sa.delete(table).where(table.c.id == row['id']))


def downgrade():
    # Data retirement is intentionally not reversed into a user's workspace.
    pass
