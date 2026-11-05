"""portfolio visibility and the gestor's portfolio names

``visible_usuarios`` shows a gestor's portfolio, as a summary, on the home page
of the other users. The three portfolios named here get their new name and
become visible. They are matched by their current name for the only gestor of
the database; anything else stays as it is.

Revision ID: e5f9a2c8d1b3
Revises: d4e8f1a6c3b7
Create Date: 2026-11-05 12:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f9a2c8d1b3'
down_revision = 'd4e8f1a6c3b7'
branch_labels = None
depends_on = None

_NAMES = {
    'Portafolio 1': 'PAVF: Portafolio Gestor 1',
    'HRP · 6 activos · 2026-10-22': 'PID: Portafolio Gestor 2',
    'HRP · 16 activos · 2026-11-01': 'PCM: Portafolio Gestor 3',
}
_users = sa.table('users', sa.column('id', sa.Integer), sa.column('role', sa.String))
_portfolios = sa.table(
    'portfolios', sa.column('user_id', sa.String), sa.column('name', sa.String),
    sa.column('visible_usuarios', sa.Boolean),
)


def upgrade():
    # Not in a batch: SQLite would rebuild the table, and dropping the old one
    # cascades to versions, valuations and alerts.
    op.add_column('portfolios', sa.Column('visible_usuarios', sa.Boolean(), server_default=sa.false(), nullable=False))
    bind = op.get_bind()
    gestores = bind.execute(sa.select(_users.c.id).where(_users.c.role == 'gestor')).scalars().all()
    if len(gestores) != 1:
        return
    for old_name, new_name in _NAMES.items():
        bind.execute(
            _portfolios.update()
            .where(_portfolios.c.user_id == str(gestores[0]), _portfolios.c.name == old_name)
            .values(name=new_name, visible_usuarios=True)
        )


def downgrade():
    op.drop_column('portfolios', 'visible_usuarios')
