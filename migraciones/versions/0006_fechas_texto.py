"""Fechas del modelo a texto libre: `documentos.fecha_documento` y los vencimientos de `liquidaciones`.

Mismo motivo que 0005_periodo_texto: el valor lo devuelve el modelo, y uno más largo que el varchar
hacía fallar el guardado después de pagar la extracción.

`downgrade` recorta los valores más largos: esa parte de la fecha se pierde.

Revisión: 0006_fechas_texto
Anterior: 0005_periodo_texto
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0006_fechas_texto'
down_revision = '0005_periodo_texto'
branch_labels = None
depends_on = None

COLUMNAS = [('documentos', 'fecha_documento', 40), ('liquidaciones', 'primer_vencimiento', 20),
            ('liquidaciones', 'segundo_vencimiento', 20)]


def upgrade() -> None:
    for tabla, columna, largo in COLUMNAS:
        op.alter_column(tabla, columna, existing_type=sa.String(length=largo), type_=sa.Text(),
                        existing_nullable=True)


def downgrade() -> None:
    for tabla, columna, largo in COLUMNAS:
        op.alter_column(tabla, columna, existing_type=sa.Text(), type_=sa.String(length=largo),
                        existing_nullable=True, postgresql_using=f'left({columna}, {largo})')
