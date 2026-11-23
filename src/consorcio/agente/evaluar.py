"""`consorcio evaluar-agente`: el set de `datos/evaluacion/mensajes_agente.csv` por el agente completo.

Spec: `docs/evaluaciones/2026-11-23-spec-evaluar-agente.md`. Plan, hipótesis y reglas:
`docs/evaluaciones/2026-11-21-plan-agente.md`.

Cada mensaje pasa por `atender`, igual que uno de la web, con dos routers: el del modelo y uno por palabras
clave (el baseline). El resto del grafo es el mismo, así que la diferencia mide solo el router. Lo medido
sale de la fila de `turnos` de cada mensaje y, si MLflow está prendido, de la duración del paso `router`
en su traza.

Solo corre sobre los datos inventados de `sembrar-demo`: el detalle (mensajes y respuestas) no tiene datos
personales y se puede guardar en el repo.
"""
from __future__ import annotations

import contextlib
import csv
import json
import logging
import math
import re
import statistics
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..almacen.postgres import Consorcio, Turno, Usuario, crear_tablas, engine
from ..config import PRECIOS_USD_POR_MILLON, RAIZ_REPO, config
from ..identidad import SinMembresia, resolver_identidad
from ..observabilidad import trazar_corrida
from ..texto import normalizar
from . import grafo
from .esquemas import SalidaRouter
from .semilla import CONSORCIOS

log = logging.getLogger("consorcio.evaluar_agente")

ROUTERS = ("modelo", "palabras")
DIR_EVALUACIONES = RAIZ_REPO / "docs" / "evaluaciones"
# Los resultados que dejan una solicitud en la bandeja: trabajo para una persona
CON_SOLICITUD = {"derivada", "reclamo_pendiente"}
# Para estimar el costo cuando el modelo no tiene precio (Ollama gratuito)
MODELOS_ESTIMADOS = ("claude-haiku-5-5", "gemini-2.5-flash")
MENSAJE_FALTA_DEMO = "Faltan los consorcios o los usuarios de la demo: corré `consorcio sembrar-demo` (make sembrar-demo)."
MENSAJE_TEST = ("La partición test se corre una sola vez y a propósito (regla 5 del plan): agregá --test-final "
                "(make evaluar-agente PARTICION=test TEST_FINAL=1).")


class EvaluacionNoPermitida(ValueError):
    """La corrida no arranca: falta la demo, el set no es de la demo o es test sin --test-final."""


# ------------------------------------------------------------------ router por palabras clave (baseline)

# Las mismas reglas de `guion_fake._intencion` (la primera que coincide gana; sin coincidencia, reglamento).
# Copiadas y no importadas: el baseline queda fijo aunque cambie el guion de los tests. Se ajustan solo
# mirando dev, y cada cambio se anota en docs/bitacora.md.
REGLAS_PALABRAS: list[tuple[tuple[str, ...], str]] = [
    (("expensa", "pagar", "gasto", "rubro"), "expensas"),
    (("documento",), "documentos"),
    (("reclamo", "roto", "perdida"), "reclamo"),
    (("humano", "persona", "administracion"), "derivar"),
    (("clima", "futbol"), "fuera_de_alcance"),
]
MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
         "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_PERIODO = re.compile(r"\b(\d{4})-(\d{2})\b")
_UNIDAD = re.compile(r"\b(\d+-[A-Za-z])\b|\bUF\s*(\d+)\b", re.IGNORECASE)
_PROPIA = re.compile(r"\b(mi|mis|debo|pago|pague|me vino|tengo que pagar)\b")


def _periodo(mensaje: str, n: str, hoy: date) -> str | None:
    """`2026-08` tal cual; un mes con nombre, el último con ese nombre contando el actual."""
    m = _PERIODO.search(mensaje)
    if m:
        return m.group(0)
    for nombre, numero in MESES.items():
        if re.search(rf"\b{nombre}\b", n):
            return f"{hoy.year if numero <= hoy.month else hoy.year - 1}-{numero:02d}"
    return None


def router_palabras(mensaje: str, hoy: date | None = None) -> SalidaRouter:
    """Sin modelo y sin memoria: solo mira el mensaje actual."""
    n = normalizar(mensaje)
    intencion = next((i for palabras, i in REGLAS_PALABRAS if any(p in n for p in palabras)), "reglamento")
    que = next((q for palabra, q in (("rubro", "rubros"), ("gasto", "gastos"), ("unidad", "unidades")) if palabra in n),
               "resumen")
    unidad = _UNIDAD.search(mensaje)
    return SalidaRouter(
        intencion=intencion, pregunta_reescrita=mensaje, que=que,
        periodo=_periodo(mensaje, n, hoy or date.today()),
        unidad=(unidad.group(1) or unidad.group(2)).upper() if unidad else None,
        propia=bool(_PROPIA.search(n)), titulo=mensaje[:120], detalle=mensaje[:2000],
    )


def nodo_router_palabras(estado: dict) -> dict:
    """Misma salida que `grafo.nodo_router`, sin llamar al modelo."""
    salida = router_palabras(estado["entrada"])
    return {"intencion": salida.intencion, "_destino": salida.intencion, "salida_router": salida.model_dump()}


@contextlib.contextmanager
def _con_router(cual: str):
    """Con `palabras`, el grafo usa el router por palabras clave mientras dure el bloque. `atender` arma el
    grafo en cada turno y toma `grafo.nodo_router` en ese momento; al salir se repone el original."""
    if cual == "modelo":
        yield
        return
    original = grafo.nodo_router
    grafo.nodo_router = nodo_router_palabras
    try:
        yield
    finally:
        grafo.nodo_router = original


# ------------------------------------------------------------------ set y controles previos


def leer_mensajes(ruta_csv: str | Path, particion: str) -> list[dict]:
    with open(ruta_csv, encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f) if r["particion"] == particion]


def _verificar(filas: list[dict], particion: str, test_final: bool) -> dict[str, object]:
    """Antes de crear un solo turno: test pide --test-final, el set tiene que ser de la demo y la demo tiene
    que estar sembrada. Devuelve la identidad de cada fila."""
    if particion == "test" and not test_final:
        raise EvaluacionNoPermitida(MENSAJE_TEST)
    ajenos = sorted({f["consorcio"] for f in filas} - set(CONSORCIOS))
    if ajenos:
        raise EvaluacionNoPermitida(f"El set solo puede usar los consorcios de la demo; tiene {', '.join(ajenos)}.")
    consorcios, usuarios = {f["consorcio"] for f in filas}, {f["usuario"] for f in filas}
    crear_tablas()
    with Session(engine()) as s:
        hay_consorcios = set(s.scalars(select(Consorcio.id).where(Consorcio.id.in_(consorcios))))
        hay_usuarios = set(s.scalars(select(Usuario.sujeto_externo).where(Usuario.sujeto_externo.in_(usuarios))))
    if hay_consorcios != consorcios or hay_usuarios != usuarios:
        raise EvaluacionNoPermitida(MENSAJE_FALTA_DEMO)
    identidades = {}
    for f in filas:
        try:
            identidades[f["id"]] = resolver_identidad(f["usuario"], f["consorcio"])
        except SinMembresia as e:
            raise EvaluacionNoPermitida(f"Fila {f['id']}: {f['usuario']} no tiene membresía en {f['consorcio']}.") from e
    return identidades


# ------------------------------------------------------------------ corrida


def _turno(turno_id: int | None) -> Turno | None:
    if turno_id is None:
        return None
    with Session(engine()) as s:
        return s.get(Turno, turno_id)


def _comparar(fila: dict, respuesta: dict, turno: Turno | None, cual: str) -> dict:
    intencion = turno.intencion if turno else respuesta.get("intencion")
    resultado = turno.resultado if turno else respuesta.get("resultado")
    con_solicitud = bool(turno.solicitud_id) if turno else bool(respuesta.get("solicitud_id"))
    esperaba_solicitud = fila["resultado_esperado"] in CON_SOLICITUD
    debe = (fila.get("debe_contener") or "").strip()
    return {
        "id": fila["id"], "router": cual, "camino": fila["intencion_esperada"], "estilo": fila["estilo"],
        "mensaje": fila["mensaje"], "respuesta": respuesta.get("mensaje"),
        "intencion": intencion, "resultado": resultado,
        "acierta_intencion": intencion == fila["intencion_esperada"],
        "acierta_resultado": resultado == fila["resultado_esperado"],
        "contiene": (normalizar(debe) in normalizar(respuesta.get("mensaje") or "")) if debe else None,
        "derivacion_de_mas": con_solicitud and not esperaba_solicitud,
        "derivacion_de_menos": esperaba_solicitud and not con_solicitud,
        "tokens_entrada": turno.tokens_entrada if turno else 0, "tokens_salida": turno.tokens_salida if turno else 0,
        "costo_usd": turno.costo_usd if turno else None, "segundos": turno.segundos if turno else None,
        "segundos_router": None, "turno_id": respuesta.get("turno_id"),
        "mlflow_traza_id": turno.mlflow_traza_id if turno else None,
    }


def _segundos_router(detalle: list[dict]) -> None:
    """Duración del paso `router` en la traza de cada mensaje. Sin MLflow queda en None."""
    if not config.mlflow_tracking_uri or not any(d["mlflow_traza_id"] for d in detalle):
        return
    try:
        import mlflow

        mlflow.flush_trace_async_logging()
    except Exception as e:
        log.debug("no se pudieron vaciar las trazas pendientes: %s", e)
        return
    for d in detalle:
        if not d["mlflow_traza_id"]:
            continue
        try:
            traza = mlflow.get_trace(d["mlflow_traza_id"], silent=True)
            span = next((s for s in traza.data.spans if s.name == "router"), None) if traza else None
            if span and span.end_time_ns:
                d["segundos_router"] = round((span.end_time_ns - span.start_time_ns) / 1e9, 3)
        except Exception as e:
            log.debug("no se pudo leer la traza %s: %s", d["mlflow_traza_id"], e)


def _correr(filas: list[dict], identidades: dict, cual: str) -> list[dict]:
    conversaciones: dict[str, str] = {}  # columna `conversacion` -> id de la conversación creada
    detalle = []
    with _con_router(cual):
        for fila in filas:
            clave = (fila.get("conversacion") or "").strip()
            respuesta = grafo.atender(fila["mensaje"], identidades[fila["id"]], conversaciones.get(clave), "cli",
                                      origen="evaluacion")
            if clave and respuesta.get("conversacion_id"):
                conversaciones[clave] = respuesta["conversacion_id"]
            detalle.append(_comparar(fila, respuesta, _turno(respuesta.get("turno_id")), cual))
    _segundos_router(detalle)
    return detalle


# ------------------------------------------------------------------ resumen


def _p95(valores: list[float]) -> float | None:
    """Por rango más cercano: con pocos mensajes es el máximo o casi, y no inventa un valor intermedio."""
    if not valores:
        return None
    ordenados = sorted(valores)
    return ordenados[max(math.ceil(0.95 * len(ordenados)) - 1, 0)]


def _mediana(valores: list[float]) -> float | None:
    return statistics.median(valores) if valores else None


def _aciertos(filas: list[dict], campo: str) -> dict:
    return {"aciertos": sum(1 for f in filas if f[campo]), "n": len(filas)}


def _costo_estimado(filas: list[dict], modelo: str) -> float:
    precio_entrada, precio_salida = PRECIOS_USD_POR_MILLON[modelo]
    return sum(f["tokens_entrada"] * precio_entrada + f["tokens_salida"] * precio_salida for f in filas) / 1_000_000


def resumir(detalle: list[dict]) -> dict:
    segundos = [f["segundos"] for f in detalle if f["segundos"] is not None]
    router = [f["segundos_router"] for f in detalle if f["segundos_router"] is not None]
    tokens = [f["tokens_entrada"] + f["tokens_salida"] for f in detalle]
    costos = [f["costo_usd"] for f in detalle if f["costo_usd"] is not None]
    con_dato = [f for f in detalle if f["contiene"] is not None]
    por_camino = defaultdict(list)
    for f in detalle:
        por_camino[f["camino"]].append(f)
    return {
        "n": len(detalle),
        "intencion": _aciertos(detalle, "acierta_intencion"),
        "resultado": _aciertos(detalle, "acierta_resultado"),
        "contiene": _aciertos(con_dato, "contiene"),
        "por_estilo": {e: {"intencion": _aciertos(fs, "acierta_intencion"), "resultado": _aciertos(fs, "acierta_resultado")}
                       for e in ("directo", "indirecto") if (fs := [f for f in detalle if f["estilo"] == e])},
        "por_camino": {c: {"intencion": _aciertos(fs, "acierta_intencion"), "resultado": _aciertos(fs, "acierta_resultado"),
                           "segundos_mediana": _mediana([f["segundos"] for f in fs if f["segundos"] is not None]),
                           "segundos_maximo": max((f["segundos"] for f in fs if f["segundos"] is not None), default=None)}
                       for c, fs in sorted(por_camino.items())},
        "derivaciones_de_mas": sum(1 for f in detalle if f["derivacion_de_mas"]),
        "derivaciones_de_menos": sum(1 for f in detalle if f["derivacion_de_menos"]),
        "segundos": {"mediana": _mediana(segundos), "p95": _p95(segundos)},
        "segundos_router": {"mediana": _mediana(router), "p95": _p95(router), "n": len(router)},
        "tokens_por_mensaje": {"mediana": _mediana(tokens), "p95": _p95(tokens)},
        "costo_usd": {"total": round(sum(costos), 6) if costos else None, "n_con_precio": len(costos)},
        "costo_estimado_usd": {m: round(_costo_estimado(detalle, m), 6) for m in MODELOS_ESTIMADOS},
    }


def pares_intencion(por_router: dict[str, list[dict]]) -> dict:
    """Mensaje por mensaje: qué router acertó la intención. Con pocos mensajes, esto dice más que los porcentajes."""
    modelo = {f["id"]: f["acierta_intencion"] for f in por_router["modelo"]}
    palabras = {f["id"]: f["acierta_intencion"] for f in por_router["palabras"]}
    pares = {"aciertan_los_dos": [], "solo_modelo": [], "solo_palabras": [], "ninguno": []}
    for id_ in modelo:
        clave = {(True, True): "aciertan_los_dos", (True, False): "solo_modelo",
                 (False, True): "solo_palabras", (False, False): "ninguno"}[(modelo[id_], palabras[id_])]
        pares[clave].append(id_)
    return {k: {"n": len(v), "ids": v} for k, v in pares.items()}


def evaluar_agente(ruta_csv: str | Path, particion: str = "dev", router: str = "ambos", test_final: bool = False) -> dict:
    filas = leer_mensajes(ruta_csv, particion)
    identidades = _verificar(filas, particion, test_final)
    routers = ROUTERS if router == "ambos" else (router,)
    salida: dict = {
        "archivo": Path(ruta_csv).name, "particion": particion, "n": len(filas),
        "proveedor": config.llm_provider, "modelo": config.modelo, "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "routers": {},
    }
    with trazar_corrida("evaluacion-agente", {"particion": particion, "router": router}) as traza:
        detalle = {cual: _correr(filas, identidades, cual) for cual in routers}
        for cual, filas_router in detalle.items():
            salida["routers"][cual] = {**resumir(filas_router), "detalle": filas_router}
        if router == "ambos":
            salida["pares_intencion"] = pares_intencion(detalle)
        metricas = {}
        for cual, r in salida["routers"].items():
            metricas[f"acierto_intencion_{cual}"] = r["intencion"]["aciertos"] / max(r["intencion"]["n"], 1)
            metricas[f"acierto_resultado_{cual}"] = r["resultado"]["aciertos"] / max(r["resultado"]["n"], 1)
            metricas[f"derivaciones_de_mas_{cual}"] = r["derivaciones_de_mas"]
            metricas[f"segundos_mediana_{cual}"] = r["segundos"]["mediana"]
        traza.registrar_metricas({"n_mensajes": str(len(filas))}, metricas)
    return salida


def guardar_evaluacion_agente(salida: dict) -> Path | None:
    """Con un modelo real deja `<fecha>-agente-<particion>.json` en docs/evaluaciones (de un mismo día queda
    la última). Con el modelo de mentira no: sus números no son resultados (regla 7 del plan)."""
    if salida["proveedor"] == "fake":
        return None
    ruta = DIR_EVALUACIONES / f"{datetime.now(UTC).date().isoformat()}-agente-{salida['particion']}.json"
    ruta.write_text(json.dumps(salida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ruta
