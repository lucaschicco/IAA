"""`sembrar-demo`, identidad de la web y esqueleto de la sesión. Datos inventados.

La base se destruye: `vaciar_y_sembrar` exige una base descartable por nombre."""
from __future__ import annotations

import time
from dataclasses import replace

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from fastapi import Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from consorcio import cli, identidad
from consorcio.agente.semilla import sembrar_demo
from consorcio.almacen import crudos, postgres
from consorcio.almacen.postgres import Base, Usuario
from consorcio.config import RAIZ_REPO, config
from consorcio.identidad import resolver_identidad
from consorcio.web import app as modulo_app
from consorcio.web import sesion as modulo_sesion
from consorcio.web.app import crear_app
from consorcio.web.sesion import (
    NOMBRE_COOKIE,
    Sesion,
    exigir_administrador,
    exigir_plataforma,
    firmar_sesion,
    identidad_actual,
    leer_sesion,
    token_csrf,
    verificar_csrf,
)

PALMERAS, ROBLES = "demo-palmeras", "demo-robles"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


def conteos() -> dict[str, int]:
    with Session(postgres.engine()) as s:
        return {t.name: s.scalar(select(func.count()).select_from(t)) for t in Base.metadata.sorted_tables}


# ------------------------------------------------------------------ semilla


def test_dos_veces_deja_los_mismos_conteos(base):
    antes = conteos()
    assert antes["consorcios"] == 2 and antes["documentos"] == 8 and antes["usuarios"] == 5
    assert antes["membresias"] == 5 and antes["membresia_unidades"] == 3 and antes["liquidaciones"] == 4
    sembrar_demo()
    assert conteos() == antes


def test_no_usa_modelo_ni_bucket_ni_datos(base, monkeypatch):
    def prohibido(*a, **k):
        raise AssertionError("la semilla no debe usarlo")

    monkeypatch.setattr("consorcio.llm.obtener_llm", prohibido)
    monkeypatch.setattr(crudos, "subir_crudo", prohibido)
    monkeypatch.setattr(config, "dir_procesados", RAIZ_REPO / "no-existe")
    monkeypatch.setattr(config, "dir_entrada", RAIZ_REPO / "no-existe")
    sembrar_demo()
    assert not (RAIZ_REPO / "no-existe").exists()


def test_cli_sembrar_demo(base, capsys):
    assert cli.main(["sembrar-demo"]) == 0
    assert "demo-palmeras" in capsys.readouterr().out


def test_cada_consorcio_tiene_reglamento_acta_y_dos_liquidaciones(base):
    for cid in (PALMERAS, ROBLES):
        tipos = sorted(d["tipo"] for d in postgres.listar_documentos(cid))
        assert tipos == ["acta_asamblea", "liquidacion_expensas", "liquidacion_expensas", "reglamento_copropiedad"]
        assert [f["periodo"] for f in postgres.consultar_expensas(consorcio_id=cid)] == ["2026-07", "2026-08"]


def test_roles_de_los_usuarios_demo(base):
    assert resolver_identidad("demo-admin-palmeras", PALMERAS).rol == "administrador"
    assert resolver_identidad("demo-prop-palmeras", PALMERAS).unidades == (3,)
    assert resolver_identidad("demo-prop-robles", ROBLES).unidades == (1,)
    assert resolver_identidad("demo-multi", PALMERAS).rol == "propietario"
    assert resolver_identidad("demo-multi", ROBLES).rol == "administrador"
    assert identidad.membresias_de("demo-plataforma") == [] and identidad.es_plataforma("demo-plataforma")


# ------------------------------------------------------------------ identidad


def test_es_plataforma(base):
    assert not identidad.es_plataforma("demo-prop-palmeras")
    assert not identidad.es_plataforma("no-existe")


def test_membresias_de(base):
    assert identidad.membresias_de("demo-multi") == [
        {"consorcio_id": PALMERAS, "nombre": "Edificio Las Palmeras (demo)", "rol": "propietario"},
        {"consorcio_id": ROBLES, "nombre": "Edificio Los Robles (demo)", "rol": "administrador"},
    ]
    assert identidad.membresias_de("no-existe") == []


def test_listar_usuarios_demo_nunca_trae_usuarios_reales(base):
    with Session(postgres.engine()) as s:
        s.add(Usuario(sujeto_externo="persona-real", nombre="Persona Real"))
        s.add(Usuario(sujeto_externo="xdemo-casi", nombre="Casi"))
        s.commit()
    try:
        sujetos = [u["sujeto_externo"] for u in identidad.listar_usuarios_demo()]
        assert sujetos == ["demo-admin-palmeras", "demo-multi", "demo-plataforma", "demo-prop-palmeras", "demo-prop-robles"]
    finally:
        with postgres.engine().begin() as con:
            con.execute(text("DELETE FROM usuarios WHERE sujeto_externo IN ('persona-real', 'xdemo-casi')"))


def test_registrar_actividad_escribe_una_vez_por_intervalo(base):
    uid = resolver_identidad("demo-prop-palmeras", PALMERAS).usuario_id

    def ultima():
        with Session(postgres.engine()) as s:
            return s.get(Usuario, uid).ultima_actividad

    with postgres.engine().begin() as con:
        con.execute(text("UPDATE usuarios SET ultima_actividad = NULL WHERE id = :i"), {"i": uid})
    identidad.registrar_actividad(uid)
    primera = ultima()
    assert primera is not None
    identidad.registrar_actividad(uid)
    assert ultima() == primera
    identidad.registrar_actividad(uid, cada_segundos=0)
    assert ultima() > primera


# ------------------------------------------------------------------ esqueleto web


def _sesion(**campos) -> Sesion:
    datos = {"sujeto_externo": "demo-multi", "consorcio_id": PALMERAS, "proveedor": "prueba", "nonce": "n",
             "emitida": int(time.time())}
    return Sesion(**{**datos, **campos})


def test_firma_y_lectura():
    s = _sesion()
    assert leer_sesion(firmar_sesion(s)) == s


@pytest.mark.parametrize("token", ["", "sin-punto", "a.b", "ñ.ñ", "e30=.0"], ids=["vacio", "sin-punto", "falso", "no-ascii", "cuerpo-vacio"])
def test_token_invalido_no_lanza(token):
    assert leer_sesion(token) is None


def test_cuerpo_alterado_no_vale():
    cuerpo, firma = firmar_sesion(_sesion()).split(".")
    otro = firmar_sesion(_sesion(sujeto_externo="demo-admin-palmeras")).split(".")[0]
    assert leer_sesion(f"{otro}.{firma}") is None and cuerpo != otro


def test_sesion_vencida(monkeypatch):
    s = _sesion(emitida=int(time.time()) - config.web_sesion_horas * 3600 - 1)
    assert leer_sesion(firmar_sesion(s)) is None


def test_sesion_del_futuro_no_vale():
    assert leer_sesion(firmar_sesion(_sesion(emitida=int(time.time()) + 3600))) is None


def test_el_secreto_firma(monkeypatch):
    token = firmar_sesion(_sesion())
    monkeypatch.setattr(config, "web_secreto", "otro-secreto")
    assert leer_sesion(token) is None


def test_csrf_depende_de_la_sesion():
    assert token_csrf(_sesion()) != token_csrf(replace(_sesion(), nonce="otro"))


def test_crear_app_sin_secreto_ni_selector_falla(monkeypatch):
    monkeypatch.setattr(config, "web_secreto", "")
    monkeypatch.setattr(config, "web_selector_usuarios", False)
    with pytest.raises(RuntimeError, match="WEB_SECRETO"):
        crear_app()


def test_con_selector_y_sin_secreto_avisa_y_usa_uno_aleatorio(monkeypatch, caplog):
    monkeypatch.setattr(config, "web_secreto", "")
    monkeypatch.setattr(config, "web_selector_usuarios", True)
    monkeypatch.setattr(modulo_sesion, "_secreto_de_proceso", None)
    crear_app()
    assert "sin WEB_SECRETO" in caplog.text
    assert leer_sesion(firmar_sesion(_sesion())) is not None


def _con_ruta(sujeto, consorcio, dependencia):
    """El esqueleto no tiene rutas propias: se prueba cada dependencia con una de mentira."""
    cliente = cliente_como(sujeto, consorcio)

    def ruta(_x=Depends(dependencia)):  # noqa: B008
        return {"ok": True}

    cliente.app.add_api_route("/prueba", ruta)
    return cliente.get("/prueba").status_code


@pytest.mark.parametrize("sujeto,consorcio,dependencia,esperado", [
    ("demo-prop-palmeras", PALMERAS, identidad_actual, 200),
    ("demo-prop-palmeras", ROBLES, identidad_actual, 403),
    ("demo-prop-palmeras", None, identidad_actual, 403),
    ("demo-admin-palmeras", PALMERAS, exigir_administrador, 200),
    ("demo-prop-palmeras", PALMERAS, exigir_administrador, 403),
    ("demo-plataforma", None, exigir_plataforma, 200),
    ("demo-admin-palmeras", PALMERAS, exigir_plataforma, 404),
])
def test_dependencias(base, sujeto, consorcio, dependencia, esperado):
    assert _con_ruta(sujeto, consorcio, dependencia) == esperado


def test_sin_cookie_401(base):
    cliente = cliente_como("demo-plataforma")
    cliente.cookies.delete(NOMBRE_COOKIE)

    def ruta(_x=Depends(exigir_plataforma)):  # noqa: B008
        return {}

    cliente.app.add_api_route("/prueba", ruta)
    assert cliente.get("/prueba").status_code == 401


def test_cookie_selector_con_selector_apagado_401(base, monkeypatch):
    cliente = cliente_como("demo-plataforma")
    cliente.cookies.set(NOMBRE_COOKIE, firmar_sesion(_sesion(sujeto_externo="demo-plataforma", proveedor="selector")))

    def ruta(_x=Depends(exigir_plataforma)):  # noqa: B008
        return {}

    cliente.app.add_api_route("/prueba", ruta)
    assert cliente.get("/prueba").status_code == 401
    monkeypatch.setattr(config, "web_selector_usuarios", True)
    assert cliente.get("/prueba").status_code == 200


def test_csrf(base):
    cliente = cliente_como("demo-multi", PALMERAS)

    def ruta(_x=Depends(verificar_csrf)):  # noqa: B008
        return {}

    cliente.app.add_api_route("/prueba", ruta, methods=["POST"])
    assert cliente.post("/prueba").status_code == 403
    assert cliente.post("/prueba", headers={"X-CSRF": "mal"}).status_code == 403
    assert cliente.post("/prueba", data={"csrf": "ñ"}).status_code == 403
    assert cliente.post("/prueba", headers={"X-CSRF": cliente.csrf}).status_code == 200
    assert cliente.post("/prueba", data={"csrf": cliente.csrf}).status_code == 200


def test_plantilla_base_tiene_los_bloques():
    texto = (modulo_app.DIR_ESTATICOS.parent / "plantillas" / "base.html").read_text(encoding="utf-8")
    assert all(f"block {b}" in texto for b in ("titulo", "cabeza", "contenido"))
