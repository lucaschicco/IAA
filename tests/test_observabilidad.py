"""Trazas de MLflow de verdad: lo que los tests de `test_agente.py` no pueden ver con un MLflow de mentira.

- 16/11: la traza de un turno de reglamento quedaba "In progress", sin el span raíz ni el nodo
  `reglamento`: la consulta anidada volvía a llamar a `mlflow.set_experiment`, que reinicia el proveedor
  de trazas con la traza del turno todavía abierta.
- 18/11: `turnos.mlflow_traza_id` y las etiquetas de la consulta anidada iban a la traza del turno
  anterior: `get_last_active_trace_id` se actualiza recién cuando termina el span raíz.

Usa MLflow real, con sqlite en una carpeta temporal y nunca el del proyecto. Cada guion corre en otro
proceso porque el autolog de LangChain y el destino de MLflow son globales: así no se filtran a los
demás tests, que corren con MLflow apagado (`conftest.py`)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import yaml
from packaging.requirements import Requirement
from packaging.version import Version

RAIZ = Path(__file__).resolve().parents[1]

# Grafos de LangGraph de juguete y un resumen de las trazas guardadas, que el guion imprime como última línea
BASE = """
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


def consulta(n):
    def reglamento(estado):
        with trazar_corrida("consulta", {"origen": f"consulta-{n}"}):
            final = grafo(("buscar", paso("buscar")), ("verificar", paso("verificar"))).invoke(estado)
        return {"texto": final["texto"]}
    return reglamento


def trazas():
    experimento = mlflow.get_experiment_by_name(config.mlflow_experiment)
    return [
        {
            "id": t.info.trace_id,
            "estado": str(getattr(t.info.state, "value", t.info.state)),
            "raices": [s.name for s in t.data.spans if s.parent_id is None],
            "entrada": next((s.inputs for s in t.data.spans if s.parent_id is None), None),
            "spans": sorted(s.name for s in t.data.spans),
            "etiquetas": dict(t.info.tags),
            "metadata": dict(t.info.trace_metadata),
        }
        for t in mlflow.search_traces(locations=[experimento.experiment_id], return_type="list", flush=True)
    ]
"""

# Como un turno del agente: el nodo `reglamento` abre su propia corrida (la consulta) y corre otro grafo
# adentro, con la traza del turno abierta
TURNO_CON_CONSULTA = """
turno = grafo(("preparar", paso("preparar")), ("reglamento", consulta(1)), ("registrar", paso("registrar")))
with trazar_corrida("agente", {"canal": "prueba"}):
    turno.invoke({"texto": "hola"})
print(json.dumps(trazas()))
"""

# Dos turnos seguidos, como `nodo_registrar`: anota el id de la traza desde adentro. Después, una
# corrida suelta (como una ingesta), que se etiqueta cuando su traza ya terminó.
DOS_TURNOS_Y_UNA_INGESTA = """
anotados = {}


def registro(n, traza):
    def registrar(estado):
        anotados[str(n)] = traza.traza_id()
        return {"texto": f"{estado['texto']} registrar"}
    return registrar


for n in (1, 2):
    with trazar_corrida("agente", {"canal": "prueba", "turno": str(n)}) as traza:
        turno = grafo(("preparar", paso("preparar")), ("reglamento", consulta(n)), ("registrar", registro(n, traza)))
        turno.invoke({"texto": f"turno {n}"})
with trazar_corrida("ingesta", {"origen": "ingesta"}):
    grafo(("clasificar", paso("clasificar"))).invoke({"texto": "pdf"})
print(json.dumps({"anotados": anotados, "trazas": trazas()}))
"""

# Tres turnos de dos conversaciones: `registrar` asocia la traza a la conversación y al usuario
TRES_TURNOS_EN_DOS_CONVERSACIONES = """
def registro(conversacion, usuario, traza):
    def registrar(estado):
        traza.asociar_conversacion(conversacion, usuario)
        return {"texto": f"{estado['texto']} registrar"}
    return registrar


for n, conversacion, usuario in ((1, "conv-a", 7), (2, "conv-a", 7), (3, "conv-b", 8)):
    with trazar_corrida("agente", {"canal": "prueba"}) as traza:
        grafo(("preparar", paso("preparar")), ("registrar", registro(conversacion, usuario, traza))).invoke(
            {"texto": f"turno {n}"}
        )
print(json.dumps(trazas()))
"""


def _correr(guion: str, tmp_path: Path):
    entorno = {
        **os.environ,
        "MLFLOW_TRACKING_URI": f"sqlite:///{tmp_path / 'mlflow.db'}",
        "MLFLOW_EXPERIMENT": "prueba-trazas",
        "PYTHONPATH": os.pathsep.join(filter(None, [str(RAIZ / "src"), os.environ.get("PYTHONPATH")])),
    }
    # En la carpeta temporal: un experimento nuevo guarda sus artefactos en ./mlruns
    salida = subprocess.run(
        [sys.executable, "-c", BASE + guion], cwd=tmp_path, env=entorno, capture_output=True, text=True,
        timeout=300,
    )
    assert salida.returncode == 0, salida.stderr[-3000:]
    return json.loads(salida.stdout.strip().splitlines()[-1])


def _por_entrada(trazas: list[dict]) -> dict[str, dict]:
    return {(t["entrada"] or {}).get("texto"): t for t in trazas}


def test_la_consulta_anidada_deja_completa_la_traza_del_turno(tmp_path):
    trazas = _correr(TURNO_CON_CONSULTA, tmp_path)

    assert len(trazas) == 1, trazas  # la consulta queda dentro de la traza del turno, no en otra
    traza = trazas[0]
    assert traza["estado"] == "OK", traza
    assert traza["raices"] == ["LangGraph"], traza
    assert {"preparar", "reglamento", "buscar", "verificar", "registrar"} <= set(traza["spans"]), traza


def test_cada_turno_anota_y_etiqueta_su_propia_traza(tmp_path):
    salida = _correr(DOS_TURNOS_Y_UNA_INGESTA, tmp_path)
    trazas = _por_entrada(salida["trazas"])
    assert set(trazas) == {"turno 1", "turno 2", "pdf"}, salida["trazas"]

    for n in ("1", "2"):
        traza = trazas[f"turno {n}"]
        # Lo que `turnos.mlflow_traza_id` guarda: la traza de este turno, no la del anterior
        assert salida["anotados"][n] == traza["id"], (n, salida["anotados"], traza["id"])
        assert traza["etiquetas"].get("turno") == n, traza["etiquetas"]
        # La consulta anidada etiqueta la traza de su turno
        assert traza["etiquetas"].get("origen") == f"consulta-{n}", traza["etiquetas"]
    assert trazas["pdf"]["etiquetas"].get("origen") == "ingesta", trazas["pdf"]["etiquetas"]


def test_la_traza_del_turno_dice_de_que_conversacion_y_de_que_usuario_es(tmp_path):
    """Lo que usa la vista Sessions de MLflow para mostrar una conversación completa."""
    trazas = _por_entrada(_correr(TRES_TURNOS_EN_DOS_CONVERSACIONES, tmp_path))

    asociadas = {
        texto: (t["metadata"].get("mlflow.trace.session"), t["metadata"].get("mlflow.trace.user"))
        for texto, t in trazas.items()
    }
    assert asociadas == {"turno 1": ("conv-a", "7"), "turno 2": ("conv-a", "7"), "turno 3": ("conv-b", "8")}


def test_mlflow_se_publica_solo_en_la_maquina_local():
    """Sin login y con textos de todos los consorcios (docs/observabilidad.md): hasta el 20/11 cualquiera
    en la misma red podía abrirlo. Adentro escucha en todas las interfaces para que la app lo alcance."""
    mlflow = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["mlflow"]
    assert mlflow["ports"] == ["127.0.0.1:5000:5000"]
    assert "--host 0.0.0.0 --port 5000" in mlflow["command"]


def test_mlflow_tiene_version_fija():
    """Los arreglos del 17/11 y del 18/11 dependen de cómo se comporta MLflow 3.18, sobre todo la librería
    que corre dentro de la app. Con la imagen en `latest` y la librería en `>=3.0`, un Codespace nuevo podía
    bajar otra versión. Servidor y librería quedan en la misma versión."""
    imagen = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["mlflow"]["image"]
    nombre, _, etiqueta = imagen.rpartition(":")
    assert nombre == "ghcr.io/mlflow/mlflow"
    assert re.fullmatch(r"v\d+\.\d+\.\d+", etiqueta), imagen
    version = Version(etiqueta.removeprefix("v"))
    dependencias = tomllib.loads((RAIZ / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    libreria = next(r for r in map(Requirement, dependencias) if r.name == "mlflow-skinny")
    assert libreria.specifier.contains(version), libreria
    assert not libreria.specifier.contains(f"{version.major}.{version.minor + 1}.0"), libreria
