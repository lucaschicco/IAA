"""Invitar desde la web (`/invitaciones`): la administración arma el link y quien lo abre se suma al consorcio.

Datos inventados de `sembrar-demo`. El circuito completo usa el mismo `/registro` que ya prueba `test_login_web.py`."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from fastapi.testclient import TestClient
from sqlalchemy import text

from consorcio.agente.semilla import PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.identidad import membresias_de
from consorcio.web.app import crear_app

ADMIN, PROP, MULTI = "demo-admin-palmeras", "demo-prop-palmeras", "demo-multi"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base):
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE invitaciones"))


def invitar(cliente, email="nueva@demo.test", rol="propietario", piso="9", depto="Z"):
    return cliente.post("/invitaciones", data={"email": email, "rol": rol, "piso": piso, "depto": depto,
                                               "csrf": cliente.csrf})


def link_de(html: str) -> str:
    return re.search(r'id="link-invitacion" type="text" value="([^"]+)"', html).group(1).replace("&amp;", "&")


def test_la_administracion_genera_un_link_que_sirve_para_registrarse():
    r = invitar(cliente_como(ADMIN, PALMERAS))
    assert r.status_code == 201 and r.headers["cache-control"] == "no-store"
    link = link_de(r.text)
    assert urlparse(link).path == "/registro"
    token = parse_qs(urlparse(link).query)["token"][0]
    with postgres.engine().begin() as con:  # a la base va el hash, nunca el token
        assert con.execute(text("SELECT count(*) FROM invitaciones WHERE token_hash = :t"), {"t": token}).scalar() == 0
    nuevo = TestClient(crear_app(), base_url="https://testserver", follow_redirects=False)
    pagina = nuevo.get(f"/registro?token={token}")
    assert "nueva@demo.test" in pagina.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', pagina.text).group(1)
    r = nuevo.post("/registro", data={"token": token, "nombre": "Nueva", "contrasena": "una-contrasena-larga",
                                      "repetida": "una-contrasena-larga", "csrf": csrf})
    assert r.status_code == 303
    with postgres.engine().begin() as con:
        sujeto, piso, depto = con.execute(text(
            "SELECT u.sujeto_externo, f.piso, f.depto FROM usuarios u JOIN membresias m ON m.usuario_id = u.id "
            "JOIN unidades_funcionales f ON f.id = m.unidad_id WHERE u.email = 'nueva@demo.test'")).one()
    assert (piso, depto) == ("9", "Z")  # la unidad no existía: se creó en el consorcio
    assert membresias_de(sujeto) == [{"consorcio_id": PALMERAS, "nombre": membresias_de(sujeto)[0]["nombre"],
                                      "rol": "propietario"}]


def test_la_lista_muestra_el_estado_y_se_puede_anular():
    cliente = cliente_como(ADMIN, PALMERAS)
    invitar(cliente)
    html = cliente.get("/invitaciones").text
    assert "nueva@demo.test" in html and "Sin usar" in html and 'id="link-invitacion"' not in html
    invitacion = re.search(r'action="/invitaciones/(\d+)/anular"', html).group(1)
    r = cliente.post(f"/invitaciones/{invitacion}/anular", data={"csrf": cliente.csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert "Vencida o anulada" in cliente.get("/invitaciones").text
    # Anular dos veces, o una que no existe: 404
    assert cliente.post(f"/invitaciones/{invitacion}/anular", data={"csrf": cliente.csrf}).status_code == 404


def test_otra_invitacion_al_mismo_mail_anula_la_anterior():
    cliente = cliente_como(ADMIN, PALMERAS)
    primero = link_de(invitar(cliente).text)
    invitar(cliente)
    token = parse_qs(urlparse(primero).query)["token"][0]
    assert TestClient(crear_app()).get(f"/registro?token={token}").status_code == 400


@pytest.mark.parametrize("datos, error", [
    ({"email": "no-es-mail"}, "mail válido"),
    ({"rol": "plataforma"}, "Elegí qué va a ser"),
    ({"piso": "", "depto": ""}, "piso y departamento"),
])
def test_datos_invalidos_vuelven_al_formulario_sin_crear_nada(datos, error):
    r = invitar(cliente_como(ADMIN, PALMERAS), **datos)
    assert r.status_code == 422 and error in r.text and 'id="link-invitacion"' not in r.text
    with postgres.engine().begin() as con:
        assert con.execute(text("SELECT count(*) FROM invitaciones")).scalar() == 0


def test_consejo_y_administracion_no_llevan_unidad():
    r = invitar(cliente_como(ADMIN, PALMERAS), rol="consejo", piso="", depto="")
    assert r.status_code == 201
    with postgres.engine().begin() as con:
        assert con.execute(text("SELECT unidad_id FROM invitaciones")).scalar() is None


def test_solo_la_administracion_del_consorcio_elegido_invita():
    assert cliente_como(PROP, PALMERAS).get("/invitaciones").status_code == 403
    assert invitar(cliente_como(PROP, PALMERAS)).status_code == 403
    assert invitar(cliente_como(MULTI, PALMERAS)).status_code == 403  # en Palmeras es vecina
    assert invitar(cliente_como(MULTI, ROBLES)).status_code == 201  # en Robles administra
    with postgres.engine().begin() as con:
        assert con.execute(text("SELECT consorcio_id FROM invitaciones")).scalars().all() == [ROBLES]


def test_no_se_anula_una_invitacion_de_otro_consorcio():
    invitar(cliente_como(MULTI, ROBLES))
    with postgres.engine().begin() as con:
        invitacion = con.execute(text("SELECT id FROM invitaciones")).scalar()
    admin = cliente_como(ADMIN, PALMERAS)
    assert admin.post(f"/invitaciones/{invitacion}/anular", data={"csrf": admin.csrf}).status_code == 404
    assert "nueva@demo.test" not in admin.get("/invitaciones").text


def test_el_menu_de_la_administracion_tiene_invitar():
    assert 'href="/invitaciones"' in cliente_como(ADMIN, PALMERAS).get("/bandeja").text
    assert 'href="/invitaciones"' not in cliente_como(PROP, PALMERAS).get("/documentos").text
