"""Login de Lucas sobre el esquema del agente: un solo `usuarios`, `consorcios` y `membresias`.

- `usuarios` suma mail, hash de contraseña (Argon2), `activo`, `creado_en` y `ultimo_login`. Todo nullable salvo
  `activo`: los usuarios demo y los de plataforma no tienen contraseña. El mail es único sin importar mayúsculas.
- `consorcios` suma `direccion`. El id sigue siendo el slug de texto.
- `unidades_funcionales`: las unidades del edificio (piso, depto, porcentual) y su número de UF en la liquidación,
  que es con lo que el agente consulta las expensas. No se llama `unidades` porque esa tabla ya guarda las
  unidades que se extraen de los documentos.
- `membresias` pasa al modelo de Lucas: una fila por usuario, consorcio, unidad y rol, con los roles
  `administracion`, `consejo`, `propietario` e `inquilino`. `administrador` pasa a `administracion`, también
  en `turnos`. Cada UF de `membresia_unidades` se vuelve una unidad funcional y una fila de membresía;
  `membresia_unidades` se borra. Una membresía de propietario sin unidades frena la migración.
- `invitaciones` y `accesos`, como en `src/auth/01_tablas.sql` de Lucas.

`downgrade` vuelve al modelo anterior: consejo e inquilino se pierden, y las invitaciones y los accesos también.

Revisión: 0008_login
Anterior: 0007_agente
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '0008_login'
down_revision = '0007_agente'
branch_labels = None
depends_on = None

ROLES = "rol IN ('administracion', 'consejo', 'propietario', 'inquilino')"
ROLES_CON_UNIDAD = (
    "(rol IN ('propietario', 'inquilino') AND unidad_id IS NOT NULL) "
    "OR (rol = 'administracion' AND unidad_id IS NULL) OR rol = 'consejo'"
)


def upgrade() -> None:
    # IF NOT EXISTS: `migrar_consorcios` crea `consorcios` con el modelo actual, que ya trae la columna
    op.execute("ALTER TABLE consorcios ADD COLUMN IF NOT EXISTS direccion TEXT")

    op.add_column('usuarios', sa.Column('email', sa.Text(), nullable=True))
    op.add_column('usuarios', sa.Column('hash_contrasena', sa.Text(), nullable=True))
    op.add_column('usuarios', sa.Column('activo', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('usuarios', sa.Column('creado_en', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                                        nullable=False))
    op.add_column('usuarios', sa.Column('ultimo_login', sa.DateTime(timezone=True), nullable=True))
    op.create_index('uq_usuarios_email', 'usuarios', [sa.text('lower(email)')], unique=True)

    op.create_table('unidades_funcionales',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('piso', sa.Text(), nullable=False),
    sa.Column('depto', sa.Text(), nullable=False),
    sa.Column('uf', sa.Integer(), nullable=True),
    sa.Column('porcentual', sa.Numeric(6, 3), nullable=True),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('consorcio_id', 'piso', 'depto', name='uq_unidades_funcionales_piso_depto'),
    sa.UniqueConstraint('consorcio_id', 'uf', name='uq_unidades_funcionales_uf'),
    )

    op.add_column('membresias', sa.Column('unidad_id', sa.Integer(), nullable=True))
    op.add_column('membresias', sa.Column('activo', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('membresias', sa.Column('creado_en', sa.DateTime(timezone=True), server_default=sa.text('now()'),
                                          nullable=False))
    op.create_foreign_key('fk_membresias_unidad_id', 'membresias', 'unidades_funcionales', ['unidad_id'], ['id'])
    op.drop_constraint('ck_membresias_rol', 'membresias', type_='check')
    op.drop_constraint('uq_membresias_usuario_consorcio', 'membresias', type_='unique')
    op.execute("UPDATE membresias SET rol = 'administracion' WHERE rol = 'administrador'")
    op.execute("UPDATE turnos SET rol = 'administracion' WHERE rol = 'administrador'")

    # Cada UF de una membresía: su unidad funcional (sin piso ni depto conocidos) y una fila de membresía.
    op.execute("""
        INSERT INTO unidades_funcionales (consorcio_id, piso, depto, uf)
        SELECT DISTINCT m.consorcio_id, 'UF', mu.uf::text, mu.uf
        FROM membresia_unidades mu JOIN membresias m ON m.id = mu.membresia_id
    """)
    op.execute("""
        INSERT INTO membresias (usuario_id, consorcio_id, rol, unidad_id)
        SELECT m.usuario_id, m.consorcio_id, m.rol, u.id
        FROM membresia_unidades mu
        JOIN membresias m ON m.id = mu.membresia_id
        JOIN unidades_funcionales u ON u.consorcio_id = m.consorcio_id AND u.uf = mu.uf
    """)
    op.execute("""
        DELETE FROM membresias WHERE unidad_id IS NULL AND id IN (SELECT membresia_id FROM membresia_unidades)
    """)
    op.drop_table('membresia_unidades')

    op.create_check_constraint('ck_membresias_rol', 'membresias', ROLES)
    op.create_check_constraint('ck_membresias_unidad', 'membresias', ROLES_CON_UNIDAD)
    op.execute("""
        ALTER TABLE membresias ADD CONSTRAINT uq_membresias_usuario_consorcio_unidad_rol
        UNIQUE NULLS NOT DISTINCT (usuario_id, consorcio_id, unidad_id, rol)
    """)

    op.create_table('invitaciones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('consorcio_id', sa.String(length=60), nullable=False),
    sa.Column('unidad_id', sa.Integer(), nullable=True),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('rol', sa.String(length=20), nullable=False),
    sa.Column('token_hash', sa.Text(), nullable=False),
    sa.Column('creada_por', sa.Integer(), nullable=True),
    sa.Column('creada_en', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('vence_en', sa.DateTime(timezone=True), nullable=False),
    sa.Column('usada_en', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint(ROLES, name='ck_invitaciones_rol'),
    sa.ForeignKeyConstraint(['consorcio_id'], ['consorcios.id'], ),
    sa.ForeignKeyConstraint(['unidad_id'], ['unidades_funcionales.id'], ),
    sa.ForeignKeyConstraint(['creada_por'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token_hash', name='uq_invitaciones_token_hash'),
    )
    op.create_table('accesos',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('usuario_id', sa.Integer(), nullable=True),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('exitoso', sa.Boolean(), nullable=False),
    sa.Column('ip', sa.Text(), nullable=True),
    sa.Column('momento', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id'], ),
    sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_accesos_email_momento', 'accesos', [sa.text('lower(email)'), 'momento'])


def downgrade() -> None:
    op.drop_index('ix_accesos_email_momento', table_name='accesos')
    op.drop_table('accesos')
    op.drop_table('invitaciones')

    op.create_table('membresia_unidades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('membresia_id', sa.Integer(), nullable=False),
    sa.Column('uf', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['membresia_id'], ['membresias.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('membresia_id', 'uf', name='uq_membresia_unidades_membresia_uf')
    )
    op.drop_constraint('uq_membresias_usuario_consorcio_unidad_rol', 'membresias', type_='unique')
    op.drop_constraint('ck_membresias_unidad', 'membresias', type_='check')
    op.drop_constraint('ck_membresias_rol', 'membresias', type_='check')
    op.execute("DELETE FROM membresias WHERE rol IN ('consejo', 'inquilino')")
    op.execute("UPDATE membresias SET rol = 'administrador' WHERE rol = 'administracion'")
    op.execute("UPDATE turnos SET rol = 'administrador' WHERE rol = 'administracion'")
    # Una membresía por usuario y consorcio, con sus UF.
    op.execute("""
        INSERT INTO membresias (usuario_id, consorcio_id, rol)
        SELECT DISTINCT usuario_id, consorcio_id, 'propietario' FROM membresias m
        WHERE unidad_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM membresias o WHERE o.usuario_id = m.usuario_id AND o.consorcio_id = m.consorcio_id
            AND o.unidad_id IS NULL)
    """)
    op.execute("""
        INSERT INTO membresia_unidades (membresia_id, uf)
        SELECT DISTINCT base.id, u.uf FROM membresias m
        JOIN unidades_funcionales u ON u.id = m.unidad_id
        JOIN membresias base ON base.usuario_id = m.usuario_id AND base.consorcio_id = m.consorcio_id
             AND base.unidad_id IS NULL
        WHERE u.uf IS NOT NULL
    """)
    op.execute("DELETE FROM membresias WHERE unidad_id IS NOT NULL")
    op.create_check_constraint('ck_membresias_rol', 'membresias', "rol IN ('administrador', 'propietario')")
    op.create_unique_constraint('uq_membresias_usuario_consorcio', 'membresias', ['usuario_id', 'consorcio_id'])
    op.drop_constraint('fk_membresias_unidad_id', 'membresias', type_='foreignkey')
    op.drop_column('membresias', 'creado_en')
    op.drop_column('membresias', 'activo')
    op.drop_column('membresias', 'unidad_id')
    op.drop_table('unidades_funcionales')

    op.drop_index('uq_usuarios_email', table_name='usuarios')
    op.drop_column('usuarios', 'ultimo_login')
    op.drop_column('usuarios', 'creado_en')
    op.drop_column('usuarios', 'activo')
    op.drop_column('usuarios', 'hash_contrasena')
    op.drop_column('usuarios', 'email')
    op.drop_column('consorcios', 'direccion')
