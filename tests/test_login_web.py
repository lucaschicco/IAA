"""Login real en la web: mail y contraseña con la lógica de `auth/cuentas.py`, e invitaciones en `/registro`.

Datos inventados de `sembrar-demo`: los usuarios demo (salvo el de plataforma) tienen `CONTRASENA_DEMO`."""
from __future__ import annotations

import re

import pytest
from apoyo_web import vaciar_y_sembrar
from fastapi.testclient import TestClient
from sqlalchemy import text

from consorcio.agente.semilla import CONTRASENA_DEMO, PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.auth.cuentas import crear_invitacion
from consorcio.auth.db import conectar
from consorcio.config import config
from consorcio.identidad import resolver_identidad
from consorcio.web.app import crear_app
from consorcio.web.sesion import NOMBRE_COOKIE, leer_sesion

ADMIN = "demo-admin-palmeras@demo.test"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, monkeypatch):
    monkeypatch.setattr(config, "web_selector_usuarios", False)
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE accesos, invitaciones"))


def _csrf(pagina) -> str:
    return re.search(r'name="csrf" value="([^"]+)"', pagina.text).group(1)


def _sesion(cliente: TestClient):
    """Con `=` de relleno base64 Starlette manda la cookie entre comillas; httpx las guarda tal cual."""
    return leer_sesion(cliente.cookies[NOMBRE_COOKIE].strip('"'))


def _cliente() -> TestClient:
    return TestClient(crear_app(), base_url="https://testserver", follow_redirects=False)


def entrar(cliente: TestClient, email: str, contrasena: str):
    csrf = _csrf(cliente.get("/entrar"))
    return cliente.post("/entrar", data={"email": email, "contrasena": contrasena, "csrf": csrf})


def test_sin_selector_entrar_muestra_el_formulario_y_ningun_usuario_demo():
    pagina = _cliente().get("/entrar")
    assert pagina.status_code == 200
    assert 'type="password"' in pagina.text and "demo-admin-palmeras" not in pagina.text


def test_login_correcto_crea_sesion_del_proveedor_login():
    cliente = _cliente()
    r = entrar(cliente, ADMIN.upper(), CONTRASENA_DEMO)
    assert r.status_code == 303 and r.headers["location"] == "/"  # `/` decide: panel o elegir consorcio
    sesion = _sesion(cliente)
    assert (sesion.sujeto_externo, sesion.proveedor) == ("demo-admin-palmeras", "login")


def test_contrasena_incorrecta_y_mail_inexistente_dan_el_mismo_error():
    malo = entrar(_cliente(), ADMIN, "otra-contrasena-larga")
    nadie = entrar(_cliente(), "nadie@demo.test", "otra-contrasena-larga")
    assert malo.status_code == nadie.status_code == 401
    assert "Mail o contraseña incorrectos." in malo.text and "Mail o contraseña incorrectos." in nadie.text
    assert NOMBRE_COOKIE not in malo.cookies


def test_bloqueo_tras_cinco_intentos_fallidos():
    for _ in range(5):
        assert entrar(_cliente(), ADMIN, "incorrecta-incorrecta").status_code == 401
    r = entrar(_cliente(), ADMIN, CONTRASENA_DEMO)
    assert r.status_code == 429 and "Demasiados intentos" in r.text


def test_login_exige_csrf():
    cliente = _cliente()
    cliente.get("/entrar")
    assert cliente.post("/entrar", data={"email": ADMIN, "contrasena": CONTRASENA_DEMO}).status_code == 403


def test_el_usuario_de_plataforma_no_tiene_contrasena():
    assert entrar(_cliente(), "demo-plataforma@demo.test", CONTRASENA_DEMO).status_code == 401


def test_con_selector_tambien_se_entra_con_contrasena(monkeypatch):
    monkeypatch.setattr(config, "web_selector_usuarios", True)
    cliente = _cliente()
    assert "demo-admin-palmeras" in cliente.get("/entrar").text
    assert entrar(cliente, ADMIN, CONTRASENA_DEMO).status_code == 303
    assert _sesion(cliente).proveedor == "login"


def _invitar(email: str, rol: str, consorcio: str = PALMERAS, uf: int | None = None) -> str:
    with conectar() as con:
        unidad_id = None
        if uf is not None:
            unidad_id = con.execute("SELECT id FROM unidades_funcionales WHERE consorcio_id = %s AND uf = %s",
                                    (consorcio, uf)).fetchone()["id"]
        token, _ = crear_invitacion(con, consorcio, email, rol, unidad_id, None)
    return token


def registrarse(cliente: TestClient, token: str, contrasena: str, repetida: str | None = None):
    pagina = cliente.get(f"/registro?token={token}")
    return cliente.post("/registro", data={"token": token, "nombre": "Vecina Inventada", "contrasena": contrasena,
                                           "repetida": contrasena if repetida is None else repetida,
                                           "csrf": _csrf(pagina)})


def test_invitacion_crea_la_cuenta_y_la_membresia_con_su_uf():
    token = _invitar("vecina@demo.test", "inquilino", uf=2)
    cliente = _cliente()
    assert "vecina@demo.test" in cliente.get(f"/registro?token={token}").text
    r = registrarse(cliente, token, "una-contrasena-larga")
    assert r.status_code == 303
    sujeto = _sesion(cliente).sujeto_externo
    assert sujeto.startswith("login:")
    identidad = resolver_identidad(sujeto, PALMERAS)
    assert (identidad.rol, identidad.unidades) == ("inquilino", (2,))
    # Ya se puede entrar con mail y contraseña, y la invitación no se vuelve a usar
    assert entrar(_cliente(), "vecina@demo.test", "una-contrasena-larga").status_code == 303
    assert _cliente().get(f"/registro?token={token}").status_code == 400


def test_invitacion_a_un_mail_con_cuenta_pide_su_contrasena_y_suma_el_consorcio():
    token = _invitar("demo-prop-palmeras@demo.test", "consejo", consorcio=ROBLES)
    assert registrarse(_cliente(), token, "no-es-la-suya-123").status_code == 401
    assert registrarse(_cliente(), token, CONTRASENA_DEMO).status_code == 303
    assert resolver_identidad("demo-prop-palmeras", ROBLES).rol == "consejo"


@pytest.mark.parametrize("contrasena,repetida,codigo", [
    ("corta", None, 422),
    ("una-contrasena-larga", "otra-contrasena-larga", 422),
])
def test_registro_valida_la_contrasena(contrasena, repetida, codigo):
    token = _invitar("otra@demo.test", "propietario", uf=4)
    assert registrarse(_cliente(), token, contrasena, repetida).status_code == codigo


def test_invitacion_inexistente_o_vencida_da_la_misma_pantalla():
    token = _invitar("tarde@demo.test", "propietario", uf=4)
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE invitaciones SET vence_en = now() - interval '1 day'"))
    vencida, inexistente = _cliente().get(f"/registro?token={token}"), _cliente().get("/registro?token=x")
    assert vencida.status_code == inexistente.status_code == 400
    assert 'type="password"' not in vencida.text
