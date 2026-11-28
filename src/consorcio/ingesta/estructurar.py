"""Del texto transcripto a entidades estructuradas, con el modelo.

1. `extraer_metadatos`: una llamada mirando el inicio del documento.
2. `estructurar_lotes`: una llamada por lote de páginas → ExtraccionParcial.
3. `fusionar`: une los lotes y deduplica entidades repetidas.
"""
from __future__ import annotations

import unicodedata

from ..config import config
from ..llm import Contador, PresupuestoExcedido, invocar_estructurado
from .esquemas import (
    DocumentoExtraido,
    ExtraccionParcial,
    MetadatosDocumento,
)
from .skill import cargar_skill

MAX_CHARS_METADATOS = 12_000


def texto_con_marcas(paginas: list[dict]) -> str:
    """Concatena las páginas marcando el número, para que el modelo pueda citarlo."""
    return "\n\n".join(f"=== Página {p['numero']} ===\n{p['texto']}" for p in paginas)


def extraer_metadatos(paginas: list[dict], contador: Contador) -> MetadatosDocumento:
    texto = texto_con_marcas(paginas)[:MAX_CHARS_METADATOS]
    return invocar_estructurado(
        contador, "metadatos", cargar_skill("metadatos"), texto, MetadatosDocumento
    )


def estructurar_lotes(paginas: list[dict], contador: Contador) -> tuple[list[ExtraccionParcial], list[str]]:
    """Una llamada por lote. Si un lote falla (JSON inválido, error del proveedor) se anota
    el error y se sigue con el siguiente: un lote malo no tira el documento entero.
    Solo el presupuesto excedido corta el ciclo."""
    tam = max(1, config.estructurar_paginas_por_lote)
    sistema = cargar_skill("estructurar")
    parciales: list[ExtraccionParcial] = []
    errores: list[str] = []
    for i in range(0, len(paginas), tam):
        lote = paginas[i : i + tam]
        if not any(p["texto"].strip() for p in lote):
            continue
        try:
            parciales.append(
                invocar_estructurado(contador, "estructurar", sistema, texto_con_marcas(lote), ExtraccionParcial)
            )
        except PresupuestoExcedido as e:
            errores.append(f"estructurar lote {i // tam + 1}: {e}")
            break
        except Exception as e:
            errores.append(f"estructurar lote {i // tam + 1} (págs. {lote[0]['numero']}-{lote[-1]['numero']}): {type(e).__name__}: {str(e)[:300]}")
    return parciales, errores


def fusionar(metadatos: MetadatosDocumento, parciales: list[ExtraccionParcial]) -> DocumentoExtraido:
    doc = DocumentoExtraido(metadatos=metadatos)
    for parcial in parciales:
        for coleccion in DocumentoExtraido.COLECCIONES:
            getattr(doc, coleccion).extend(getattr(parcial, coleccion))
    doc.personas = _deduplicar(doc.personas, lambda p: _clave(p.nombre))
    doc.organizaciones = _deduplicar(doc.organizaciones, lambda o: _clave(o.nombre))
    doc.direcciones = _deduplicar(doc.direcciones, lambda d: _clave(d.texto))
    doc.eventos = _deduplicar(doc.eventos, lambda e: (e.fecha, _clave(e.descripcion)[:40]))
    doc.unidades = _deduplicar(doc.unidades, lambda u: _clave(u.identificador))
    doc.secciones = _deduplicar(doc.secciones, lambda s: (_clave(s.titulo), s.pagina_inicio))
    return doc


def _clave(texto: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", texto or "")
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    return " ".join(sin_acentos.lower().replace(",", " ").split())


def _deduplicar(items: list, clave) -> list:
    """Conserva la primera aparición y completa sus campos vacíos con las siguientes."""
    vistos: dict = {}
    for item in items:
        k = clave(item)
        if k in vistos:
            previo = vistos[k]
            for campo, valor in item.model_dump().items():
                if getattr(previo, campo) in (None, [], "") and valor not in (None, [], ""):
                    setattr(previo, campo, getattr(item, campo))
            if hasattr(item, "datos_sensibles"):
                conocidos = {(d.tipo, d.valor) for d in previo.datos_sensibles}
                previo.datos_sensibles.extend(
                    d for d in item.datos_sensibles if (d.tipo, d.valor) not in conocidos
                )
        else:
            vistos[k] = item
    return list(vistos.values())
