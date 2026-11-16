"""Trazas de MLflow de verdad: un turno con una consulta anidada tiene que quedar completo.

El 16/11 la traza de un turno de reglamento quedaba "In progress", sin el span raíz ni el nodo
`reglamento`: la consulta anidada volvía a llamar a `mlflow.set_experiment`, que reinicia el proveedor de
trazas con la traza del turno todavía abierta. Los tests de `test_agente.py` usan un MLflow de mentira y
no pueden ver spans perdidos; este usa MLflow real, con sqlite en una carpeta temporal y nunca el del
proyecto.

Corre en otro proceso porque el autolog de LangChain y el destino de MLflow son globales: así no se
filtran a los demás tests, que corren con MLflow apagado (`conftest.py`)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# Como un turno del agente: el nodo `reglamento` abre su propia corrida (la consulta) y corre otro grafo
# adentro, con la traza del turno abierta. Imprime, como última línea, un JSON con cada traza guardada.
GUION = """
import json
from typing import TypedDict

import mlflow
from langgraph.graph import END, START, StateGraph

from consorcio.config import config
from consorcio.observabilidad import trazar_corrida


class Estado(TypedDict):
    texto: str


def paso(nombre):
    return lambda estado: {"texto": f"{estado['texto']} {nombre}"}


def grafo(*nodos):
    g = StateGraph(Estado)
    for nombre, funcion in nodos:
        g.add_node(nombre, funcion)
    nombres = [nombre for nombre, _ in nodos]
    g.add_edge(START, nombres[0])
    for origen, destino in zip(nombres, nombres[1:]):
        g.add_edge(origen, destino)
    g.add_edge(nombres[-1], END)
    return g.compile()


def reglamento(estado):
    with trazar_corrida("consulta", {"origen": "prueba"}):
        final = grafo(("buscar", paso("buscar")), ("verificar", paso("verificar"))).invoke(estado)
    return {"texto": final["texto"]}


turno = grafo(("preparar", paso("preparar")), ("reglamento", reglamento), ("registrar", paso("registrar")))
with trazar_corrida("agente", {"canal": "prueba"}):
    turno.invoke({"texto": "hola"})

experimento = mlflow.get_experiment_by_name(config.mlflow_experiment)
trazas = mlflow.search_traces(locations=[experimento.experiment_id], return_type="list", flush=True)
print(json.dumps([
    {
        "estado": str(getattr(t.info.state, "value", t.info.state)),
        "raices": [s.name for s in t.data.spans if s.parent_id is None],
        "spans": sorted(s.name for s in t.data.spans),
    }
    for t in trazas
]))
"""


def test_la_consulta_anidada_deja_completa_la_traza_del_turno(tmp_path):
    entorno = {
        **os.environ,
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        "MLFLOW_EXPERIMENT": "prueba-traza-anidada",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(RAIZ / "src"), os.environ.get("PYTHONPATH")])),
    }
    # En la carpeta temporal: un experimento nuevo guarda sus artefactos en ./mlruns
    salida = subprocess.run(
        [sys.executable, "-c", GUION], cwd=tmp_path, env=entorno, capture_output=True, text=True, timeout=300
    )
    assert salida.returncode == 0, salida.stderr[-3000:]
    trazas = json.loads(salida.stdout.strip().splitlines()[-1])

    assert len(trazas) == 1, trazas  # la consulta queda dentro de la traza del turno, no en otra
    traza = trazas[0]
    assert traza["estado"] == "OK", traza
    assert traza["raices"] == ["LangGraph"], traza
    assert {"preparar", "reglamento", "buscar", "verificar", "registrar"} <= set(traza["spans"]), traza
