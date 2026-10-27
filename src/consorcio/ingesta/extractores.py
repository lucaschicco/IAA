"""Las tres formas de sacar texto de una página de PDF.

- capa de texto (pdfplumber): gratis, exacta, solo si el PDF la tiene.
- Tesseract (pytesseract, idioma spa): gratis, devuelve confianza por palabra.
- visión del modelo: rasteriza la página y pide la transcripción; cuesta tokens.

Ninguna función de acá decide cuál usar: eso lo hace `clasificador.py`.
"""
from __future__ import annotations

import logging
import shutil
import time
from functools import lru_cache
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
from PIL import Image

from ..config import config
from ..llm import Contador, PresupuestoExcedido, imagen_a_bloque, invocar_texto
from .skill import cargar_skill

log = logging.getLogger("consorcio.extractores")
REINTENTOS_VISION = 3
ESPERA_REINTENTO = 20  # segundos; se multiplica por el número de intento
MAX_CHARS_PAGINA = 15_000  # una página A4 densa tiene ~4.000 caracteres


class TranscripcionDegenerada(ValueError):
    """El modelo entró en un bucle de repetición en vez de transcribir."""


def texto_degenerado(texto: str) -> bool:
    """Detecta el bucle de repetición de un modelo de visión: miles de 'l l l l' o de
    '[ilegible]' (gemma4 lo hizo en 2 de 60 páginas del reglamento, 261.000 tokens cada una).
    Criterio: demasiado largo para una página, o la cola repite 3 palabras o menos."""
    if len(texto) > MAX_CHARS_PAGINA:
        return True
    cola = texto[-400:].split()
    return len(cola) >= 40 and len(set(cola)) <= 3


@lru_cache(maxsize=1)
def tesseract_disponible() -> bool:
    return shutil.which("tesseract") is not None


def texto_capa(ruta_pdf: str, numero: int) -> str:
    with pdfplumber.open(ruta_pdf) as pdf:
        return (pdf.pages[numero - 1].extract_text() or "").strip()


def renderizar_pagina(ruta_pdf: str, numero: int, dpi: int = 200) -> Image.Image:
    doc = pdfium.PdfDocument(ruta_pdf)
    try:
        pagina = doc[numero - 1]
        bitmap = pagina.render(scale=dpi / 72)
        return bitmap.to_pil().convert("RGB")
    finally:
        doc.close()


def tesseract_pagina(imagen: Image.Image, idioma: str = "spa") -> tuple[str, float]:
    """Devuelve (texto, confianza media 0-100 de las palabras reconocidas)."""
    import pytesseract

    datos = pytesseract.image_to_data(imagen, lang=idioma, output_type=pytesseract.Output.DICT)
    confs = [float(c) for c, t in zip(datos["conf"], datos["text"], strict=True) if t.strip() and float(c) >= 0]
    texto = pytesseract.image_to_string(imagen, lang=idioma)
    media = sum(confs) / len(confs) if confs else 0.0
    return texto.strip(), media


def vision_pagina(imagen: Image.Image, numero: int, contador: Contador, modelo: str) -> str:
    """Pide al modelo multimodal que transcriba la página. El prompt vive en
    skills/ingesta/SKILL.md para poder iterarlo sin tocar código."""
    skill = cargar_skill("transcripcion")
    bloques = [
        {"type": "text", "text": f"Transcribí la página {numero} del documento escaneado."},
        imagen_a_bloque(imagen),
    ]
    ultimo: Exception | None = None
    for intento in range(REINTENTOS_VISION):
        try:
            texto = invocar_texto(
                contador, "extraer_vision", skill, bloques, modelo=modelo, max_tokens=config.max_tokens_vision
            )
            if texto_degenerado(texto):
                # No se reintenta: el bucle se repitió idéntico 3 veces (261.563 tokens, 20 min cada
                # una) y Ollama cloud ignora `max_tokens`, así que la única salida barata es Tesseract.
                ultimo = TranscripcionDegenerada(f"{len(texto)} caracteres, cola: {texto[-60:]!r}")
                log.warning("visión página %s: transcripción degenerada (%s)", numero, str(ultimo)[:120])
                break
            return texto
        except PresupuestoExcedido:
            raise
        except Exception as e:  # cortes de conexión, 429, timeouts del proveedor
            ultimo = e
            log.warning("visión página %s, intento %s falló: %s", numero, intento + 1, str(e)[:120])
            time.sleep(ESPERA_REINTENTO * (intento + 1))
    # Respaldo: Tesseract si está, para no perder el documento entero por una página
    if tesseract_disponible():
        texto, conf = tesseract_pagina(imagen)
        log.warning("página %s: visión falló (%s), se usa Tesseract (confianza %.0f)", numero, type(ultimo).__name__, conf)
        return f"[visión no disponible: {type(ultimo).__name__}; transcripción Tesseract, confianza {conf:.0f}]\n{texto}"
    raise ultimo  # type: ignore[misc]


def guardar_png(imagen: Image.Image, carpeta: Path, numero: int) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / f"pagina_{numero:03d}.png"
    imagen.save(ruta)
    return ruta
