"""Clasificador de calidad del PDF: decide cómo extraer el texto SIN usar el modelo.

Es el "paso inicial de clasificación, símil Jev" de la práctica: recibe el PDF,
mide señales en código y devuelve una decisión tipada con confianza.

Decisión por página:
  - "texto":     la página tiene capa de texto suficiente → se lee gratis.
  - "tesseract": escaneo legible (Tesseract confía) → OCR local, gratis.
  - "vision":    escaneo malo o Tesseract no disponible → modelo multimodal (cuesta tokens).

Decisión del documento: el método que necesita la mayoría de las páginas sin
texto; las páginas con texto siempre se leen de la capa de texto.
"""
from __future__ import annotations

import shutil
import statistics
from dataclasses import asdict, dataclass, field
from typing import Literal

import pdfplumber

from ..config import config
from .extractores import renderizar_pagina, tesseract_disponible, tesseract_pagina

Metodo = Literal["texto", "tesseract", "vision"]


@dataclass
class SenalPagina:
    numero: int
    chars_texto: int
    confianza_tesseract: float | None  # None = no se midió
    metodo: Metodo


@dataclass
class Clasificacion:
    metodo: Metodo
    confianza: float  # 0 a 1: cuán segura es la decisión
    paginas: list[SenalPagina] = field(default_factory=list)
    motivo: str = ""
    total_paginas: int = 0

    def como_dict(self) -> dict:
        return {
            "metodo": self.metodo,
            "confianza": round(self.confianza, 3),
            "motivo": self.motivo,
            "total_paginas": self.total_paginas,
            "paginas": [asdict(p) for p in self.paginas],
        }

    def metodo_de(self, numero: int) -> Metodo:
        for p in self.paginas:
            if p.numero == numero:
                return p.metodo
        return self.metodo


def contar_chars_por_pagina(ruta_pdf: str) -> list[int]:
    with pdfplumber.open(ruta_pdf) as pdf:
        return [len((p.extract_text() or "").strip()) for p in pdf.pages]


def parsear_rango(rango: str | None, total: int) -> list[int] | None:
    """'1-3,7' → [1, 2, 3, 7]; None → todas las páginas."""
    if not rango:
        return None
    numeros: set[int] = set()
    for parte in rango.split(","):
        parte = parte.strip()
        if "-" in parte:
            a, b = parte.split("-", 1)
            numeros.update(range(int(a), int(b) + 1))
        elif parte:
            numeros.add(int(parte))
    return sorted(n for n in numeros if 1 <= n <= total)


def clasificar_pdf(
    ruta_pdf: str, metodo_forzado: Metodo | None = None, paginas: str | None = None
) -> Clasificacion:
    chars = contar_chars_por_pagina(ruta_pdf)
    total = len(chars)
    umbral = config.ocr_umbral_texto_chars
    seleccion = parsear_rango(paginas, total)
    senales = [
        SenalPagina(i + 1, c, None, "texto" if c >= umbral else "vision")
        for i, c in enumerate(chars)
        if seleccion is None or (i + 1) in seleccion
    ]
    sin_texto = [s for s in senales if s.metodo != "texto"]

    if metodo_forzado:
        for s in senales:
            if s.metodo != "texto" or metodo_forzado == "vision":
                s.metodo = metodo_forzado
        return Clasificacion(metodo_forzado, 1.0, senales, f"método forzado por el usuario: {metodo_forzado}", total)

    if not sin_texto:
        return Clasificacion("texto", 1.0, senales, "todas las páginas tienen capa de texto", total)

    # Hay páginas escaneadas: medimos cuánto confía Tesseract en una muestra.
    if not tesseract_disponible():
        return Clasificacion(
            "vision", 0.6, senales,
            "páginas escaneadas y Tesseract no está instalado; se usa visión del modelo", total,
        )

    muestra = _muestra_uniforme([s.numero for s in sin_texto], config.ocr_paginas_muestra)
    confianzas: list[float] = []
    for numero in muestra:
        imagen = renderizar_pagina(ruta_pdf, numero, dpi=config.ocr_dpi)
        _, conf = tesseract_pagina(imagen)
        confianzas.append(conf)
        for s in senales:
            if s.numero == numero:
                s.confianza_tesseract = round(conf, 1)

    media = statistics.fmean(confianzas) if confianzas else 0.0
    umbral_conf = config.ocr_umbral_confianza
    # Distancia al umbral, normalizada, como confianza en la decisión
    confianza_decision = min(1.0, 0.5 + abs(media - umbral_conf) / 50)

    if media >= umbral_conf:
        metodo: Metodo = "tesseract"
        motivo = f"escaneo legible: confianza media de Tesseract {media:.0f} ≥ {umbral_conf:.0f} en {len(muestra)} páginas de muestra"
    else:
        metodo = "vision"
        motivo = f"escaneo difícil: confianza media de Tesseract {media:.0f} < {umbral_conf:.0f}; conviene gastar tokens en visión"

    for s in sin_texto:
        s.metodo = metodo
    frac_escaneadas = len(sin_texto) / max(total, 1)
    if frac_escaneadas < 0.5:
        motivo += f" ({len(sin_texto)} de {total} páginas escaneadas; el resto se lee de la capa de texto)"
    return Clasificacion(metodo, confianza_decision, senales, motivo, total)


def _muestra_uniforme(numeros: list[int], k: int) -> list[int]:
    if len(numeros) <= k:
        return numeros
    paso = len(numeros) / k
    return sorted({numeros[int(i * paso)] for i in range(k)})


def herramientas_disponibles() -> dict[str, bool]:
    return {"tesseract": tesseract_disponible(), "pdftotext": shutil.which("pdftotext") is not None}
