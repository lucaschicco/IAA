"""Administración como entidad: la empresa (o persona) que administra uno o varios consorcios.

- `administraciones(id, nombre, creado_en)`.
- `consorcios.administracion_id`, nullable y con FK. Nullable a propósito: Telegram (congelado) crea `chat-<id>`
  y `auth/alta.py` crea consorcios por terminal, sin Administración. En el CRM esos van a "Sin administración".

Sin backfill: no hay forma segura de saber qué consorcios son de la misma empresa; la base real se asigna a mano
con `consorcio administraciones`. La Administración no da permisos: es solo una dimensión de métricas.

`downgrade` borra la columna y la tabla: se pierde la agrupación, no los consorcios.

Revisión: 0011_administraciones
Anterior: 0010_elevar
"""
from __future__ import annotations

from alembic import op

revision = '0011_administraciones'
down_revision = '0010_elevar'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: `migrar_consorcios` crea `consorcios` (y su FK) con el modelo actual, que ya trae todo esto
    op.execute("CREATE TABLE IF NOT EXISTS administraciones ("
               "id SERIAL NOT NULL, nombre TEXT NOT NULL, creado_en TIMESTAMP WITH TIME ZONE NOT NULL, "
               "CONSTRAINT administraciones_pkey PRIMARY KEY (id))")
    op.execute("ALTER TABLE consorcios ADD COLUMN IF NOT EXISTS administracion_id INTEGER")
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'consorcios_administracion_id_fkey') "
        "THEN ALTER TABLE consorcios ADD CONSTRAINT consorcios_administracion_id_fkey "
        "FOREIGN KEY (administracion_id) REFERENCES administraciones (id); END IF; END $$"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_consorcios_administracion_id ON consorcios (administracion_id)")


def downgrade() -> None:
    op.drop_index('ix_consorcios_administracion_id', table_name='consorcios')
    op.drop_constraint('consorcios_administracion_id_fkey', 'consorcios', type_='foreignkey')
    op.drop_column('consorcios', 'administracion_id')
    op.drop_table('administraciones')
