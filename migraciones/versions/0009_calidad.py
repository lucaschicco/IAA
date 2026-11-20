"""Calidad de las respuestas: medidor de certeza en `turnos` y revisiones de la administración.

- `turnos` suma `certeza` (0 a 1, solo en lo respondido), `senales_certeza` (números y banderas con las que
  se calculó) y los ids de los dos mensajes del turno, para que la administración revise pregunta y
  respuesta. Los turnos anteriores quedan sin certeza y sin mensajes: no se pueden revisar.
- `revisiones`: una por turno, con veredicto (correcta, parcial, incorrecta), motivo cuando no es
  correcta y, opcional, la respuesta que correspondía.

`downgrade` borra las revisiones y las columnas nuevas.

Revisión: 0009_calidad
Anterior: 0008_login
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0009_calidad'
down_revision = '0008_login'
branch_labels = None
depends_on = None

MOTIVOS = ("'dato_incorrecto', 'cita_incorrecta', 'incompleta', 'debia_derivar', 'derivo_sin_necesidad', "
           "'no_entendio'")


def upgrade() -> None:
    op.add_column('turnos', sa.Column('certeza', sa.Float(), nullable=True))
    op.add_column('turnos', sa.Column('senales_certeza', sa.JSON(), server_default=sa.text("'{}'"), nullable=False))
    op.add_column('turnos', sa.Column('mensaje_usuario_id', sa.Integer(), nullable=True))
    op.add_column('turnos', sa.Column('mensaje_asistente_id', sa.Integer(), nullable=True))
    op.create_foreign_key('turnos_mensaje_usuario_id_fkey', 'turnos', 'mensajes', ['mensaje_usuario_id'], ['id'],
                          ondelete='SET NULL')
    op.create_foreign_key('turnos_mensaje_asistente_id_fkey', 'turnos', 'mensajes', ['mensaje_asistente_id'], ['id'],
                          ondelete='SET NULL')

    op.create_table('revisiones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('turno_id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('revisor_id', sa.Integer(), nullable=False),
    sa.Column('veredicto', sa.String(length=12), nullable=False),
    sa.Column('motivo', sa.String(length=30), nullable=True),
    sa.Column('respuesta_correcta', sa.Text(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actualizado_en', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("veredicto IN ('correcta', 'parcial', 'incorrecta')", name='ck_revisiones_veredicto'),
    sa.CheckConstraint(f"motivo IS NULL OR motivo IN ({MOTIVOS})", name='ck_revisiones_motivo'),
    sa.CheckConstraint("(veredicto = 'correcta') = (motivo IS NULL)", name='ck_revisiones_motivo_si_hay_error'),
    sa.ForeignKeyConstraint(['turno_id'], ['turnos.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['revisor_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('turno_id', name='uq_revisiones_turno_id'),
    )
    op.create_index('ix_revisiones_consorcio_creado', 'revisiones', ['consorcio_id', 'creado_en'])


def downgrade() -> None:
    op.drop_index('ix_revisiones_consorcio_creado', table_name='revisiones')
    op.drop_table('revisiones')
    op.drop_constraint('turnos_mensaje_asistente_id_fkey', 'turnos', type_='foreignkey')
    op.drop_constraint('turnos_mensaje_usuario_id_fkey', 'turnos', type_='foreignkey')
    op.drop_column('turnos', 'mensaje_asistente_id')
    op.drop_column('turnos', 'mensaje_usuario_id')
    op.drop_column('turnos', 'senales_certeza')
    op.drop_column('turnos', 'certeza')
