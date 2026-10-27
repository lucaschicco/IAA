"""Evaluación de la transcripción contra una referencia hecha a mano.

Caso de uso: `datos/documentos/contrato edificio cabrera.md` es la transcripción
revisada del PDF escaneado del mismo nombre. Corremos el extractor (Tesseract o
visión) y medimos cuánto se parece. Sirve como baseline para la hipótesis
"el clasificador de calidad ahorra tokens sin perder fidelidad".

La referencia tiene encabezados `## Página N`; si se evalúa un rango de páginas,
se compara solo contra esas páginas de la referencia.

Métricas (sobre texto normalizado):
- similitud_caracteres: ratio de difflib (0 a 1).
- tasa_error_palabras (WER aproximada): 1 - palabras coincidentes / palabras de referencia.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from pathlib import Path

from ..config import config
from ..llm import Contador
from .clasificador import Metodo, clasificar_pdf
from .grafo import hash_archivo, nodo_extraer_tesseract, nodo_extraer_texto, nodo_extraer_vision


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9ñ\s]", " ", t)
    return " ".join(t.split())


def quitar_marcas_md(texto: str) -> str:
    lineas = [ln for ln in texto.splitlines() if not ln.startswith("#") and not ln.startswith("---")]
    return "\n".join(lineas)


def referencia_por_pagina(texto_md: str) -> dict[int, str]:
    """Parte la referencia por encabezados `## Página N`."""
    paginas: dict[int, str] = {}
    actual: int | None = None
    for ln in texto_md.splitlines():
        m = re.match(r"^##\s+P[áa]gina\s+(\d+)", ln)
        if m:
            actual = int(m.group(1))
            paginas[actual] = ""
        elif actual is not None:
            paginas[actual] += ln + "\n"
    return paginas


def evaluar_transcripcion(
    ruta_pdf: str, ruta_referencia: str, metodo: Metodo | None = None, paginas: str | None = None
) -> dict:
    clasif = clasificar_pdf(ruta_pdf, metodo, paginas)
    estado = {
        "ruta_pdf": ruta_pdf,
        "documento_id": hash_archivo(ruta_pdf),
        "nombre_archivo": Path(ruta_pdf).name,
        "clasificacion": clasif.como_dict(),
        "paginas_rango": paginas,
        "_clasificacion_obj": clasif,
        "_contador": Contador(),
    }
    nodo = {"texto": nodo_extraer_texto, "tesseract": nodo_extraer_tesseract, "vision": nodo_extraer_vision}[clasif.metodo]
    extraidas = nodo(estado)["paginas"]  # type: ignore[arg-type]
    hipotesis = normalizar("\n".join(p["texto"] for p in extraidas))

    texto_ref = Path(ruta_referencia).read_text(encoding="utf-8")
    por_pagina = referencia_por_pagina(texto_ref)
    numeros = [p["numero"] for p in extraidas]
    if por_pagina and all(n in por_pagina for n in numeros):
        referencia = normalizar(quitar_marcas_md("\n".join(por_pagina[n] for n in numeros)))
    else:
        referencia = normalizar(quitar_marcas_md(texto_ref))

    sim = difflib.SequenceMatcher(None, referencia, hipotesis, autojunk=False).ratio()
    pal_ref, pal_hip = referencia.split(), hipotesis.split()
    coincidencias = sum(
        b.size for b in difflib.SequenceMatcher(None, pal_ref, pal_hip, autojunk=False).get_matching_blocks()
    )
    wer = 1 - coincidencias / max(len(pal_ref), 1)
    return {
        "archivo": Path(ruta_pdf).name,
        "proveedor": config.llm_provider,
        "modelo_vision": config.modelo_vision if clasif.metodo == "vision" else None,
        "metodo": clasif.metodo,
        "motivo": clasif.motivo,
        "paginas": numeros,
        "metodos_por_pagina": {p["numero"]: p["metodo"] for p in extraidas},
        "similitud_caracteres": round(sim, 4),
        "tasa_error_palabras": round(wer, 4),
        "palabras_referencia": len(pal_ref),
        "palabras_hipotesis": len(pal_hip),
        "costo": estado["_contador"].resumen(),
        "transcripcion": {p["numero"]: p["texto"] for p in extraidas},
    }
