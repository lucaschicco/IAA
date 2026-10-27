"""Grafo de LangGraph de una consulta con cita verificada.

    START → buscar ─┬→ responder → verificar → END
                    └→ derivar ──────────────→ END   (sin resultados)

- `buscar` no usa el modelo: Postgres devuelve las k secciones más parecidas.
- `responder` hace UNA llamada con salida estructurada (RespuestaConCita).
- `verificar` comprueba en código que la cita exista textualmente en las páginas
  originales de la fuente. Si no existe, o el modelo dijo que no puede responder,
  la consulta se deriva a la administración: el sistema no responde sin respaldo.
"""
from __future__ import annotations

import time
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import config
from ..ingesta.skill import cargar_skill, version_skill
from ..llm import Contador, invocar_estructurado
from .buscador import armar_contexto, buscar_secciones, verificar_cita
from .esquemas import RespuestaConCita

MENSAJE_DERIVACION = (
    "No encuentro respaldo suficiente en los documentos del consorcio para responder con seguridad. "
    "Derivo la consulta a la administración."
)


class EstadoConsulta(TypedDict, total=False):
    pregunta: str
    consorcio_id: str  # base en la que se busca
    k: int
    metodo_busqueda: str
    resultados: list[dict]
    contexto: str
    respuesta: dict  # RespuestaConCita.model_dump()
    verificada: bool
    derivada: bool
    motivo: str
    mensaje: str  # lo que ve el usuario
    fuente: dict | None  # la sección citada
    costo: dict
    segundos: float
    _contador: Contador
    _paginas_por_fuente: dict
    _t0: float


def nodo_buscar(estado: EstadoConsulta) -> dict:
    resultados = buscar_secciones(
        estado["pregunta"], estado.get("k", 3), estado.get("metodo_busqueda", "tsvector"), estado.get("consorcio_id")
    )
    contexto, paginas = armar_contexto(resultados)
    return {
        "resultados": resultados, "contexto": contexto, "_paginas_por_fuente": paginas,
        "_contador": Contador(), "_t0": time.perf_counter(),
    }


def hay_resultados(estado: EstadoConsulta) -> Literal["responder", "derivar"]:
    return "responder" if estado["resultados"] else "derivar"


def nodo_responder(estado: EstadoConsulta) -> dict:
    usuario = f"Pregunta del vecino: {estado['pregunta']}\n\nFuentes:\n\n{estado['contexto']}"
    resp = invocar_estructurado(
        estado["_contador"], "responder", cargar_skill("responder", "consulta"), usuario, RespuestaConCita
    )
    return {"respuesta": resp.model_dump()}


def nodo_verificar(estado: EstadoConsulta) -> dict:
    r = estado["respuesta"]
    if not r.get("puede_responder"):
        return _derivar(estado, "el modelo no encontró respaldo en las fuentes")
    fuente_n = r.get("fuente")
    paginas = estado["_paginas_por_fuente"].get(fuente_n or -1, [])
    ok, motivo = verificar_cita(r.get("cita"), paginas)
    if not ok:
        return _derivar(estado, f"cita no verificada: {motivo}")
    fuente = estado["resultados"][fuente_n - 1]
    pagina = r.get("pagina")
    mensaje = (
        f"{r['respuesta']}\n\n"
        f"Fuente: {fuente['documento']} — {fuente['titulo']}"
        f"{f', página {pagina}' if pagina else ''}.\n"
        f"Cita: «{r['cita']}»"
    )
    return {**_cierre(estado), "verificada": True, "derivada": False, "motivo": motivo, "mensaje": mensaje, "fuente": fuente}


def nodo_derivar(estado: EstadoConsulta) -> dict:
    return _derivar(estado, "la búsqueda no encontró secciones relacionadas")


def _derivar(estado: EstadoConsulta, motivo: str) -> dict:
    return {**_cierre(estado), "verificada": False, "derivada": True, "motivo": motivo,
            "mensaje": MENSAJE_DERIVACION, "fuente": None}


def _cierre(estado: EstadoConsulta) -> dict:
    contador: Contador = estado.get("_contador") or Contador()
    t0 = estado.get("_t0") or time.perf_counter()
    return {"costo": contador.resumen(), "segundos": round(time.perf_counter() - t0, 2)}


def construir_grafo():
    g = StateGraph(EstadoConsulta)
    g.add_node("buscar", nodo_buscar)
    g.add_node("responder", nodo_responder)
    g.add_node("verificar", nodo_verificar)
    g.add_node("derivar", nodo_derivar)
    g.add_edge(START, "buscar")
    g.add_conditional_edges("buscar", hay_resultados, ["responder", "derivar"])
    g.add_edge("responder", "verificar")
    g.add_edge("verificar", END)
    g.add_edge("derivar", END)
    return g.compile()


def preguntar(
    pregunta: str, k: int = 3, metodo_busqueda: str = "tsvector", sin_persistir: bool = False, origen: str = "cli",
    consorcio_id: str | None = None,
) -> EstadoConsulta:
    """Punto de entrada: responde una pregunta con los documentos del consorcio, la traza en MLflow y la
    guarda en `consultas`."""
    from ..observabilidad import trazar_corrida

    grafo = construir_grafo()
    consorcio_id = consorcio_id or config.consorcio_por_defecto
    etiquetas = {"metodo_busqueda": metodo_busqueda, "k": k, "origen": origen, "consorcio": consorcio_id}
    with trazar_corrida("consulta", etiquetas) as traza:
        final = grafo.invoke({"pregunta": pregunta, "k": k, "metodo_busqueda": metodo_busqueda,
                              "consorcio_id": consorcio_id})
        costo = final.get("costo", {})
        traza.registrar_metricas(
            {"derivada": str(final.get("derivada")), "verificada": str(final.get("verificada"))},
            {
                "resultados": len(final.get("resultados", [])),
                "verificada": float(bool(final.get("verificada"))),
                "derivada": float(bool(final.get("derivada"))),
                "segundos": final.get("segundos", 0.0),
                **{k_: float(v) for k_, v in costo.items() if v is not None},
            },
        )
    if not sin_persistir:
        from ..almacen.postgres import guardar_consulta

        r = final.get("respuesta", {})
        fuente = final.get("fuente") or {}
        costo = final.get("costo", {})
        final["consulta_id"] = guardar_consulta(
            {
                "pregunta": pregunta, "consorcio_id": consorcio_id, "respuesta": r.get("respuesta"), "cita": r.get("cita"),
                "documento_id": fuente.get("documento_id"), "seccion_id": fuente.get("seccion_id"),
                "pagina": r.get("pagina"), "verificada": bool(final.get("verificada")),
                "derivada": bool(final.get("derivada")), "motivo": final.get("motivo"),
                "metodo_busqueda": metodo_busqueda, "k": k, "proveedor": config.llm_provider,
                "modelo": config.modelo, "skill_version": version_skill("consulta"),
                "tokens_entrada": costo.get("tokens_entrada", 0), "tokens_salida": costo.get("tokens_salida", 0),
                "costo_usd": costo.get("costo_usd"), "segundos": final.get("segundos", 0.0), "origen": origen,
            }
        )
    return final
