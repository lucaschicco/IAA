"""Carga de PDFs en segundo plano: la administración sube varios a la vez y sigue usando la web.

Una sola hebra procesa la cola en orden: la ingesta ya usa el modelo con su propio tope, dos a la vez solo
competirían por la misma cuota, y MLflow configura su experimento una vez por proceso sin candado.
El estado vive en memoria y se ve en Documentos (se refresca solo mientras haya algo pendiente).

shortcut: cola y estado dentro del proceso web; un reinicio pierde lo pendiente (el PDF queda en
datos/entrada) y con más de un worker cada uno ve solo lo suyo. Pasar a una cola persistente
(tabla + proceso aparte) cuando haya más de un worker o cargas de cientos de archivos.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

log = logging.getLogger("consorcio.web")

MAX_POR_CONSORCIO = 50  # las últimas cargas que se recuerdan de cada consorcio
PENDIENTES = ("en_cola", "procesando")

_cola = ThreadPoolExecutor(max_workers=1, thread_name_prefix="carga-pdf")
_candado = threading.Lock()
_cargas: dict[str, list[dict]] = {}  # consorcio_id -> cargas, la más nueva primero
_futuros: dict[str, Future] = {}  # id de carga -> su tarea, mientras no terminó


def encolar(ruta: Path, nombre: str, consorcio_id: str, usuario_id: int | None,
            procesar: Callable[[str, str, int | None], str], mensaje_error: str) -> dict:
    """`procesar(ruta, consorcio_id, usuario_id)` devuelve el texto que ve la administración al terminar."""
    carga = {"id": uuid4().hex, "nombre": nombre, "estado": "en_cola", "mensaje": "", "creado_en": datetime.now(UTC)}
    with _candado:
        lista = _cargas.setdefault(consorcio_id, [])
        lista.insert(0, carga)
        del lista[MAX_POR_CONSORCIO:]
    futuro = _cola.submit(_correr, carga, str(ruta), consorcio_id, usuario_id, procesar, mensaje_error)
    with _candado:
        _futuros[carga["id"]] = futuro
    futuro.add_done_callback(lambda _f: _futuros.pop(carga["id"], None))
    return carga


def esperar(cargas: list[dict], segundos: float) -> None:
    """Espera hasta `segundos` a que terminen: lo rápido (un duplicado, un PDF de texto chico) se informa en la misma
    respuesta, como antes de la cola; lo demás sigue en segundo plano."""
    with _candado:
        futuros = [_futuros[c["id"]] for c in cargas if c["id"] in _futuros]
    if futuros:
        wait(futuros, timeout=segundos)


def _correr(carga: dict, ruta: str, consorcio_id: str, usuario_id: int | None,
            procesar: Callable[[str, str, int | None], str], mensaje_error: str) -> None:
    carga["estado"] = "procesando"
    try:
        carga["mensaje"] = procesar(ruta, consorcio_id, usuario_id)
        carga["estado"] = "listo"
    except Exception as e:  # una carga que falla no frena la cola; el detalle puede traer texto del PDF
        log.warning("falló la carga de un PDF: %s", type(e).__name__)
        carga["mensaje"], carga["estado"] = mensaje_error, "error"


def de_consorcio(consorcio_id: str) -> list[dict]:
    with _candado:
        return [dict(c) for c in _cargas.get(consorcio_id, [])]


def hay_pendientes(cargas: list[dict]) -> bool:
    return any(c["estado"] in PENDIENTES for c in cargas)
