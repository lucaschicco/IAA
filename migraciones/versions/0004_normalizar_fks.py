"""Lleva una base que pasó por `migrar_consorcios` al esquema de los modelos.

Esa migración dejó DEFERRABLE los FKs hacia `documentos` y `liquidaciones` y pudo no crear
`ix_consultas_consorcio_id`; con eso `alembic check` falla y `--autogenerate` propone operaciones
espurias. Idempotente: en una base creada por estas migraciones no cambia nada. Se busca por catálogo
porque los nombres de los constraints no son los mismos en todas las bases.

`downgrade` no hace nada: el estado anterior solo le servía a aquella migración.

Revisión: 0004_normalizar_fks
Anterior: 0003_crudos
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0004_normalizar_fks'
down_revision = '0003_crudos'
branch_labels = None
depends_on = None


def upgrade() -> None:
    diferibles = op.get_bind().execute(sa.text(
        "SELECT conname, conrelid::regclass FROM pg_constraint "
        "WHERE confrelid IN ('documentos'::regclass, 'liquidaciones'::regclass) AND condeferrable"
    )).all()
    for nombre, tabla in diferibles:
        op.execute(f'ALTER TABLE {tabla} ALTER CONSTRAINT "{nombre}" NOT DEFERRABLE')
    op.execute("CREATE INDEX IF NOT EXISTS ix_consultas_consorcio_id ON consultas (consorcio_id)")


def downgrade() -> None:
    pass
