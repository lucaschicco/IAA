"""Usuarios, membresías por consorcio y unidades de cada membresía; `consultas.usuario_id`.

Solo operaciones aditivas que no dependen de nombres de constraints existentes: la base real pasó
por `migrar_consorcios`, que dejó FKs DEFERRABLE y puede no tener `ix_consultas_consorcio_id`
(lo normaliza `0004_normalizar_fks`).

Revisión: 0002_usuarios
Anterior: 0001_base
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0002_usuarios'
down_revision = '0001_base'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('usuarios',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('sujeto_externo', sa.Text(), nullable=False),
    sa.Column('nombre', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('sujeto_externo', name='uq_usuarios_sujeto_externo')
    )
    op.create_table('membresias',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('rol', sa.String(length=20), nullable=False),
    sa.CheckConstraint("rol IN ('administrador', 'propietario')", name='ck_membresias_rol'),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('usuario_id', 'consorcio_id', name='uq_membresias_usuario_consorcio')
    )
    op.create_table('membresia_unidades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('membresia_id', sa.Integer(), nullable=False),
    sa.Column('uf', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['membresia_id'], ['membresias.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('membresia_id', 'uf', name='uq_membresia_unidades_membresia_uf')
    )
    op.add_column('consultas', sa.Column('usuario_id', sa.Integer(), nullable=True))
    op.create_foreign_key('fk_consultas_usuario_id', 'consultas', 'usuarios', ['usuario_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('fk_consultas_usuario_id', 'consultas', type_='foreignkey')
    op.drop_column('consultas', 'usuario_id')
    op.drop_table('membresia_unidades')
    op.drop_table('membresias')
    op.drop_table('usuarios')
