"""Nodos del agente: el estado que comparten y una función por cada funcionalidad.

Todos operan sobre `identidad.consorcio_id`. De la salida del router solo se usan los campos de
`SalidaRouter`, ya validados, y los importes de la respuesta salen siempre de SQL.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any, TypedDict

from ..almacen.postgres import consultar_expensas, listar_documentos, periodo_valido
from ..canales.formato import (
    _recortar,
    formatear_documentos,
    formatear_expensas,
    formatear_respuesta,
)
from ..consulta.grafo import preguntar
from ..identidad import Identidad
from ..llm import Contador
from .bandeja import crear_solicitud
from .certeza import senales_expensas, senales_reglamento

log = logging.getLogger("consorcio.agente")

MENSAJE_ERROR = "No pude completar el pedido por un problema interno. Probá de nuevo en un rato."
MENSAJE_FALLO_DERIVADO = "No pude resolverlo y lo derivé a la administración."
MENSAJE_DERIVADO = "Derivé tu consulta a la administración. Vas a ver la respuesta en tus solicitudes."
MENSAJE_FUERA_DE_ALCANCE = "Eso queda fuera de lo que puedo resolver: solo ayudo con temas del consorcio."
MENSAJE_SIN_EXPENSAS = "No encontré liquidaciones que coincidan con lo que pediste."
MENSAJE_SIN_UNIDADES = "Tu usuario no tiene unidades asociadas en este consorcio, así que no puedo saber cuál es la tuya."
MENSAJE_SIN_DOCUMENTOS = "Este consorcio todavía no tiene documentos cargados."


class EstadoAgente(TypedDict, total=False):
    entrada: str  # el mensaje del usuario
    identidad: Identidad
    conversacion_id: str | None  # validada; None hasta que `registrar` crea la conversación nueva
    canal: str
    origen: str
    resultado: str  # vocabulario de `turnos.resultado`
    respuesta: str  # lo que ve el usuario
    intencion: str | None
    funcionalidad: str | None
    motivo: str  # de la solicitud que se cree
    salida_router: dict
    secciones: list[int]
    seccion_citada: int | None
    consulta_id: int | None
    solicitud_id: int | None
    verificada: bool
    senales_certeza: dict  # números y banderas, sin texto: van a `turnos`
    certeza: float | None  # la calcula `registrar` (ver certeza.py)
    error: str | None  # solo el nombre de la excepción
    costo_consulta: dict
    turno_id: int
    _destino: str
    _historial: list[dict]
    _contador: Contador
    _traza: Any
    _t0: float


def _derivar(estado: EstadoAgente, motivo: str, respuesta: str) -> dict:
    """Crea la solicitud antes de decirle al usuario que se derivó: sin solicitud no hay derivación."""
    salida, entrada = estado.get("salida_router") or {}, estado["entrada"]
    solicitud_id = crear_solicitud(
        estado["identidad"], "derivacion", motivo, entrada, salida.get("titulo") or entrada[:120],
        salida.get("detalle") or entrada[:2000], _unidad(salida),
    )
    return {"resultado": "derivada", "respuesta": respuesta, "motivo": motivo, "solicitud_id": solicitud_id}


def protegido(nombre: str, nodo: Callable[[EstadoAgente], dict]) -> Callable[[EstadoAgente], dict]:
    """Una funcionalidad que falla termina en la administración, nunca en una excepción. El detalle de
    la excepción no se guarda: puede traer texto del usuario o de los documentos."""

    def envuelto(estado: EstadoAgente) -> dict:
        try:
            return {"funcionalidad": nombre, **nodo(estado)}
        except Exception as e:
            error = type(e).__name__[:60]
            log.warning("falló la funcionalidad %s: %s", nombre, error)
        try:
            return {"funcionalidad": nombre, "error": error,
                    **_derivar(estado, "error_funcionalidad", MENSAJE_FALLO_DERIVADO)}
        except Exception as e:
            log.warning("tampoco se pudo crear la solicitud: %s", type(e).__name__)
            return {"funcionalidad": nombre, "error": error, "resultado": "error", "respuesta": MENSAJE_ERROR}

    return envuelto


def nodo_reglamento(estado: EstadoAgente) -> dict:
    identidad = estado["identidad"]
    pregunta = (estado.get("salida_router") or {}).get("pregunta_reescrita") or estado["entrada"]
    origen = "evaluacion" if estado["origen"] == "evaluacion" else estado["canal"]
    final = preguntar(pregunta, origen=origen, consorcio_id=identidad.consorcio_id, usuario_id=identidad.usuario_id,
                      validar_respuesta=True)
    datos = {
        "secciones": [r["seccion_id"] for r in final.get("resultados", [])], "consulta_id": final.get("consulta_id"),
        "costo_consulta": {**(final.get("costo") or {}), "costo_usd": final.get("_costo_usd")},
        "seccion_citada": (final.get("fuente") or {}).get("seccion_id"), "verificada": bool(final.get("verificada")),
        "senales_certeza": senales_reglamento(final.get("resultados", []), (final.get("respuesta") or {}).get("fuente")),
    }
    if not final.get("derivada"):
        return {**datos, "resultado": "respondida", "respuesta": formatear_respuesta(final)}
    motivo = "cita_no_verificada" if (final.get("motivo") or "").startswith("cita no verificada") else "sin_respaldo"
    return {**datos, **_derivar(estado, motivo, formatear_respuesta(final))}


MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")


def _ultimo_periodo(consorcio_id: str, mes: int | None = None) -> str | None:
    """El período cargado más reciente; con `mes`, el más reciente de ese mes."""
    periodos = [f["periodo"] for f in consultar_expensas("resumen", consorcio_id=consorcio_id)]
    validos = [p for p in periodos if periodo_valido(p) and (mes is None or p.endswith(f"-{mes:02d}"))]
    return validos[-1] if validos else None


def _mes_nombrado(texto: str) -> int | None:
    """El mes que nombra el vecino sin año ("la de agosto"). El router solo completa `AAAA-MM`."""
    palabras = set(re.findall(r"\w+", texto.lower().replace("setiembre", "septiembre")))
    nombrados = [i for i, mes in enumerate(MESES, 1) if mes in palabras]
    return nombrados[0] if len(nombrados) == 1 else None


def _que_pide(salida: dict) -> str:
    """Igual que en /expensas: sin indicación, una unidad o un texto dicen qué se está buscando."""
    if salida.get("que") not in (None, "resumen"):
        return salida["que"]
    if salida.get("propia") or salida.get("unidad"):
        return "unidades"
    return "gastos" if salida.get("texto") else "resumen"


def nodo_expensas(estado: EstadoAgente) -> dict:
    identidad, salida = estado["identidad"], estado.get("salida_router") or {}
    cid = identidad.consorcio_id
    que, periodo = _que_pide(salida), salida.get("periodo")
    propia = que == "unidades" and salida.get("propia")
    if propia and not identidad.unidades:
        return {"resultado": "sin_datos", "respuesta": MENSAJE_SIN_UNIDADES}
    mes = None if periodo else _mes_nombrado(salida.get("pregunta_reescrita") or estado["entrada"])
    if mes:
        periodo = _ultimo_periodo(cid, mes)
        if periodo is None:
            return {"resultado": "sin_datos", "respuesta": MENSAJE_SIN_EXPENSAS}
    if que == "resumen":
        filas = consultar_expensas("resumen", consorcio_id=cid)
        filas = [f for f in filas if f["periodo"] == periodo] if periodo else filas
        return _respuesta_expensas(que, filas)

    supuesto = None
    if not periodo:
        periodo = supuesto = _ultimo_periodo(cid)
        if periodo is None:
            return {"resultado": "sin_datos", "respuesta": MENSAJE_SIN_EXPENSAS}
    if propia:
        filas = [f for uf in identidad.unidades for f in consultar_expensas("unidades", periodo, str(uf), consorcio_id=cid)]
    else:
        filas = consultar_expensas(que, periodo, _unidad(salida), salida.get("texto"), consorcio_id=cid)
    return _respuesta_expensas(que, filas, supuesto, filtro_texto=que == "gastos" and bool(salida.get("texto")))


def _unidad(salida: dict) -> str | None:
    return salida["unidad"].upper() if salida.get("unidad") else None


def _respuesta_expensas(que: str, filas: list[dict], periodo_supuesto: str | None = None,
                        filtro_texto: bool = False) -> dict:
    if not filas:  # el texto de `formatear_expensas` menciona /expensas, y acá no hay comandos
        return {"resultado": "sin_datos", "respuesta": MENSAJE_SIN_EXPENSAS}
    texto = formatear_expensas(que, filas)
    if periodo_supuesto:
        texto = _recortar(f"Período {periodo_supuesto}, el último cargado.\n{texto}")
    return {"resultado": "respondida", "respuesta": texto,
            "senales_certeza": senales_expensas(bool(periodo_supuesto), filtro_texto, len(filas))}


def nodo_documentos(estado: EstadoAgente) -> dict:
    documentos = listar_documentos(estado["identidad"].consorcio_id)
    if not documentos:
        return {"resultado": "sin_datos", "respuesta": MENSAJE_SIN_DOCUMENTOS}
    return {"resultado": "respondida", "respuesta": formatear_documentos(documentos)}


def nodo_reclamo(estado: EstadoAgente) -> dict:
    """Solo deja la solicitud pendiente. No repite lo que escribió el modelo: acá no hay nada verificado."""
    salida, entrada = estado.get("salida_router") or {}, estado["entrada"]
    solicitud_id = crear_solicitud(
        estado["identidad"], "reclamo", "reclamo", entrada, salida.get("titulo") or entrada[:120],
        salida.get("detalle") or entrada[:2000], _unidad(salida),
    )
    return {"resultado": "reclamo_pendiente", "motivo": "reclamo", "solicitud_id": solicitud_id,
            "respuesta": f"Registré tu reclamo (n.º {solicitud_id}). Queda pendiente de aprobación de la administración."}


def nodo_derivar(estado: EstadoAgente) -> dict:
    """El pedido del usuario o un router que no se pudo interpretar: lo decide el nodo `router`."""
    return _derivar(estado, estado.get("motivo") or "pedido_usuario", MENSAJE_DERIVADO)


def nodo_fuera_de_alcance(_estado: EstadoAgente) -> dict:
    return {"resultado": "fuera_de_alcance", "respuesta": MENSAJE_FUERA_DE_ALCANCE, "funcionalidad": None}
