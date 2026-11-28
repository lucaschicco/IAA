"""Grafo de LangGraph de una consulta con citas verificadas.

    START → buscar ─┬→ extraer → verificar ─┬→ redactar → END
                    │                       └→ END        (ninguna cita verificada: deriva)
                    └→ derivar ─────────────────────────→ END   (sin resultados)

- `buscar` no usa el modelo: Postgres devuelve las k secciones más parecidas.
- `extraer` (modelo) copia de 1 a 3 fragmentos textuales de las páginas que respondan la pregunta.
- `verificar` (código) se queda con las citas que aparecen textualmente en las páginas de su fuente. Una cita
  mala se descarta sin tirar la consulta; si no queda ninguna, se deriva: el sistema no responde sin respaldo.
- `redactar` (modelo) escribe la respuesta corta viendo solo la pregunta y las citas verificadas, no las páginas:
  lo que no está en una cita no lo puede usar. El vecino ve la respuesta y, debajo, las citas reales.
  Con `validar_respuesta` también revisa en código los montos y números del texto libre y calcula la página:
  lo piden el agente y la evaluación. Telegram está congelado y sigue sin esa revisión.
"""
from __future__ import annotations

import time
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import config
from ..ingesta.skill import cargar_skill, version_skill
from ..llm import Contador, invocar_estructurado
from .buscador import (
    armar_contexto,
    buscar_secciones,
    pagina_de_cita,
    texto_de_fuentes,
    verificar_cita,
    verificar_respuesta,
)
from .esquemas import MAX_CITAS, CitasExtraidas, RespuestaRedactada

MENSAJE_DERIVACION = (
    "No encuentro respaldo suficiente en los documentos del consorcio para responder con seguridad. "
    "Derivo la consulta a la administración."
)


class EstadoConsulta(TypedDict, total=False):
    pregunta: str
    consorcio_id: str  # base en la que se busca
    k: int
    metodo_busqueda: str
    validar_respuesta: bool
    resultados: list[dict]
    contexto: str
    extraidas: dict  # CitasExtraidas.model_dump()
    citas: list[dict]  # las verificadas, con su documento
    respuesta: dict  # respuesta, y fuente, pagina y cita de la primera cita; todas en `citas`
    verificada: bool
    derivada: bool
    motivo: str
    mensaje: str  # lo que ve el usuario
    fuente: dict | None  # la sección de la primera cita
    costo: dict
    segundos: float
    _costo_usd: float | None  # `costo` lo trae redondeado a 4 decimales, para mostrar
    _contador: Contador
    _paginas_por_fuente: dict
    _t0: float


def nodo_buscar(estado: EstadoConsulta) -> dict:
    resultados = buscar_secciones(
        estado["pregunta"], estado.get("k", 3), estado.get("metodo_busqueda", "tsvector"), estado["consorcio_id"]
    )
    contexto, paginas = armar_contexto(resultados, estado["consorcio_id"])
    return {
        "resultados": resultados, "contexto": contexto, "_paginas_por_fuente": paginas,
        "_contador": Contador(consorcio_id=estado["consorcio_id"]), "_t0": time.perf_counter(),
    }


def hay_resultados(estado: EstadoConsulta) -> Literal["extraer", "derivar"]:
    return "extraer" if estado["resultados"] else "derivar"


def nodo_extraer(estado: EstadoConsulta) -> dict:
    usuario = f"Pregunta del vecino: {estado['pregunta']}\n\nFuentes:\n\n{estado['contexto']}"
    resp = invocar_estructurado(
        estado["_contador"], "extraer", cargar_skill("extraer", "consulta"), usuario, CitasExtraidas
    )
    return {"extraidas": resp.model_dump()}


def nodo_verificar(estado: EstadoConsulta) -> dict:
    e = estado["extraidas"]
    if not e.get("puede_responder") or not e.get("citas"):
        return _derivar(estado, "el modelo no encontró respaldo en las fuentes")
    citas, motivos = [], []
    for c in e["citas"][:MAX_CITAS]:
        paginas = estado["_paginas_por_fuente"].get(c.get("fuente") or -1, [])
        ok, motivo = verificar_cita(c.get("cita"), paginas)
        if not ok:
            motivos.append(motivo)
            continue
        pagina = pagina_de_cita(c["cita"], paginas, c.get("pagina")) if estado.get("validar_respuesta") else c.get("pagina")
        r = estado["resultados"][c["fuente"] - 1]
        citas.append({**c, "pagina": pagina, "documento": r["documento"], "titulo": r["titulo"],
                      "nombre_archivo": r.get("nombre_archivo"), "fecha_documento": r.get("fecha_documento")})
    if not citas:
        return _derivar(estado, f"cita no verificada: {motivos[0]}")
    return {"citas": citas}


def hay_citas(estado: EstadoConsulta) -> Literal["redactar", "__end__"]:
    return "redactar" if estado.get("citas") and not estado.get("derivada") else END


def _con_pagina(c: dict) -> str:
    return f", página {c['pagina']}" if c.get("pagina") else ""


def nodo_redactar(estado: EstadoConsulta) -> dict:
    citas = estado["citas"]
    bloques = "\n\n".join(
        f"[Cita {i}] {c['documento']} — {c['titulo']} (fecha {c.get('fecha_documento') or 'sin fecha'}{_con_pagina(c)}):"
        f"\n«{c['cita']}»" for i, c in enumerate(citas, start=1)
    )
    usuario = f"Pregunta del vecino: {estado['pregunta']}\n\nCitas verificadas:\n\n{bloques}"
    redactada = invocar_estructurado(
        estado["_contador"], "redactar", cargar_skill("redactar", "consulta"), usuario, RespuestaRedactada
    )
    principal = citas[0]
    r = {"puede_responder": True, "respuesta": redactada.respuesta, "fuente": principal["fuente"],
         "pagina": principal.get("pagina"), "cita": principal["cita"], "citas": citas}
    motivo = "citas encontradas en el texto original"
    if estado.get("validar_respuesta"):
        fuentes = texto_de_fuentes(estado["resultados"], estado["_paginas_por_fuente"])
        ok, motivo = verificar_respuesta(
            r["respuesta"], " ".join(c["cita"] for c in citas), fuentes, estado["pregunta"]
        )
        if not ok:
            return {**_derivar(estado, motivo), "respuesta": r}
    pie = "\n".join(f"Fuente: {c['documento']} — {c['titulo']}{_con_pagina(c)}.\nCita: «{c['cita']}»" for c in citas)
    return {**_cierre(estado), "respuesta": r, "verificada": True, "derivada": False, "motivo": motivo,
            "mensaje": f"{r['respuesta']}\n\n{pie}", "fuente": estado["resultados"][principal["fuente"] - 1]}


def nodo_derivar(estado: EstadoConsulta) -> dict:
    return _derivar(estado, "la búsqueda no encontró secciones relacionadas")


def _derivar(estado: EstadoConsulta, motivo: str) -> dict:
    return {**_cierre(estado), "verificada": False, "derivada": True, "motivo": motivo,
            "mensaje": MENSAJE_DERIVACION, "fuente": None}


def _cierre(estado: EstadoConsulta) -> dict:
    contador: Contador = estado.get("_contador") or Contador()
    t0 = estado.get("_t0") or time.perf_counter()
    return {"costo": contador.resumen(), "_costo_usd": contador.costo_total(), "segundos": round(time.perf_counter() - t0, 2)}


def construir_grafo():
    g = StateGraph(EstadoConsulta)
    g.add_node("buscar", nodo_buscar)
    g.add_node("extraer", nodo_extraer)
    g.add_node("verificar", nodo_verificar)
    g.add_node("redactar", nodo_redactar)
    g.add_node("derivar", nodo_derivar)
    g.add_edge(START, "buscar")
    g.add_conditional_edges("buscar", hay_resultados, ["extraer", "derivar"])
    g.add_edge("extraer", "verificar")
    g.add_conditional_edges("verificar", hay_citas, ["redactar", END])
    g.add_edge("redactar", END)
    g.add_edge("derivar", END)
    return g.compile()


def preguntar(
    pregunta: str, k: int = 3, metodo_busqueda: str = "tsvector", sin_persistir: bool = False, origen: str = "cli",
    consorcio_id: str | None = None, usuario_id: int | None = None, validar_respuesta: bool = False,
) -> EstadoConsulta:
    """Punto de entrada: responde una pregunta con los documentos del consorcio (obligatorio), la traza en
    MLflow y la guarda en `consultas`, con el usuario si se conoce."""
    from ..almacen.postgres import exigir_consorcio
    from ..observabilidad import trazar_corrida

    consorcio_id = exigir_consorcio(consorcio_id)
    grafo = construir_grafo()
    etiquetas = {"metodo_busqueda": metodo_busqueda, "k": k, "origen": origen, "consorcio": consorcio_id}
    with trazar_corrida("consulta", etiquetas) as traza:
        final = grafo.invoke({"pregunta": pregunta, "k": k, "metodo_busqueda": metodo_busqueda,
                              "consorcio_id": consorcio_id, "validar_respuesta": validar_respuesta})
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
                "pregunta": pregunta, "consorcio_id": consorcio_id, "respuesta": r.get("respuesta"),
                "cita": "\n\n".join(c["cita"] for c in r.get("citas", [])) or r.get("cita"),
                "documento_id": fuente.get("documento_id"), "seccion_id": fuente.get("seccion_id"),
                "pagina": r.get("pagina"), "verificada": bool(final.get("verificada")),
                "derivada": bool(final.get("derivada")), "motivo": final.get("motivo"),
                "metodo_busqueda": metodo_busqueda, "k": k, "proveedor": config.llm_provider,
                "modelo": config.modelo, "skill_version": version_skill("consulta"),
                "tokens_entrada": costo.get("tokens_entrada", 0), "tokens_salida": costo.get("tokens_salida", 0),
                "costo_usd": costo.get("costo_usd"), "segundos": final.get("segundos", 0.0), "origen": origen,
                "usuario_id": usuario_id,
            }
        )
    return final
