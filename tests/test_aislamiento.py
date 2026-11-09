"""Nada cruza entre consorcios: dos edificios inventados con el mismo archivo y la misma sección."""
from __future__ import annotations

import argparse
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from consorcio import cli
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Consulta, Membresia, UnidadFuncional, Usuario
from consorcio.config import config
from consorcio.consulta import grafo
from consorcio.consulta.buscador import armar_contexto, buscar_secciones, paginas_de
from consorcio.consulta.grafo import preguntar
from consorcio.identidad import resolver_identidad
from consorcio.ingesta import esquemas

BASES_DESCARTABLES = {"consorcio_test"}
A, B = "edificio-a", "edificio-b"
ID_A, ID_B = f"{A}:aaaa000000000000", f"{B}:bbbb000000000000"


def _hay_postgres() -> bool:
    try:
        with postgres.engine().connect():
            return True
    except Exception:
        return False


requiere_postgres = pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")


def _vaciar_base() -> None:
    postgres.engine().dispose()
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))


def _doc() -> esquemas.DocumentoExtraido:
    return esquemas.DocumentoExtraido(
        metadatos=esquemas.MetadatosDocumento(
            tipo_documento="reglamento", titulo="Reglamento interno", resumen="Reglamento inventado",
        ),
        secciones=[esquemas.Seccion(titulo="Uso de cocheras", tema="cocheras", resumen="Reglas de las cocheras",
                                    pagina_inicio=1, pagina_fin=1)],
    )


def _liquidacion(periodo: str, total: float, a_pagar: float) -> dict:
    return {
        "periodo": periodo, "total_gastos": total, "filas_prorrateo": 2,
        "rubros": [{"numero": 1, "nombre": "ABONOS", "total": total, "porcentaje": 100.0}],
        "gastos": [{"rubro": "ABONOS", "proveedor": "LIMPIEZA INVENTADA", "concepto": "limpieza", "importe": total}],
        "unidades": [
            {"uf": 1, "piso_depto": "1-A", "propietario": "Persona Uno", "porcentual": 50.0, "a_pagar": a_pagar},
            {"uf": 2, "piso_depto": "1-B", "propietario": "Persona Dos", "porcentual": 50.0, "a_pagar": a_pagar},
        ],
    }


def _guardar(documento_id: str, liquidacion: dict) -> None:
    postgres.guardar_documento(
        documento_id=documento_id, nombre_archivo="reglamento.pdf", ruta="x",
        clasificacion={"metodo": "texto", "confianza": 1, "total_paginas": 1},
        paginas=[{"numero": 1, "texto": "Texto inventado", "metodo": "texto", "confianza": 100}],
        doc=_doc(), costo={}, llamadas=[], segundos_total=0.1, liquidacion=liquidacion,
    )


def _consulta(consorcio_id: str, pregunta: str, origen: str = "cli") -> None:
    postgres.guardar_consulta({
        "pregunta": pregunta, "consorcio_id": consorcio_id, "metodo_busqueda": "tsvector", "k": 3, "proveedor": "fake",
        "modelo": "fake", "skill_version": "0.1.0", "segundos": 0.0, "origen": origen,
    })


@pytest.fixture
def dos_consorcios():
    if make_url(config.postgres_url).database not in BASES_DESCARTABLES:
        pytest.skip("la base configurada no es descartable: este test borra el schema")
    _vaciar_base()
    _guardar(ID_A, _liquidacion("2026-07", 100.0, 11.0))
    _guardar(ID_B, _liquidacion("2026-07", 200.0, 22.0))
    _consulta(A, "pregunta de A")
    _consulta(B, "pregunta de B", origen="telegram")
    yield
    _vaciar_base()


FUNCIONES = {
    "buscar_secciones": lambda **kw: buscar_secciones("cocheras", **kw),
    "consultar_expensas": lambda **kw: postgres.consultar_expensas(**kw),
    "listar_documentos": lambda **kw: postgres.listar_documentos(**kw),
    "activar_documento": lambda **kw: postgres.activar_documento("reglamento", False, **kw),
    "listar_consultas": lambda **kw: postgres.listar_consultas(**kw),
    "preguntar": lambda **kw: preguntar("¿cocheras?", **kw),
    "paginas_de": lambda **kw: paginas_de(ID_A, 1, 1, **kw),
    "armar_contexto": lambda **kw: armar_contexto([], **kw),
    "paginas_guardadas": lambda **kw: postgres.paginas_guardadas(ID_A, **kw),
    "buscar_documento": lambda **kw: postgres.buscar_documento(ID_A, **kw),
}


@pytest.mark.parametrize("nombre", FUNCIONES)
@pytest.mark.parametrize("argumentos", [{}, {"consorcio_id": None}, {"consorcio_id": ""}], ids=["falta", "none", "vacio"])
def test_sin_consorcio_hay_error_claro(nombre, argumentos, monkeypatch):
    def no_debe_llegar():
        raise AssertionError("se llegó a tocar la base o a construir el grafo sin consorcio")

    monkeypatch.setattr(postgres, "engine", no_debe_llegar)
    monkeypatch.setattr(grafo, "construir_grafo", no_debe_llegar)
    with pytest.raises(ValueError, match="consorcio_id"):
        FUNCIONES[nombre](**argumentos)


@requiere_postgres
@pytest.mark.parametrize("metodo", ["tsvector", "palabras"])
def test_busqueda_solo_ve_su_consorcio(dos_consorcios, metodo):
    for cid, doc in ((A, ID_A), (B, ID_B)):
        res = buscar_secciones("cocheras", 10, metodo, cid)
        assert [r["documento_id"] for r in res] == [doc]
    assert buscar_secciones("cocheras", 10, metodo, "otro") == []


@requiere_postgres
@pytest.mark.parametrize(("que", "campo", "valor_a", "valor_b"), [
    ("resumen", "total_gastos", 100.0, 200.0),
    ("rubros", "total", 100.0, 200.0),
    ("gastos", "importe", 100.0, 200.0),
    ("unidades", "a_pagar", 11.0, 22.0),
])
def test_expensas_solo_ven_su_consorcio(dos_consorcios, que, campo, valor_a, valor_b):
    assert {f[campo] for f in postgres.consultar_expensas(que, consorcio_id=A)} == {valor_a}
    assert {f[campo] for f in postgres.consultar_expensas(que, consorcio_id=B)} == {valor_b}
    assert postgres.consultar_expensas(que, consorcio_id="otro") == []


@requiere_postgres
def test_un_id_de_otro_consorcio_no_devuelve_nada(dos_consorcios):
    assert paginas_de(ID_B, 1, 1, A) == []
    assert paginas_de(ID_B, None, None, A) == []
    assert postgres.paginas_guardadas(ID_B, A) == []
    assert postgres.buscar_documento(ID_B, A) is None

    assert [p["texto"] for p in paginas_de(ID_B, 1, 1, B)] == ["Texto inventado"]
    assert [p["texto"] for p in postgres.paginas_guardadas(ID_B, B)] == ["Texto inventado"]
    assert postgres.buscar_documento(ID_B, B)["id"] == ID_B


@requiere_postgres
def test_armar_contexto_no_trae_paginas_de_otro_consorcio(dos_consorcios):
    resultados = buscar_secciones("cocheras", 10, "tsvector", B)
    contexto, paginas = armar_contexto(resultados, A)
    assert paginas == {1: []} and "Texto inventado" not in contexto
    assert armar_contexto(resultados, B)[1][1][0]["texto"] == "Texto inventado"


@requiere_postgres
def test_listados_solo_ven_su_consorcio(dos_consorcios):
    assert [d["id"] for d in postgres.listar_documentos(A)] == [ID_A]
    assert [c["pregunta"] for c in postgres.listar_consultas(B)] == ["pregunta de B"]
    assert postgres.listar_consultas(A, origen="telegram") == []


@requiere_postgres
def test_listar_todos_es_una_funcion_aparte(dos_consorcios):
    assert {d["id"] for d in postgres.listar_documentos_de_todos_los_consorcios()} == {ID_A, ID_B}
    assert {c["consorcio"] for c in postgres.listar_consultas_de_todos_los_consorcios()} == {A, B}
    assert [c["consorcio"] for c in postgres.listar_consultas_de_todos_los_consorcios(origen="telegram")] == [B]
    assert len(postgres.listar_consultas_de_todos_los_consorcios(ultimas=1)) == 1


@requiere_postgres
def test_activar_documento_solo_altera_su_consorcio(dos_consorcios):
    assert postgres.activar_documento("reglamento", False, A) == ["reglamento.pdf"]
    assert buscar_secciones("cocheras", 10, "tsvector", A) == []
    assert [r["documento_id"] for r in buscar_secciones("cocheras", 10, "tsvector", B)] == [ID_B]
    # ni siquiera con el id exacto de un documento ajeno
    assert postgres.activar_documento(ID_B, False, A) == []
    assert postgres.listar_documentos(B)[0]["activo"] is True


@requiere_postgres
def test_propietario_ve_todas_las_unidades_de_su_consorcio_y_ninguna_ajena(dos_consorcios):
    with Session(postgres.engine()) as s:
        ana = Usuario(sujeto_externo="login|ana")
        s.add(ana)
        s.flush()
        unidad = UnidadFuncional(consorcio_id=A, piso="1", depto="A", uf=1)
        s.add(unidad)
        s.flush()
        s.add(Membresia(usuario_id=ana.id, consorcio_id=A, rol="propietario", unidad_id=unidad.id))
        s.commit()
    identidad = resolver_identidad("login|ana", A)
    assert identidad.unidades == (1,)
    filas = postgres.consultar_expensas(que="unidades", consorcio_id=identidad.consorcio_id)
    assert {f["uf"] for f in filas} == {1, 2}  # sin filtro por rol: así circula la liquidación
    assert {f["a_pagar"] for f in filas} == {11.0}


@requiere_postgres
def test_preguntar_guarda_el_usuario_si_lo_recibe(dos_consorcios):
    with Session(postgres.engine()) as s:
        usuario = Usuario(sujeto_externo="login|ana")
        s.add(usuario)
        s.commit()
        uid = usuario.id
    con_usuario = preguntar("zzzz sin resultados", consorcio_id=A, usuario_id=uid)
    sin_usuario = preguntar("zzzz sin resultados", consorcio_id=A)
    with Session(postgres.engine()) as s:
        assert s.get(Consulta, con_usuario["consulta_id"]).usuario_id == uid
        assert s.get(Consulta, sin_usuario["consulta_id"]).usuario_id is None
        assert s.scalar(select(Consulta.consorcio_id).where(Consulta.id == con_usuario["consulta_id"])) == A


# ---- CLI: el único lugar, junto con la evaluación, que resuelve el consorcio por defecto


@pytest.mark.parametrize(("cmd", "extra", "esperado"), [
    ("ingestar", {}, "por-defecto"),
    ("preguntar", {}, "por-defecto"),
    ("expensas", {}, "por-defecto"),
    ("evaluar-consultas", {}, "por-defecto"),
    ("documentos", {"desactivar": "x", "activar": None}, "por-defecto"),
    ("documentos", {"desactivar": None, "activar": "x"}, "por-defecto"),
    ("documentos", {"desactivar": None, "activar": None}, None),
    ("consultas", {}, None),
])
def test_resolver_consorcio_de_la_cli(cmd, extra, esperado, monkeypatch):
    monkeypatch.setattr(config, "consorcio_por_defecto", "por-defecto")
    args = argparse.Namespace(cmd=cmd, consorcio=None, **extra)
    cli._resolver_consorcio(args)
    assert args.consorcio == esperado
    args = argparse.Namespace(cmd=cmd, consorcio="pedido", **extra)
    cli._resolver_consorcio(args)
    assert args.consorcio == "pedido"


def _filas(salida: str) -> list[dict]:
    return [json.loads(linea) for linea in salida.splitlines() if linea.strip()]


@requiere_postgres
def test_cli_documentos_sin_consorcio_lista_todos(dos_consorcios, capsys):
    assert cli.main(["documentos"]) == 0
    assert {d["id"] for d in _filas(capsys.readouterr().out)} == {ID_A, ID_B}
    assert cli.main(["documentos", "--consorcio", A]) == 0
    assert [d["id"] for d in _filas(capsys.readouterr().out)] == [ID_A]


@requiere_postgres
def test_cli_consultas_sin_consorcio_lista_todas(dos_consorcios, capsys):
    assert cli.main(["consultas"]) == 0
    assert {c["consorcio"] for c in _filas(capsys.readouterr().out)} == {A, B}
    assert cli.main(["consultas", "--consorcio", B]) == 0
    assert [c["consorcio"] for c in _filas(capsys.readouterr().out)] == [B]


@requiere_postgres
def test_cli_desactivar_sin_consorcio_solo_afecta_al_por_defecto(dos_consorcios, monkeypatch, capsys):
    monkeypatch.setattr(config, "consorcio_por_defecto", A)
    assert cli.main(["documentos", "--desactivar", "reglamento"]) == 0
    assert postgres.listar_documentos(A)[0]["activo"] is False
    assert postgres.listar_documentos(B)[0]["activo"] is True


@requiere_postgres
def test_cli_sigue_funcionando_sin_consorcio(dos_consorcios, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(config, "consorcio_por_defecto", B)

    assert cli.main(["expensas", "unidades"]) == 0
    assert {f["a_pagar"] for f in _filas(capsys.readouterr().out)} == {22.0}

    assert cli.main(["preguntar", "zzzz sin resultados", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["consorcio_id"] == B

    csv = tmp_path / "preguntas.csv"
    csv.write_text(
        "id,particion,pregunta,documento,seccion_clave,debe_derivar,respuesta_esperada\n"
        "1,dev,cocheras,reglamento,cocheras,no,\n",
        encoding="utf-8",
    )
    assert cli.main(["evaluar-consultas", str(csv), "--sin-detalle"]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["consorcio"] == B and res["retrieval"]["por_metodo"]["tsvector"]["aciertos"] == 1
