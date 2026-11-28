"""Entorno de Alembic: la URL sale de `config.postgres_url` y el esquema de los modelos.

`crear_tablas()` corre Alembic dentro del proceso de la app y marca `en_proceso`: ahí no se llama
a `fileConfig`, porque reemplaza los handlers del root y apagaría el logging de la app.
"""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from consorcio.almacen.postgres import Base
from consorcio.config import config as config_app

config = context.config
target_metadata = Base.metadata

if config.config_file_name and not config.attributes.get("en_proceso"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def correr_sin_conexion() -> None:
    context.configure(url=config_app.postgres_url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def correr_con_conexion() -> None:
    engine = create_engine(config_app.postgres_url, poolclass=pool.NullPool)
    with engine.connect() as conexion:
        context.configure(connection=conexion, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    correr_sin_conexion()
else:
    correr_con_conexion()
