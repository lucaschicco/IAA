"""Esquema base: idéntico al que creaban los modelos en el commit 7d86ca2.

Una base existente no corre esta revisión: `consorcio actualizar-base` decide cómo marcarla según sus
tablas. DDL explícito a propósito, sin importar los modelos: ellos van a seguir cambiando y esta
revisión tiene que quedar fija.

Revisión: 0001_base
Anterior: ninguna
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0001_base'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('consorcios',
    sa.Column('id', sa.String(length=60), nullable=False),
    sa.Column('nombre', sa.Text(), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('consultas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pregunta', sa.Text(), nullable=False),
    sa.Column('respuesta', sa.Text(), nullable=True),
    sa.Column('cita', sa.Text(), nullable=True),
    sa.Column('consorcio_id', sa.String(length=60), nullable=True),
    sa.Column('documento_id', sa.String(length=120), nullable=True),
    sa.Column('seccion_id', sa.Integer(), nullable=True),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('verificada', sa.Boolean(), nullable=False),
    sa.Column('derivada', sa.Boolean(), nullable=False),
    sa.Column('motivo', sa.Text(), nullable=True),
    sa.Column('metodo_busqueda', sa.String(length=20), nullable=False),
    sa.Column('k', sa.Integer(), nullable=False),
    sa.Column('proveedor', sa.String(length=30), nullable=False),
    sa.Column('modelo', sa.String(length=80), nullable=False),
    sa.Column('skill_version', sa.String(length=20), nullable=False),
    sa.Column('tokens_entrada', sa.Integer(), nullable=False),
    sa.Column('tokens_salida', sa.Integer(), nullable=False),
    sa.Column('costo_usd', sa.Float(), nullable=True),
    sa.Column('segundos', sa.Float(), nullable=False),
    sa.Column('origen', sa.String(length=20), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_consultas_consorcio_id'), 'consultas', ['consorcio_id'], unique=False)
    op.create_table('chats',
    sa.Column('chat_id', sa.BigInteger(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('descripcion', sa.Text(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.PrimaryKeyConstraint('chat_id')
    )
    op.create_table('documentos',
    sa.Column('id', sa.String(length=120), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('nombre_archivo', sa.String(length=300), nullable=False),
    sa.Column('ruta', sa.Text(), nullable=False),
    sa.Column('tipo_documento', sa.String(length=50), nullable=False),
    sa.Column('titulo', sa.Text(), nullable=False),
    sa.Column('fecha_documento', sa.String(length=40), nullable=True),
    sa.Column('consorcio', sa.Text(), nullable=True),
    sa.Column('resumen', sa.Text(), nullable=False),
    sa.Column('total_paginas', sa.Integer(), nullable=False),
    sa.Column('metodo_extraccion', sa.String(length=20), nullable=False),
    sa.Column('confianza_clasificador', sa.Float(), nullable=False),
    sa.Column('clasificacion', sa.JSON(), nullable=False),
    sa.Column('costo', sa.JSON(), nullable=False),
    sa.Column('segundos_total', sa.Float(), nullable=False),
    sa.Column('estado', sa.String(length=30), nullable=False),
    sa.Column('activo', sa.Boolean(), nullable=False),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_documentos_consorcio_id'), 'documentos', ['consorcio_id'], unique=False)
    op.create_table('direcciones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.Column('calle', sa.Text(), nullable=True),
    sa.Column('numero', sa.String(length=80), nullable=True),
    sa.Column('piso', sa.String(length=80), nullable=True),
    sa.Column('departamento', sa.String(length=80), nullable=True),
    sa.Column('localidad', sa.String(length=120), nullable=True),
    sa.Column('provincia', sa.String(length=120), nullable=True),
    sa.Column('tipo', sa.String(length=40), nullable=False),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('eventos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('fecha', sa.String(length=40), nullable=False),
    sa.Column('tipo', sa.String(length=40), nullable=False),
    sa.Column('descripcion', sa.Text(), nullable=False),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('liquidaciones',
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('periodo', sa.String(length=7), nullable=False),
    sa.Column('primer_vencimiento', sa.String(length=20), nullable=True),
    sa.Column('segundo_vencimiento', sa.String(length=20), nullable=True),
    sa.Column('consorcio', sa.Text(), nullable=True),
    sa.Column('administracion', sa.Text(), nullable=True),
    sa.Column('total_gastos', sa.Float(), nullable=True),
    sa.Column('filas_prorrateo', sa.Integer(), nullable=False),
    sa.Column('filas_no_parseadas', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('documento_id')
    )
    op.create_table('llamadas_modelo',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('nodo', sa.String(length=40), nullable=False),
    sa.Column('modelo', sa.String(length=80), nullable=False),
    sa.Column('tokens_entrada', sa.Integer(), nullable=False),
    sa.Column('tokens_salida', sa.Integer(), nullable=False),
    sa.Column('segundos', sa.Float(), nullable=False),
    sa.Column('costo_usd', sa.Float(), nullable=True),
    sa.Column('creado_en', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('montos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('valor', sa.Float(), nullable=False),
    sa.Column('moneda', sa.String(length=40), nullable=False),
    sa.Column('concepto', sa.Text(), nullable=False),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('organizaciones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('nombre', sa.Text(), nullable=False),
    sa.Column('tipo', sa.String(length=80), nullable=True),
    sa.Column('rol', sa.Text(), nullable=True),
    sa.Column('domicilio', sa.Text(), nullable=True),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('paginas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('numero', sa.Integer(), nullable=False),
    sa.Column('metodo', sa.String(length=20), nullable=False),
    sa.Column('confianza', sa.Float(), nullable=True),
    sa.Column('chars', sa.Integer(), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('personas',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('nombre', sa.Text(), nullable=False),
    sa.Column('rol', sa.String(length=30), nullable=False),
    sa.Column('nacionalidad', sa.String(length=60), nullable=True),
    sa.Column('estado_civil', sa.String(length=60), nullable=True),
    sa.Column('domicilio', sa.Text(), nullable=True),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('secciones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('titulo', sa.Text(), nullable=False),
    sa.Column('tema', sa.String(length=80), nullable=False),
    sa.Column('resumen', sa.Text(), nullable=False),
    sa.Column('pagina_inicio', sa.Integer(), nullable=True),
    sa.Column('pagina_fin', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('unidades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('identificador', sa.String(length=80), nullable=False),
    sa.Column('piso', sa.String(length=80), nullable=True),
    sa.Column('destino', sa.String(length=60), nullable=True),
    sa.Column('superficie_m2', sa.Float(), nullable=True),
    sa.Column('porcentual', sa.Float(), nullable=True),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.Column('fragmento', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('datos_sensibles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('persona_id', sa.Integer(), nullable=True),
    sa.Column('organizacion_id', sa.Integer(), nullable=True),
    sa.Column('tipo', sa.String(length=30), nullable=False),
    sa.Column('valor', sa.Text(), nullable=False),
    sa.Column('sensible', sa.Boolean(), nullable=False),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['documentos.id'], ),
    sa.ForeignKeyConstraint(['organizacion_id'], ['organizaciones.id'], ),
    sa.ForeignKeyConstraint(['persona_id'], ['personas.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('liquidacion_gastos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('rubro', sa.Text(), nullable=False),
    sa.Column('proveedor', sa.Text(), nullable=True),
    sa.Column('concepto', sa.Text(), nullable=False),
    sa.Column('importe', sa.Float(), nullable=False),
    sa.Column('fecha', sa.String(length=40), nullable=True),
    sa.Column('comprobante', sa.String(length=80), nullable=True),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['liquidaciones.documento_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('liquidacion_rubros',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('numero', sa.Integer(), nullable=True),
    sa.Column('nombre', sa.Text(), nullable=False),
    sa.Column('total', sa.Float(), nullable=True),
    sa.Column('porcentaje', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['liquidaciones.documento_id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('liquidacion_unidades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('documento_id', sa.String(length=120), nullable=False),
    sa.Column('uf', sa.Integer(), nullable=False),
    sa.Column('piso_depto', sa.String(length=40), nullable=False),
    sa.Column('propietario', sa.Text(), nullable=False),
    sa.Column('porcentual', sa.Float(), nullable=False),
    sa.Column('expensas_ordinarias', sa.Float(), nullable=True),
    sa.Column('expensa_extraordinaria', sa.Float(), nullable=True),
    sa.Column('prevision_gastos', sa.Float(), nullable=True),
    sa.Column('total_expensa', sa.Float(), nullable=True),
    sa.Column('gasto_particular', sa.Float(), nullable=True),
    sa.Column('redondeo', sa.Float(), nullable=True),
    sa.Column('a_pagar', sa.Float(), nullable=True),
    sa.Column('saldo_anterior', sa.Float(), nullable=True),
    sa.Column('importes_previos', sa.JSON(), nullable=False),
    sa.Column('otros_conceptos', sa.JSON(), nullable=False),
    sa.Column('pagina', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['documento_id'], ['liquidaciones.documento_id'], ),
    sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('liquidacion_unidades')
    op.drop_table('liquidacion_rubros')
    op.drop_table('liquidacion_gastos')
    op.drop_table('datos_sensibles')
    op.drop_table('unidades')
    op.drop_table('secciones')
    op.drop_table('personas')
    op.drop_table('paginas')
    op.drop_table('organizaciones')
    op.drop_table('montos')
    op.drop_table('llamadas_modelo')
    op.drop_table('liquidaciones')
    op.drop_table('eventos')
    op.drop_table('direcciones')
    op.drop_index(op.f('ix_documentos_consorcio_id'), table_name='documentos')
    op.drop_table('documentos')
    op.drop_table('chats')
    op.drop_index(op.f('ix_consultas_consorcio_id'), table_name='consultas')
    op.drop_table('consultas')
    op.drop_table('consorcios')
