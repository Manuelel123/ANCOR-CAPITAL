"""onboarding: data notice acceptance and introduction

Existing users keep their profile and get the three columns empty, so they
accept the notice on their next visit.

Revision ID: d4e8f1a6c3b7
Revises: c7d1e9f3a2b4
Create Date: 2026-11-05 09:00:00

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e8f1a6c3b7'
down_revision = 'c7d1e9f3a2b4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('datos_aceptados_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('datos_aviso_version', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('intro_vista_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Not in a batch: rebuilding users in SQLite cascades to everything it owns.
    op.drop_column('users', 'intro_vista_at')
    op.drop_column('users', 'datos_aviso_version')
    op.drop_column('users', 'datos_aceptados_at')
