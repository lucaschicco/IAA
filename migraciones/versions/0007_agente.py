"""Agente: conversaciones, mensajes, turnos, solicitudes, libro de gastos y cargas; datos de usuario para la web.

`gastos_modelo` arranca con lo ya gastado (una fila por `llamadas_modelo` y otra por cada consulta con
tokens o costo, sin el proveedor fake): si no, el tope del mes en que se aplica esto empezaría de cero.

`downgrade` borra todo lo nuevo: conversaciones, turnos, solicitudes y el libro de gastos se pierden.

Revisión: 0007_agente
Anterior: 0006_fechas_texto
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0007_agente'
down_revision = '0006_fechas_texto'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('usuarios', sa.Column('plataforma', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('usuarios', sa.Column('ultima_actividad', sa.DateTime(timezone=True), nullable=True))
    op.create_table('conversaciones',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actualizada_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_conversaciones_usuario_consorcio', 'conversaciones', ['usuario_id', 'consorcio_id'])
    op.create_table('mensajes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversacion_id', sa.String(length=32), nullable=False),
    sa.Column('rol', sa.String(length=20), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("rol IN ('usuario', 'asistente')", name='ck_mensajes_rol'),
    sa.ForeignKeyConstraint(['conversacion_id'], ['conversaciones.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_mensajes_conversacion_id', 'mensajes', ['conversacion_id', 'id'])
    op.create_table('solicitudes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('tipo', sa.String(length=20), nullable=False),
    sa.Column('estado', sa.String(length=20), server_default='pendiente', nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('autor_id', sa.Integer(), nullable=False),
    sa.Column('motivo', sa.String(length=30), nullable=False),
    sa.Column('mensaje', sa.Text(), nullable=False),
    sa.Column('titulo', sa.Text(), nullable=False),
    sa.Column('detalle', sa.Text(), nullable=False),
    sa.Column('unidad', sa.String(length=20), nullable=True),
    sa.Column('titulo_final', sa.Text(), nullable=True),
    sa.Column('detalle_final', sa.Text(), nullable=True),
    sa.Column('respuesta', sa.Text(), nullable=True),
    sa.Column('resuelta_por', sa.Integer(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resuelta_en', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("tipo IN ('derivacion', 'reclamo')", name='ck_solicitudes_tipo'),
    sa.CheckConstraint(
        "(tipo = 'derivacion' AND estado IN ('pendiente', 'resuelta')) OR "
        "(tipo = 'reclamo' AND estado IN ('pendiente', 'aprobada', 'editada', 'rechazada'))",
        name='ck_solicitudes_tipo_estado'),
    sa.CheckConstraint(
        "motivo IN ('pedido_usuario', 'sin_respaldo', 'cita_no_verificada', 'router_invalido', "
        "'error_funcionalidad', 'reclamo')",
        name='ck_solicitudes_motivo'),
    sa.ForeignKeyConstraint(['autor_id'], ['usuarios.id'], ),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['resuelta_por'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_solicitudes_consorcio_estado', 'solicitudes', ['consorcio_id', 'estado'])
    op.create_table('turnos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('rol', sa.String(length=20), nullable=False),
    sa.Column('canal', sa.String(length=20), nullable=False),
    sa.Column('origen', sa.String(length=20), nullable=False),
    sa.Column('conversacion_id', sa.String(length=32), nullable=True),
    sa.Column('intencion', sa.String(length=30), nullable=True),
    sa.Column('funcionalidad', sa.String(length=30), nullable=True),
    sa.Column('resultado', sa.String(length=30), nullable=False),
    sa.Column('secciones_usadas', sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
    sa.Column('seccion_citada', sa.Integer(), nullable=True),
    sa.Column('consulta_id', sa.Integer(), nullable=True),
    sa.Column('solicitud_id', sa.Integer(), nullable=True),
    sa.Column('tokens_entrada', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('tokens_salida', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('costo_usd', sa.Float(), nullable=True),
    sa.Column('segundos', sa.Float(), nullable=False),
    sa.Column('modelo', sa.String(length=80), nullable=False),
    sa.Column('skill_version', sa.String(length=20), nullable=False),
    sa.Column('feedback', sa.SmallInteger(), nullable=True),
    sa.Column('error', sa.String(length=60), nullable=True),
    sa.Column('mlflow_traza_id', sa.Text(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint(
        "resultado IN ('respondida', 'sin_datos', 'derivada', 'reclamo_pendiente', 'fuera_de_alcance', "
        "'tope_excedido', 'mensaje_invalido', 'conversacion_invalida', 'error')",
        name='ck_turnos_resultado'),
    sa.CheckConstraint('feedback IN (-1, 1)', name='ck_turnos_feedback'),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['consulta_id'], ['consultas.id'], ),
    sa.ForeignKeyConstraint(['conversacion_id'], ['conversaciones.id'], ),
    sa.ForeignKeyConstraint(['solicitud_id'], ['solicitudes.id'], ),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_turnos_consorcio_creado', 'turnos', ['consorcio_id', 'creado_en'])
    op.create_index('ix_turnos_creado_en', 'turnos', ['creado_en'])
    op.create_table('gastos_modelo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=True),
    sa.Column('nodo', sa.String(length=40), nullable=False),
    sa.Column('modelo', sa.String(length=80), nullable=False),
    sa.Column('tokens_entrada', sa.Integer(), nullable=False),
    sa.Column('tokens_salida', sa.Integer(), nullable=False),
    sa.Column('costo_usd', sa.Float(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_gastos_modelo_creado_en', 'gastos_modelo', ['creado_en'])
    op.create_table('cargas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=True),
    sa.Column('estado', sa.String(length=30), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_cargas_creado_en', 'cargas', ['creado_en'])

    op.execute("""
        INSERT INTO gastos_modelo (consorcio_id, nodo, modelo, tokens_entrada, tokens_salida, costo_usd, creado_en)
        SELECT d.consorcio_id, l.nodo, l.modelo, l.tokens_entrada, l.tokens_salida, l.costo_usd, l.creado_en
        FROM llamadas_modelo l JOIN documentos d ON d.id = l.documento_id
        WHERE l.modelo <> 'fake'
    """)
    op.execute("""
        INSERT INTO gastos_modelo (consorcio_id, nodo, modelo, tokens_entrada, tokens_salida, costo_usd, creado_en)
        SELECT consorcio_id, 'responder', modelo, tokens_entrada, tokens_salida, costo_usd, creado_en
        FROM consultas
        WHERE modelo <> 'fake' AND (tokens_entrada > 0 OR tokens_salida > 0 OR costo_usd IS NOT NULL)
    """)


def downgrade() -> None:
    op.drop_table('cargas')
    op.drop_table('gastos_modelo')
    op.drop_table('turnos')
    op.drop_table('solicitudes')
    op.drop_table('mensajes')
    op.drop_table('conversaciones')
    op.drop_column('usuarios', 'ultima_actividad')
    op.drop_column('usuarios', 'plataforma')
