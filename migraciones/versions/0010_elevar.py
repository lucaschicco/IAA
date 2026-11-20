"""Elevar consultas a la administración: dos motivos nuevos de derivación.

- `elevada_usuario`: el vecino tocó "Pasar a la administración" bajo una respuesta del chat.
- `pedido_directo`: el vecino le escribió a la administración desde Mis solicitudes, sin pasar por el modelo.

Solo cambia el check `ck_solicitudes_motivo`; no toca tablas. `downgrade` pasa esas solicitudes a
`pedido_usuario` (lo más parecido que había antes) para que el check viejo vuelva a valer: se pierde cuál
de los dos caminos usó el vecino, no la solicitud.

Revisión: 0010_elevar
Anterior: 0009_calidad
"""
from __future__ import annotations

from alembic import op

revision = '0010_elevar'
down_revision = '0009_calidad'
branch_labels = None
depends_on = None

ANTERIORES = ("'pedido_usuario', 'sin_respaldo', 'cita_no_verificada', 'router_invalido', 'error_funcionalidad', "
              "'reclamo'")
NUEVOS = "'elevada_usuario', 'pedido_directo'"


def upgrade() -> None:
    op.drop_constraint('ck_solicitudes_motivo', 'solicitudes', type_='check')
    op.create_check_constraint('ck_solicitudes_motivo', 'solicitudes', f"motivo IN ({ANTERIORES}, {NUEVOS})")


def downgrade() -> None:
    op.execute(f"UPDATE solicitudes SET motivo = 'pedido_usuario' WHERE motivo IN ({NUEVOS})")
    op.drop_constraint('ck_solicitudes_motivo', 'solicitudes', type_='check')
    op.create_check_constraint('ck_solicitudes_motivo', 'solicitudes', f"motivo IN ({ANTERIORES})")
