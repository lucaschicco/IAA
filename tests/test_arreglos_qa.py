"""Un test por hallazgo de la auditoría de QA del 20/11 (R1..R4, I1..I3, I5..I8): que no vuelvan.

Web y API de login con datos de `sembrar-demo`; CLI y Telegram sin red ni modelo."""
from __future__ import annotations

import logging

import pytest
from apoyo_web import cliente_como, manda_a_entrar, vaciar_y_sembrar
from fastapi.testclient import TestClient
from sqlalchemy import text

from consorcio.agente.semilla import CONTRASENA_DEMO, PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.config import config
from consorcio.web.app import crear_app

ADMIN, PROP, PROP_ROBLES, MULTI, PLATAFORMA = (
    "demo-admin-palmeras", "demo-prop-palmeras", "demo-prop-robles", "demo-multi", "demo-plataforma")


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def selector_apagado(base, monkeypatch):
    monkeypatch.setattr(config, "web_selector_usuarios", False)


def con_menu(r) -> bool:
    return 'class="menu-principal"' in r.text and "Volver al inicio" in r.text and "Ir a ingresar" not in r.text


# ------------------------------------------------------------------ R1: errores con sesión, con menú y salida


@pytest.mark.parametrize("sujeto, consorcio, camino, codigo", [
    (PROP, PALMERAS, "/crm", 404),
    (PROP, PALMERAS, "/bandeja", 403),
    (PROP, PALMERAS, "/no-existe", 404),
    (ADMIN, PALMERAS, "/salir", 405),
    (PROP, PALMERAS, "/consorcios/nuevo", 403),
    (PLATAFORMA, None, "/panel", 403),
])
def test_error_con_sesion_tiene_menu_y_volver_al_inicio(sujeto, consorcio, camino, codigo):
    r = cliente_como(sujeto, consorcio).get(camino)
    assert r.status_code == codigo and con_menu(r), camino


def test_error_con_consorcio_elegido_muestra_el_menu_del_consorcio():
    r = cliente_como(PROP, PALMERAS).get("/bandeja")
    assert 'href="/chat"' in r.text and "Las Palmeras" in r.text


def test_error_con_consorcio_ajeno_no_muestra_su_nombre():
    """La cookie forjada con un consorcio ajeno da 403 y el menú, pero sin datos de ese consorcio."""
    r = cliente_como(PROP, ROBLES).get("/chat")
    assert r.status_code == 403 and con_menu(r)
    assert "Robles" not in r.text and 'href="/chat"' not in r.text


def test_error_sin_sesion_sigue_mandando_a_ingresar():
    r = TestClient(crear_app()).get("/no-existe")
    assert r.status_code == 404 and "Ir a ingresar" in r.text and "menu-principal" not in r.text


def test_error_htmx_es_un_fragmento():
    r = cliente_como(PROP, PALMERAS).get("/bandeja", headers={"HX-Request": "true"})
    assert r.status_code == 403 and "<html" not in r.text and "aviso" in r.text


# ------------------------------------------------------------------ 500: excepción no HTTP


def _con_ruta_rota(cliente):
    def rota():
        raise RuntimeError("detalle interno /app/secreto.py SELECT * FROM usuarios")

    cliente.app.add_api_route("/rota", rota)
    return cliente


def test_excepcion_inesperada_da_pagina_500_con_menu_sin_detalle(caplog):
    cliente = _con_ruta_rota(cliente_como(PROP, PALMERAS))
    with caplog.at_level(logging.ERROR, logger="consorcio.web"):
        r = cliente.get("/rota")
    assert r.status_code == 500 and con_menu(r)
    assert "RuntimeError" not in r.text and "secreto" not in r.text and "Traceback" not in r.text
    assert r.headers["content-security-policy"] == "default-src 'self'"
    assert "detalle interno" in caplog.text  # el detalle queda en el log


def test_excepcion_inesperada_sin_sesion_y_htmx():
    sin_sesion = _con_ruta_rota(TestClient(crear_app()))
    r = sin_sesion.get("/rota")
    assert r.status_code == 500 and "Ir a ingresar" in r.text
    r = _con_ruta_rota(cliente_como(PROP, PALMERAS)).get("/rota", headers={"HX-Request": "true"})
    assert r.status_code == 500 and "<html" not in r.text and "secreto" not in r.text


# ------------------------------------------------------------------ I2: validación de parámetros


@pytest.mark.parametrize("consulta", ["vista=x", "vista=", "dias=5", "dias=x"])
def test_crm_con_parametros_invalidos_da_pagina_422(consulta):
    r = cliente_como(PLATAFORMA).get(f"/crm?{consulta}")
    assert r.status_code == 422 and r.headers["content-type"].startswith("text/html")
    assert con_menu(r) and "literal_error" not in r.text


# ------------------------------------------------------------------ I1: sin consorcio elegido, a elegirlo


@pytest.mark.parametrize("sujeto", [ADMIN, "demo-admin-dos", MULTI])
def test_sin_consorcio_elegido_redirige_a_consorcios(sujeto):
    cliente = cliente_como(sujeto)
    for camino in ("/chat", "/documentos", "/mis-solicitudes", "/bandeja", "/revisar"):
        r = cliente.get(camino, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/consorcios", (sujeto, camino)


def test_con_consorcio_elegido_sin_permiso_sigue_en_403():
    assert cliente_como(PROP, PALMERAS).get("/bandeja").status_code == 403
    assert cliente_como(PROP, ROBLES).get("/chat").status_code == 403


# ------------------------------------------------------------------ I3: usuario dado de baja


def _activo(sujeto: str, valor: bool) -> None:
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE usuarios SET activo = :v WHERE sujeto_externo = :s"), {"v": valor, "s": sujeto})


def test_usuario_dado_de_baja_pierde_la_sesion():
    cliente = cliente_como(PROP_ROBLES, ROBLES)
    assert cliente.get("/chat").status_code == 200
    _activo(PROP_ROBLES, False)
    try:
        for camino in ("/", "/consorcios", "/chat"):
            assert manda_a_entrar(cliente.get(camino, follow_redirects=False)), camino
        r = cliente.get("/no-existe")  # la página de error tampoco lo trata como sesión
        assert r.status_code == 404 and "menu-principal" not in r.text
    finally:
        _activo(PROP_ROBLES, True)


# ------------------------------------------------------------------ API de login: R2, R3, I8


def api():
    from consorcio.auth.app import app
    return TestClient(app)


def token_api(email: str) -> dict:
    r = api().post("/auth/login", json={"email": email, "contrasena": CONTRASENA_DEMO})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def test_usuarios_demo_entran_por_la_api():
    """R3: el mismo mail `@demo.test` que acepta la web."""
    assert api().get("/auth/yo", headers=token_api(f"{PROP}@demo.test")).status_code == 200


def test_invitar_a_un_mail_demo_y_mails_mal_formados():
    cabecera = token_api(f"{ADMIN}@demo.test")
    r = api().post("/auth/invitaciones", headers=cabecera,
                   json={"consorcio_id": PALMERAS, "email": "nueva@demo.test", "rol": "consejo"})
    assert r.status_code == 201, r.text
    for mal in ("sin-arroba", "a@b", "a b@c.com", "", "x" * 400 + "@demo.test", "a@b.c\u0000"):
        r = api().post("/auth/invitaciones", headers=cabecera, json={"consorcio_id": PALMERAS, "email": mal, "rol": "consejo"})
        assert r.status_code == 422, mal
        assert api().post("/auth/login", json={"email": mal, "contrasena": "x"}).status_code == 422, mal


@pytest.mark.parametrize("unidad", [
    {"piso": "1", "depto": "A", "porcentual": 1e300},
    {"piso": "1", "depto": "A", "porcentual": -1},
    {"piso": "1", "depto": "A", "uf": 2**40},
    {"piso": "1", "depto": "A", "uf": 0},
    {"piso": "1\u0000", "depto": "A"},
    {"piso": "1", "depto": "A" * 500},
])
def test_unidades_fuera_de_rango_dan_422(unidad):
    """R2: antes daban 500."""
    r = api().post(f"/auth/consorcios/{PALMERAS}/unidades", headers=token_api(f"{ADMIN}@demo.test"), json=[unidad])
    assert r.status_code == 422, r.text


def test_consorcio_con_nul_en_la_ruta_no_da_500():
    cliente = api()
    cabecera = token_api(f"{ADMIN}@demo.test")
    assert cliente.get("/auth/consorcios/a%00b/unidades", headers=cabecera).status_code in (403, 422)


def test_registro_con_nombre_de_espacios_da_422():
    """I8: `min_length` se medía antes del strip y la cuenta quedaba con nombre vacío."""
    r = api().post("/auth/registro", json={"token": "x", "nombre": "   ", "contrasena": "una-contrasena-larga"})
    assert r.status_code == 422


def test_alta_de_unidades_por_terminal_sin_traceback(tmp_path, capsys):
    from consorcio.auth import alta

    csv = tmp_path / "u.csv"
    csv.write_text("piso,depto,uf,porcentual\n1,A,1,1e300\n", encoding="utf-8")
    for args in (["unidades", "--consorcio-id", PALMERAS, "--csv", str(csv)],
                 ["unidades", "--consorcio-id", PALMERAS, "--csv", str(tmp_path / "no-existe.csv")],
                 ["unidades", "--consorcio-id", "no-existe", "--csv", str(csv)]):
        with pytest.raises(SystemExit) as salida:
            alta.main(args)
        assert salida.value.code == 1
        assert "error:" in capsys.readouterr().err


# ------------------------------------------------------------------ CLI y Telegram: R4, I5, I6, I7


def _cli(capsys, *args) -> tuple[int, str, str]:
    from consorcio import cli

    try:
        rc = cli.main(list(args))
    except SystemExit as e:
        rc = e.code
    salida = capsys.readouterr()
    return rc, salida.out, salida.err


@pytest.mark.parametrize("args", [
    ["consultas", "--ultimas", "-1"], ["consultas", "--ultimas", "0"], ["consultas", "--ultimas", "x"],
    ["consultas", "--ultimas", str(10**20)],
    ["preguntar", "hola", "--k", "0", "--consorcio", PALMERAS],
    ["expensas", "--periodo", "xx", "--consorcio", PALMERAS], ["expensas", "rubros", "--periodo", "2026-13"],
])
def test_cli_argumentos_invalidos_sin_traceback(capsys, args):
    """R4 e I6: argparse los rechaza con un mensaje, antes de llegar a la base."""
    rc, _, err = _cli(capsys, *args)
    assert rc == 2 and "error:" in err and "Traceback" not in err


def test_cli_expensas_resumen_respeta_el_periodo(capsys):
    import json

    rc, out, _ = _cli(capsys, "expensas", "--consorcio", PALMERAS)
    todos = [json.loads(linea)["periodo"] for linea in out.splitlines()]
    assert rc == 0 and len(set(todos)) > 1
    rc, out, _ = _cli(capsys, "expensas", "--consorcio", PALMERAS, "--periodo", todos[0])
    assert rc == 0 and [json.loads(linea)["periodo"] for linea in out.splitlines()] == [todos[0]]


@pytest.mark.parametrize("args", [
    ["expensas", "--consorcio", "no-existe"], ["expensas", "gastos", "--consorcio", "no-existe"],
    ["documentos", "--consorcio", "no-existe"], ["consultas", "--consorcio", "no-existe"],
    ["preguntar", "hola", "--consorcio", "no-existe"], ["documentos", "--desactivar", "x", "--consorcio", "no-existe"],
])
def test_cli_consorcio_inexistente_da_error(capsys, args):
    """I7: antes daba vacío y rc=0, indistinguible de un consorcio sin datos."""
    rc, out, err = _cli(capsys, *args)
    assert rc == 1 and "no existe el consorcio 'no-existe'" in err and out == ""


def test_cli_consorcio_por_defecto_inexistente_da_error(capsys, monkeypatch):
    monkeypatch.setattr(config, "consorcio_por_defecto", "mercedes-que-no-esta")
    rc, _, err = _cli(capsys, "expensas")
    assert rc == 1 and "mercedes-que-no-esta" in err


def test_telegram_no_le_manda_la_excepcion_al_chat():
    """I5: el texto de la excepción (rutas, SQL, el proveedor) va al log, no al usuario."""
    import inspect

    from consorcio.canales import telegram

    fuente = inspect.getsource(telegram.correr_bot)
    assert "respuesta = MENSAJE_FALLA" in fuente and "str(e)" not in fuente
    assert "Error" not in telegram.MENSAJE_FALLA and "{" not in telegram.MENSAJE_FALLA
