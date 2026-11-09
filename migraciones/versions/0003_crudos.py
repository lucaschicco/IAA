"""Crudos y duplicados: `documentos.sha256` y `documentos.crudo_clave`.

Las dos columnas son nullable: los documentos históricos no tienen hash completo ni PDF en el bucket,
y siguen detectándose como duplicados por su id `<consorcio>:<hash16>`, que no cambia.

Revisión: 0003_crudos
Anterior: 0002_usuarios
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0003_crudos'
down_revision = '0002_usuarios'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('documentos', sa.Column('sha256', sa.String(length=64), nullable=True))
    op.add_column('documentos', sa.Column('crudo_clave', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('documentos', 'crudo_clave')
    op.drop_column('documentos', 'sha256')
