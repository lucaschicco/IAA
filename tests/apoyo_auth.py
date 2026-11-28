"""Apoyo de los tests del login: base descartable vacía, con el esquema de Alembic."""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from consorcio.almacen import postgres
from consorcio.config import config

BASES_DESCARTABLES = {"consorcio_test"}
CONTRASENA = "una-contrasena-larga"


def base_vacia_con_esquema() -> None:
    if make_url(config.postgres_url).database not in BASES_DESCARTABLES:
        pytest.skip("la base configurada no es descartable: este test borra el schema")
    try:
        postgres.engine().dispose()
        with postgres.engine().begin() as con:
            con.execute(text("DROP SCHEMA public CASCADE"))
            con.execute(text("CREATE SCHEMA public"))
    except Exception as e:  # sin Postgres
        pytest.skip(f"sin Postgres: {type(e).__name__}")
    postgres.crear_tablas()
