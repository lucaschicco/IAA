"""Conexión a Postgres con psycopg. Cada pedido HTTP abre una conexión y la cierra al terminar.

El esquema no se crea acá: lo maneja Alembic (`migraciones/`, revisión `0008_login`). `conectar()` se asegura
de que la base esté en la revisión que espera el código, igual que el resto del sistema."""
import psycopg
from psycopg.rows import dict_row

from ..almacen.postgres import crear_tablas
from . import config


def conectar():
    crear_tablas()
    return psycopg.connect(config.database_url(), row_factory=dict_row)


def obtener_conexion():
    """Para usar con Depends(): confirma los cambios si el pedido salió bien."""
    with conectar() as con:
        yield con
