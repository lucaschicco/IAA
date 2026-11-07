"""Conexión a Postgres. Cada pedido HTTP abre una conexión y la cierra al terminar."""
import psycopg
from psycopg.rows import dict_row

from auth import config


def conectar():
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)


def obtener_conexion():
    """Para usar con Depends(): confirma los cambios si el pedido salió bien."""
    with conectar() as con:
        yield con
