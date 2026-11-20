"""Administración como entidad (0011): semilla, herencia al crear un consorcio desde la web y `consorcio administraciones`.

La Administración no da permisos: solo agrupa consorcios para las métricas. Datos de `sembrar-demo`."""
from __future__ import annotations

import json

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from consorcio import cli
from consorcio.agente.semilla import ADMINISTRACION, PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Administracion, Consorcio, Membresia, Usuario
from consorcio.identidad import AdministracionInvalida, administraciones_de, crear_consorcio

ADMIN, ADMIN_DOS, MULTI, PROP = "demo-admin-palmeras", "demo-admin-dos", "demo-multi", "demo-prop-palmeras"


@pytest.fixture(scope="module")
def sembrado():
    vaciar_y_sembrar()


def _id_demo() -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(Administracion.id).where(Administracion.nombre == ADMINISTRACION))


@pytest.fixture(autouse=True)
def limpio(sembrado):
    """Al final de cada test: solo los dos consorcios y la Administración de la semilla, como los dejó."""
    yield
    with postgres.engine().begin() as con:
        con.execute(text("DELETE FROM membresias WHERE consorcio_id NOT IN (:p, :r)"), {"p": PALMERAS, "r": ROBLES})
        con.execute(text("DELETE FROM consorcios WHERE id NOT IN (:p, :r)"), {"p": PALMERAS, "r": ROBLES})
        con.execute(text("UPDATE consorcios SET administracion_id = :a"), {"a": _id_demo()})
        con.execute(text("DELETE FROM administraciones WHERE nombre <> :n"), {"n": ADMINISTRACION})


def _administracion_de(consorcio_id: str) -> int | None:
    with Session(postgres.engine()) as s:
        return s.get(Consorcio, consorcio_id).administracion_id


def _otra(nombre: str, consorcio_id: str | None = None) -> int:
    """Una Administración nueva; si se pasa un consorcio, lo mueve ahí."""
    adm = postgres.crear_administracion(nombre)
    if consorcio_id:
        postgres.asignar_administracion(consorcio_id, adm)
    return adm


def _administrar(sujeto: str, consorcio_id: str, nombre: str = "Consorcio de prueba") -> None:
    """Suma un consorcio (sin Administración) que la persona administra."""
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=consorcio_id, nombre=nombre))
        s.flush()
        usuario = s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == sujeto))
        s.add(Membresia(usuario_id=usuario, consorcio_id=consorcio_id, rol="administracion"))
        s.commit()


# ------------------------------------------------------------------ semilla


def test_la_semilla_agrupa_los_dos_consorcios_en_la_administracion_demo():
    assert _administracion_de(PALMERAS) == _administracion_de(ROBLES) == _id_demo()
    assert [a["nombre"] for a in postgres.listar_administraciones()] == [ADMINISTRACION, "Sin administración"]


# ------------------------------------------------------------------ herencia en crear_consorcio


def test_con_una_sola_administracion_el_consorcio_nuevo_la_hereda():
    assert administraciones_de(ADMIN) == [{"id": _id_demo(), "nombre": ADMINISTRACION}]
    assert _administracion_de(crear_consorcio(ADMIN, "Edificio Heredado")) == _id_demo()


def test_un_consorcio_administrado_sin_administracion_no_cambia_la_herencia():
    _administrar(ADMIN, "prueba-suelto")
    assert _administracion_de(crear_consorcio(ADMIN, "Edificio Heredado")) == _id_demo()


def test_sin_administraciones_queda_null():
    postgres.asignar_administracion(PALMERAS, None)  # demo-admin-palmeras solo administra Palmeras
    assert administraciones_de(ADMIN) == []
    assert _administracion_de(crear_consorcio(ADMIN, "Edificio Suelto")) is None
    with pytest.raises(AdministracionInvalida):
        crear_consorcio(ADMIN, "Edificio Suelto", administracion_id=_id_demo())


def test_con_varias_hay_que_elegir_una_de_las_propias():
    otra = _otra("Otra Administradora", ROBLES)  # demo-admin-dos administra los dos
    ajena = _otra("Administración Ajena")
    assert [a["id"] for a in administraciones_de(ADMIN_DOS)] == [_id_demo(), otra]
    for elegida in (None, ajena, 999999):
        with pytest.raises(AdministracionInvalida):
            crear_consorcio(ADMIN_DOS, "Edificio Elegido", administracion_id=elegida)
    assert _administracion_de(crear_consorcio(ADMIN_DOS, "Edificio Elegido", administracion_id=otra)) == otra


def test_con_una_sola_no_se_puede_elegir_otra():
    ajena = _otra("Administración Ajena")
    with pytest.raises(AdministracionInvalida):
        crear_consorcio(ADMIN, "Edificio Elegido", administracion_id=ajena)


def test_ser_propietario_en_un_consorcio_no_da_su_administracion():
    """demo-multi es propietaria en Palmeras y administra Robles: solo hereda la de Robles."""
    otra = _otra("Otra Administradora", PALMERAS)
    assert administraciones_de(MULTI) == [{"id": _id_demo(), "nombre": ADMINISTRACION}]
    with pytest.raises(AdministracionInvalida):
        crear_consorcio(MULTI, "Edificio Elegido", administracion_id=otra)


# ------------------------------------------------------------------ formulario de /consorcios/nuevo


def test_el_formulario_muestra_el_select_solo_con_varias():
    html = cliente_como(ADMIN).get("/consorcios/nuevo").text
    assert 'name="administracion"' not in html and f"Queda en {ADMINISTRACION}" in html
    otra = _otra("Otra Administradora", ROBLES)
    _otra("Administración Ajena")
    html = cliente_como(ADMIN_DOS).get("/consorcios/nuevo").text
    assert '<select name="administracion" required>' in html
    assert f'<option value="{_id_demo()}">{ADMINISTRACION}</option>' in html
    assert f'<option value="{otra}">Otra Administradora</option>' in html
    assert "Administración Ajena" not in html


def test_el_post_hereda_y_el_servidor_valida_la_eleccion():
    otra = _otra("Otra Administradora", ROBLES)
    ajena = _otra("Administración Ajena")
    cliente = cliente_como(ADMIN_DOS)
    for valor in ("", str(ajena), "999999", "x", "-1", "1.5", "9" * 40):
        r = cliente.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "administracion": valor, "csrf": cliente.csrf})
        assert r.status_code == 422, valor
        assert "Elegí una de tus Administraciones" in r.text and "menu-principal" in r.text
        assert '<select name="administracion" required>' in r.text  # el formulario vuelve con las opciones
    r = cliente.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "administracion": str(otra), "csrf": cliente.csrf},
                     follow_redirects=False)
    assert r.status_code == 303
    assert _administracion_de("edificio-prueba") == otra


def test_el_post_con_una_sola_administracion_la_hereda_sin_elegir():
    cliente = cliente_como(ADMIN)
    r = cliente.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "csrf": cliente.csrf}, follow_redirects=False)
    assert r.status_code == 303 and _administracion_de("edificio-prueba") == _id_demo()


def test_un_vecino_sigue_sin_poder_crear_aunque_mande_una_administracion():
    vecino = cliente_como(PROP)
    r = vecino.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "administracion": str(_id_demo()),
                                               "csrf": vecino.csrf}, follow_redirects=False)
    assert r.status_code == 403


# ------------------------------------------------------------------ consorcio administraciones


def _cli(capsys, *args) -> tuple[int, list, str]:
    try:
        rc = cli.main(list(args))
    except SystemExit as e:
        rc = e.code
    salida = capsys.readouterr()
    return rc, [json.loads(linea) for linea in salida.out.splitlines()], salida.err


def test_cli_lista_crea_asigna_y_quita(capsys):
    rc, filas, _ = _cli(capsys, "administraciones")
    assert rc == 0 and filas == [{"id": _id_demo(), "nombre": ADMINISTRACION, "consorcios": [PALMERAS, ROBLES]},
                                 {"id": None, "nombre": "Sin administración", "consorcios": []}]
    rc, filas, _ = _cli(capsys, "administraciones", "--crear", "  Nueva   Administradora ")
    nueva = filas[0]["creada"]
    assert rc == 0 and {"id": nueva, "nombre": "Nueva Administradora", "consorcios": []} in filas
    rc, filas, _ = _cli(capsys, "administraciones", "--asignar", ROBLES, "--a", str(nueva))
    assert rc == 0 and {"id": nueva, "nombre": "Nueva Administradora", "consorcios": [ROBLES]} in filas
    rc, filas, _ = _cli(capsys, "administraciones", "--quitar", PALMERAS)
    assert rc == 0 and filas[-1] == {"id": None, "nombre": "Sin administración", "consorcios": [PALMERAS]}


@pytest.mark.parametrize("args", [
    ["--asignar", PALMERAS], ["--a", "1"], ["--asignar", PALMERAS, "--a", "x"], ["--asignar", PALMERAS, "--a", "0"],
    ["--asignar", PALMERAS, "--a", "-3"], ["--asignar", PALMERAS, "--a", "1", "--quitar", ROBLES], ["--crear", "   "],
    ["--crear", "a" * 121], ["--crear"], ["--asignar"],
])
def test_cli_argumentos_invalidos_rc_2_sin_traceback(capsys, args):
    antes = postgres.listar_administraciones()
    rc, _, err = _cli(capsys, "administraciones", *args)
    assert rc == 2 and "error:" in err and "Traceback" not in err
    assert postgres.listar_administraciones() == antes


@pytest.mark.parametrize("args, mensaje", [
    (["--asignar", "no-existe", "--a", "1"], "no existe el consorcio 'no-existe'"),
    (["--asignar", PALMERAS, "--a", "999999"], "no existe la Administración 999999"),
    (["--quitar", "no-existe"], "no existe el consorcio 'no-existe'"),
])
def test_cli_inexistentes_rc_1_con_mensaje(capsys, args, mensaje):
    antes = postgres.listar_administraciones()
    rc, filas, err = _cli(capsys, "administraciones", *args)
    assert rc == 1 and mensaje in err and "Traceback" not in err and filas == []
    assert postgres.listar_administraciones() == antes
