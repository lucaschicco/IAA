"""Apoyo de los tests de la web. `cliente_como` es la única forma de crear sesión en los tests: firma la
cookie directo, sin pasar por `/entrar`, que es la pieza que después reemplaza el login real."""
from __future__ import annotations

import secrets
import time

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from consorcio.agente.semilla import sembrar_demo
from consorcio.almacen import postgres
from consorcio.config import config
from consorcio.web.app import crear_app
from consorcio.web.sesion import NOMBRE_COOKIE, Sesion, firmar_sesion, token_csrf

BASES_DESCARTABLES = {"consorcio_test"}


def vaciar_y_sembrar() -> None:
    """Borra el schema entero: solo contra una base descartable (lista blanca)."""
    if make_url(config.postgres_url).database not in BASES_DESCARTABLES:
        raise RuntimeError("la base configurada no es descartable: esto borra el schema")
    postgres.engine().dispose()
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))
    sembrar_demo()


def cliente_como(sujeto: str, consorcio: str | None = None) -> TestClient:
    """Cliente con sesión de `sujeto`; `cliente.csrf` es el token para los POST."""
    sesion = Sesion(sujeto_externo=sujeto, consorcio_id=consorcio, proveedor="prueba", nonce=secrets.token_hex(8),
                    emitida=int(time.time()))
    cliente = TestClient(crear_app())
    cliente.cookies.set(NOMBRE_COOKIE, firmar_sesion(sesion))
    cliente.csrf = token_csrf(sesion)
    return cliente


def manda_a_entrar(respuesta) -> bool:
    """Sin sesión válida la web redirige a `/entrar` (303). Sirve con o sin `follow_redirects`."""
    if respuesta.status_code == 303:
        return respuesta.headers["location"] == "/entrar"
    return bool(respuesta.history) and respuesta.history[0].status_code == 303 and respuesta.url.path == "/entrar"
