"""`liquidaciones.periodo` pasa a texto libre.

El período lo devuelve el modelo: si no lo normaliza a AAAA-MM ("Julio 2026", "2026-07-01"), el control
de período único no aplica y el documento se guarda igual, con el valor tal como vino. El varchar(7)
hacía fallar ese guardado.

`downgrade` recorta a 7 caracteres los valores más largos: esa parte del período se pierde.

Revisión: 0005_periodo_texto
Anterior: 0004_normalizar_fks
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0005_periodo_texto'
down_revision = '0004_normalizar_fks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column('liquidaciones', 'periodo', existing_type=sa.String(length=7), type_=sa.Text(),
                    existing_nullable=False)


def downgrade() -> None:
    op.alter_column('liquidaciones', 'periodo', existing_type=sa.Text(), type_=sa.String(length=7),
                    existing_nullable=False, postgresql_using='left(periodo, 7)')
