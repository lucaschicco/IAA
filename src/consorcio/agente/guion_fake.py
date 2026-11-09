"""Guion del proveedor fake (`FAKE_GUIONADO=true`): respuestas plausibles sin clave ni red.

Sin esto el router fake siempre falla y `RespuestaConCita` vacía siempre deriva, y la web de prueba
solo mostraría derivaciones. Las marcas `#fallar-router` y `#sin-respaldo` solo existen acá."""
from __future__ import annotations

import re

from ..consulta.esquemas import RespuestaConCita
from ..texto import normalizar
from .esquemas import SalidaRouter

_MENSAJE = re.compile(r"<mensaje>\s*(.*?)\s*</mensaje>", re.DOTALL)
_PAGINA = re.compile(r"=== Página (\d+) ===\n(.+)")


def _intencion(mensaje: str) -> str:
    n = normalizar(mensaje)
    reglas = [
        (("expensa", "pagar", "gasto", "rubro"), "expensas"), (("documento",), "documentos"),
        (("reclamo", "roto", "perdida"), "reclamo"), (("humano", "persona", "administracion"), "derivar"),
        (("clima", "futbol"), "fuera_de_alcance"),
    ]
    return next((intencion for palabras, intencion in reglas if any(p in n for p in palabras)), "reglamento")


def _que(mensaje: str) -> str:
    n = normalizar(mensaje)
    return next((que for palabra, que in (("rubro", "rubros"), ("gasto", "gastos"), ("unidad", "unidades")) if palabra in n),
                "resumen")


def _unidad(mensaje: str) -> str | None:
    m = re.search(r"\b(\d+-[A-Za-z])\b", mensaje) or re.search(r"\bUF\s*(\d+)\b", mensaje, re.IGNORECASE)
    return m.group(1).upper() if m else None


def router(texto: str) -> SalidaRouter:
    m = _MENSAJE.search(texto)
    mensaje = m.group(1) if m else texto
    if "#fallar-router" in mensaje:
        raise ValueError("el guion pidió fallar")
    periodo = re.search(r"\b\d{4}-\d{2}\b", mensaje)
    return SalidaRouter(
        intencion=_intencion(mensaje), pregunta_reescrita=mensaje, que=_que(mensaje),
        periodo=periodo.group(0) if periodo else None, unidad=_unidad(mensaje),
        propia=bool(re.search(r"\b(mi|mis|pago|debo)\b", normalizar(mensaje))),
        titulo=mensaje[:120], detalle=mensaje[:2000],
    )


def respuesta_con_cita(texto: str) -> RespuestaConCita:
    pagina = _PAGINA.search(texto)
    if "#sin-respaldo" in texto or not pagina:
        return RespuestaConCita(puede_responder=False, respuesta="[fake] sin respaldo en las fuentes")
    fuente = re.findall(r"\[Fuente (\d+)\]", texto[: pagina.start()])[-1]
    cita = pagina.group(2)[:60]
    return RespuestaConCita(puede_responder=True, respuesta=f"[fake] Según el documento: {cita}", fuente=int(fuente),
                            pagina=int(pagina.group(1)), cita=cita)


GUIONES = {"SalidaRouter": router, "RespuestaConCita": respuesta_con_cita}
