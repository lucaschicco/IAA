"""Medidor de certeza de una respuesta: de 0 a 1, calculado en código con señales del propio turno.

Ningún proveedor que usamos devuelve una confianza por respuesta (Gemini no habilita logprobs en
`gemini-2.5-flash`, Ollama tampoco los expone), y la que el modelo declara sobre sí mismo no está
calibrada. Esto combina lo que el sistema ya sabe de cómo se armó la respuesta:

- reglamento: la cita y el texto se verificaron (sin eso la respuesta no sale: se deriva), si la sección
  citada fue la primera de la búsqueda, el margen sobre la segunda y qué tan fuerte fue la coincidencia.
- expensas: los importes salen de SQL; la duda está en la interpretación del pedido (un período que
  no se nombró y se supuso, o un filtro por texto que puede traer de más o de menos).
- documentos: es el listado de la base.

Solo se mide lo que se respondió: una derivación, un reclamo o un error no tienen certeza (None).
Los pesos son una estimación inicial: los calibran las revisiones de la administración (ver
`revision.py` y la sección Calidad del CRM). Si se cambian, se sube `VERSION` para comparar corridas.
"""
from __future__ import annotations

VERSION = 1
UMBRAL_ALTA = 0.8
UMBRAL_MEDIA = 0.65
PUNTAJE_REFERENCIA = 0.5  # ts_rank_cd de una coincidencia buena con las preguntas de prueba (19/11)


def _acotar(valor: float) -> float:
    return round(min(1.0, max(0.0, valor)), 3)


def senales_reglamento(resultados: list[dict], fuente: int | None) -> dict:
    """`fuente` es el número de fuente que citó el modelo (1 = la primera de la búsqueda)."""
    puntajes = [float(r.get("puntaje") or 0) for r in resultados]
    p1 = puntajes[0] if puntajes else 0.0
    margen = 1 - puntajes[1] / p1 if len(puntajes) > 1 and p1 > 0 else None
    return {"fuente_citada": fuente, "puntaje": round(p1, 4), "margen": None if margen is None else round(margen, 3),
            "secciones": len(puntajes)}


def senales_expensas(periodo_supuesto: bool, filtro_texto: bool, filas: int) -> dict:
    return {"periodo_supuesto": periodo_supuesto, "filtro_texto": filtro_texto, "filas": filas}


def calcular(funcionalidad: str | None, resultado: str, senales: dict | None) -> float | None:
    if resultado != "respondida" or not funcionalidad:
        return None
    s = senales or {}
    if funcionalidad == "reglamento":
        # Llegar acá ya implica cita y respuesta verificadas: es la base
        margen = s.get("margen")
        valor = (0.5 + 0.2 * (s.get("fuente_citada") == 1)
                 + 0.15 * (0.5 if margen is None else margen)  # con una sola sección no hay con qué comparar
                 + 0.15 * min(1.0, (s.get("puntaje") or 0) / PUNTAJE_REFERENCIA))
        return _acotar(valor)
    if funcionalidad == "expensas":
        return _acotar(0.9 - 0.2 * bool(s.get("periodo_supuesto")) - 0.15 * bool(s.get("filtro_texto")))
    if funcionalidad == "documentos":
        return 0.95
    return None


def nivel(valor: float | None) -> str | None:
    if valor is None:
        return None
    if valor >= UMBRAL_ALTA:
        return "alta"
    return "media" if valor >= UMBRAL_MEDIA else "baja"


NIVELES = ("alta", "media", "baja")
