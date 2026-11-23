"""`consorcio evaluar-agente` con la demo y el modelo de mentira. Cada test es un caso de la spec
(`docs/evaluaciones/2026-11-23-spec-evaluar-agente.md`, sección "Casos").

La base se destruye: `vaciar_y_sembrar` exige una base descartable por nombre."""
from __future__ import annotations

import csv
import json
from datetime import date

import pytest
from apoyo_web import vaciar_y_sembrar
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from consorcio import cli
from consorcio.agente import evaluar, grafo
from consorcio.agente.evaluar import (
    EvaluacionNoPermitida,
    evaluar_agente,
    guardar_evaluacion_agente,
    router_palabras,
)
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Solicitud, Turno
from consorcio.config import config

COLUMNAS = ["id", "particion", "consorcio", "usuario", "conversacion", "mensaje", "intencion_esperada",
            "resultado_esperado", "debe_contener", "estilo", "nota"]
PABLO = {"consorcio": "demo-palmeras", "usuario": "demo-prop-palmeras"}
HOY = date(2026, 11, 23)
EXPENSAS = {"mensaje": "¿Cuánto debo de expensas en 2026-08?", "intencion_esperada": "expensas",
            "resultado_esperado": "respondida", "debe_contener": "110.000,00"}
DOCUMENTOS = {"mensaje": "¿Qué documentos hay cargados?", "intencion_esperada": "documentos",
              "resultado_esperado": "respondida", "debe_contener": "acta-demo.pdf"}


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, monkeypatch):
    """La semilla se conserva; lo que deja cada corrida, no."""
    monkeypatch.setattr(config, "fake_guionado", True)
    yield
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE turnos, mensajes, conversaciones, solicitudes, consultas CASCADE"))


def escribir_set(tmp_path, filas: list[dict], particion: str = "dev") -> str:
    """Un set chico con las columnas de `mensajes_agente.csv`; por defecto escribe Pablo, en Las Palmeras."""
    ruta = tmp_path / "mensajes.csv"
    with open(ruta, "w", encoding="utf-8", newline="") as f:
        escritor = csv.DictWriter(f, fieldnames=COLUMNAS)
        escritor.writeheader()
        for i, fila in enumerate(filas, start=1):
            escritor.writerow({"id": str(i), "particion": particion, **PABLO, "conversacion": "", "debe_contener": "",
                               "estilo": "directo", "nota": "", **fila})
    return str(ruta)


def contar(modelo, *filtro) -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(func.count()).select_from(modelo).where(*filtro))


def test_router_por_palabras_clave():
    """Caso 1."""
    assert router_palabras("¿Qué documentos hay cargados?").intencion == "documentos"
    assert router_palabras("Pasame una receta de empanadas").intencion == "reglamento"  # por descarte
    salida = router_palabras("¿Cuánto tengo que pagar de expensas en agosto?", hoy=HOY)
    assert (salida.intencion, salida.periodo, salida.propia) == ("expensas", "2026-08", True)
    assert router_palabras("¿Y en diciembre?", hoy=HOY).periodo == "2025-12"  # el último diciembre
    assert router_palabras("expensas de 2026-07", hoy=HOY).periodo == "2026-07"


def test_corrida_con_el_router_por_palabras_clave(tmp_path):
    """Casos 2 y 6: cuenta los aciertos, el reclamo no es una derivación de más y el CRM no ve los turnos."""
    ruta = escribir_set(tmp_path, [
        EXPENSAS, DOCUMENTOS,
        {"mensaje": "Quiero hacer un reclamo: se quemó la luz del palier", "intencion_esperada": "reclamo",
         "resultado_esperado": "reclamo_pendiente"},
    ])

    res = evaluar_agente(ruta, "dev", "palabras")

    assert set(res["routers"]) == {"palabras"} and "pares_intencion" not in res
    r = res["routers"]["palabras"]
    assert r["intencion"] == r["resultado"] == {"aciertos": 3, "n": 3}, r["detalle"]
    assert r["contiene"] == {"aciertos": 2, "n": 2}, r["detalle"]
    assert (r["derivaciones_de_mas"], r["derivaciones_de_menos"]) == (0, 0)
    assert contar(Solicitud) == 1
    assert contar(Turno) == contar(Turno, Turno.origen == "evaluacion") == 3
    assert grafo.nodo_router is not evaluar.nodo_router_palabras  # al terminar vuelve el router del modelo


def test_las_filas_de_una_conversacion_van_juntas(tmp_path):
    """Caso 3."""
    ruta = escribir_set(tmp_path, [
        {**EXPENSAS, "conversacion": "c1"},
        {"conversacion": "c1", "mensaje": "¿Y en 2026-07?", "intencion_esperada": "expensas",
         "resultado_esperado": "respondida"},
        DOCUMENTOS,
    ])

    res = evaluar_agente(ruta, "dev", "palabras")

    with Session(postgres.engine()) as s:
        conversaciones = [s.get(Turno, f["turno_id"]).conversacion_id for f in res["routers"]["palabras"]["detalle"]]
    assert conversaciones[0] == conversaciones[1] != conversaciones[2]


def test_test_sin_test_final_no_corre(tmp_path, capsys):
    """Caso 4: ni por la función ni por la CLI, y no deja turnos. Con --test-final sí corre."""
    ruta = escribir_set(tmp_path, [DOCUMENTOS], particion="test")

    with pytest.raises(EvaluacionNoPermitida, match="--test-final"):
        evaluar_agente(ruta, "test", "palabras")
    assert cli.main(["evaluar-agente", ruta, "--particion", "test", "--router", "palabras"]) == 2
    assert "--test-final" in capsys.readouterr().err
    assert contar(Turno) == 0

    assert evaluar_agente(ruta, "test", "palabras", test_final=True)["n"] == 1


def test_sin_la_demo_o_con_otro_consorcio_no_corre(tmp_path):
    """Caso 5: un usuario que la semilla no creó es lo que se ve sin `sembrar-demo`."""
    ruta = escribir_set(tmp_path, [{**DOCUMENTOS, "usuario": "demo-que-no-existe"}])
    with pytest.raises(EvaluacionNoPermitida, match="sembrar-demo"):
        evaluar_agente(ruta, "dev", "palabras")

    ruta = escribir_set(tmp_path, [{**DOCUMENTOS, "consorcio": "mercedes-274"}])
    with pytest.raises(EvaluacionNoPermitida, match="consorcios de la demo"):
        evaluar_agente(ruta, "dev", "palabras")
    assert contar(Turno) == 0


def test_con_el_modelo_de_mentira_no_guarda(tmp_path, monkeypatch):
    """Caso 7."""
    monkeypatch.setattr(evaluar, "DIR_EVALUACIONES", tmp_path)
    salida = {"proveedor": "fake", "particion": "dev"}

    assert guardar_evaluacion_agente(salida) is None
    assert not list(tmp_path.iterdir())

    ruta = guardar_evaluacion_agente({**salida, "proveedor": "ollama"})
    assert ruta.parent == tmp_path and json.loads(ruta.read_text(encoding="utf-8"))["proveedor"] == "ollama"


def test_los_dos_routers_y_la_tabla_de_pares(tmp_path):
    """El router del modelo de mentira decide con las mismas palabras clave: falla el indirecto igual."""
    ruta = escribir_set(tmp_path, [
        DOCUMENTOS,
        {"mensaje": "¿Qué papeles del edificio tienen subidos?", "intencion_esperada": "documentos",
         "resultado_esperado": "respondida", "estilo": "indirecto"},
    ])

    res = evaluar_agente(ruta, "dev", "ambos")

    assert set(res["routers"]) == {"modelo", "palabras"}
    pares = res["pares_intencion"]
    assert (pares["aciertan_los_dos"]["ids"], pares["ninguno"]["ids"]) == (["1"], ["2"])
    assert pares["solo_modelo"]["n"] == pares["solo_palabras"]["n"] == 0
    assert res["routers"]["palabras"]["por_estilo"]["indirecto"]["intencion"] == {"aciertos": 0, "n": 1}


def test_p95_por_rango_mas_cercano():
    assert evaluar._p95([]) is None
    assert evaluar._p95([3.0]) == 3.0
    assert evaluar._p95([float(i) for i in range(1, 21)]) == 19.0
