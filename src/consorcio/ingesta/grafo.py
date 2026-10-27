"""Grafo de LangGraph de la ingesta de un PDF.

    START → clasificar ─┬→ extraer_texto ─────┐
                        ├→ extraer_tesseract ─┼→ estructurar → persistir → END
                        └→ extraer_vision ────┘

- `clasificar` decide en código (sin modelo) y la arista condicional rutea.
- Los extractores respetan la decisión por página: una página con capa de texto
  siempre se lee de la capa de texto aunque el documento sea "escaneado".
- `extraer_tesseract` puede escalar a visión las páginas con baja confianza
  (OCR_ESCALAR_A_VISION), que es el gasto de tokens "sólo cuando hace falta".
- `persistir` guarda la transcripción en datos/procesados/, los datos en Postgres
  y el contexto en Neo4j. Todavía no hay revisión humana: el documento queda en
  estado `extraido`; la bandeja de aprobación (interrupt) se agrega después.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import config
from ..llm import Contador
from ..texto import hash_de, id_documento
from . import extractores
from .clasificador import Clasificacion, Metodo, clasificar_pdf
from .esquemas import DocumentoExtraido, ExtraccionParcial
from .estructurar import estructurar_lotes, extraer_metadatos, fusionar
from .liquidacion import es_liquidacion, extraer_liquidacion, parsear_prorrateo, secciones_de_rubros
from .skill import version_skill

log = logging.getLogger("consorcio.ingesta")


class EstadoIngesta(TypedDict, total=False):
    ruta_pdf: str
    metodo_forzado: Metodo | None
    paginas_rango: str | None  # p. ej. "1-3": procesar solo esas páginas
    reusar_texto: bool  # reutiliza las páginas OCR/visión ya guardadas en Postgres (misma hash)
    consorcio_id: str  # base a la que va el documento (cada consorcio tiene la suya)
    documento_id: str  # <consorcio>:<hash del archivo>
    nombre_archivo: str
    clasificacion: dict
    paginas: list[dict]  # {numero, texto, metodo, confianza}
    extraccion: dict  # DocumentoExtraido.model_dump()
    liquidacion: dict | None  # solo liquidaciones de expensas: encabezado, rubros, gastos, prorrateo
    persistido: dict
    costo: dict
    errores: list[str]
    segundos_total: float
    sin_persistir: bool  # para pruebas sin bases de datos
    _contador: Contador
    _clasificacion_obj: Clasificacion
    _t0: float


# ------------------------------------------------------------------ nodos


def nodo_clasificar(estado: EstadoIngesta) -> dict:
    ruta = estado["ruta_pdf"]
    clasif = clasificar_pdf(ruta, estado.get("metodo_forzado"), estado.get("paginas_rango"))
    consorcio_id = estado.get("consorcio_id") or config.consorcio_por_defecto
    return {
        "consorcio_id": consorcio_id,
        "documento_id": id_documento(consorcio_id, hash_archivo(ruta)),
        "nombre_archivo": Path(ruta).name,
        "clasificacion": clasif.como_dict(),
        "_clasificacion_obj": clasif,
        "_contador": Contador(),
        "_t0": time.perf_counter(),
        "errores": [],
    }


def rutear(estado: EstadoIngesta) -> Literal["extraer_texto", "extraer_tesseract", "extraer_vision"]:
    return f"extraer_{estado['clasificacion']['metodo']}"  # type: ignore[return-value]


def nodo_extraer_texto(estado: EstadoIngesta) -> dict:
    return {"paginas": _extraer_paginas(estado, permitir_ocr=False)}


def nodo_extraer_tesseract(estado: EstadoIngesta) -> dict:
    return {"paginas": _extraer_paginas(estado, permitir_ocr=True)}


def nodo_extraer_vision(estado: EstadoIngesta) -> dict:
    return {"paginas": _extraer_paginas(estado, permitir_ocr=True)}


def _extraer_paginas(estado: EstadoIngesta, permitir_ocr: bool) -> list[dict]:
    ruta = estado["ruta_pdf"]
    clasif: Clasificacion = estado["_clasificacion_obj"]
    contador: Contador = estado["_contador"]
    carpeta_png = carpeta_procesados(estado) / "paginas_png"
    previas = _paginas_previas(estado) if estado.get("reusar_texto") else {}
    paginas: list[dict] = []
    for senal in clasif.paginas:
        n = senal.numero
        metodo = senal.metodo if permitir_ocr else "texto"
        confianza: float | None = None
        if n in previas:
            paginas.append(previas[n])
            continue
        if metodo == "texto":
            texto = extractores.texto_capa(ruta, n)
            confianza = 100.0
        else:
            imagen = extractores.renderizar_pagina(ruta, n, dpi=config.ocr_dpi)
            extractores.guardar_png(imagen, carpeta_png, n)
            if metodo == "tesseract":
                texto, confianza = extractores.tesseract_pagina(imagen)
                if config.ocr_escalar_a_vision and confianza < config.ocr_umbral_confianza:
                    texto = extractores.vision_pagina(imagen, n, contador, config.modelo_vision)
                    metodo = "vision"
                    confianza = None
            else:
                texto = extractores.vision_pagina(imagen, n, contador, config.modelo_vision)
        paginas.append({"numero": n, "texto": texto, "metodo": metodo, "confianza": confianza})
    return paginas


def _paginas_previas(estado: EstadoIngesta) -> dict[int, dict]:
    """Páginas OCR/visión de una corrida anterior del mismo archivo, para iterar el
    estructurado sin volver a pagar la transcripción (`--reusar-texto`). Se descartan las
    páginas degeneradas (bucle del modelo) para que se transcriban de nuevo."""
    from ..almacen import postgres

    previas = {}
    for p in postgres.paginas_guardadas(estado["documento_id"]):
        if p["metodo"] != "texto" and not extractores.texto_degenerado(p["texto"]):
            previas[p["numero"]] = p
    if previas:
        log.info("se reutilizan %s páginas transcriptas en una corrida anterior", len(previas))
    return previas


def nodo_estructurar(estado: EstadoIngesta) -> dict:
    contador: Contador = estado["_contador"]
    paginas = estado["paginas"]
    errores = list(estado.get("errores", []))
    metadatos = extraer_metadatos(paginas, contador)
    liquidacion: dict | None = None
    parciales: list[ExtraccionParcial] = []
    try:
        if metadatos.tipo_documento == "liquidacion_expensas" or es_liquidacion(paginas):
            # Liquidación: una llamada para encabezado, rubros y gastos; el prorrateo por
            # unidad se parsea en código. No se corre `estructurar` (sacaría 150 "personas").
            metadatos.tipo_documento = "liquidacion_expensas"
            liq = extraer_liquidacion(paginas, contador)
            liquidacion = {**liq.model_dump(), **parsear_prorrateo(paginas)}
            parciales = [ExtraccionParcial(secciones=secciones_de_rubros(liq))]
        else:
            parciales, errores_lotes = estructurar_lotes(paginas, contador)
            errores.extend(errores_lotes)
    except Exception as e:  # presupuesto o error del proveedor: guardamos lo que hay
        errores.append(f"estructurar: {type(e).__name__}: {str(e)[:300]}")
    doc = fusionar(metadatos, parciales)
    return {"extraccion": doc.model_dump(), "liquidacion": liquidacion, "errores": errores}


def nodo_persistir(estado: EstadoIngesta) -> dict:
    contador: Contador = estado["_contador"]
    doc = DocumentoExtraido.model_validate(estado["extraccion"])
    costo = contador.resumen()
    segundos_total = round(time.perf_counter() - estado["_t0"], 2)
    carpeta = carpeta_procesados(estado)
    carpeta.mkdir(parents=True, exist_ok=True)

    # 1) Transcripción legible y resultado estructurado, siempre, en el repo
    (carpeta / "texto.md").write_text(transcripcion_markdown(estado), encoding="utf-8")
    resultado = {
        "documento_id": estado["documento_id"],
        "consorcio_id": estado["consorcio_id"],
        "archivo": estado["nombre_archivo"],
        "clasificacion": estado["clasificacion"],
        "skill_version": version_skill(),
        "proveedor": config.llm_provider,
        "modelo": config.modelo,
        "modelo_vision": config.modelo_vision,
        "paginas_rango": estado.get("paginas_rango"),
        "costo": costo,
        "llamadas": contador.detalle(),
        "segundos_total": segundos_total,
        "errores": estado.get("errores", []),
        "extraccion": doc.model_dump(),
        "liquidacion": estado.get("liquidacion"),
    }
    (carpeta / "extraccion.json").write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    persistido: dict = {"carpeta": str(carpeta)}
    if not estado.get("sin_persistir"):
        from ..almacen import grafo_neo4j, postgres

        persistido["postgres"] = postgres.guardar_documento(
            documento_id=estado["documento_id"],
            nombre_archivo=estado["nombre_archivo"],
            ruta=estado["ruta_pdf"],
            clasificacion=estado["clasificacion"],
            paginas=estado["paginas"],
            doc=doc,
            costo=costo,
            llamadas=contador.detalle(),
            segundos_total=segundos_total,
            liquidacion=estado.get("liquidacion"),
            consorcio_id=estado["consorcio_id"],
        )
        persistido["neo4j"] = grafo_neo4j.guardar_documento(estado["documento_id"], doc, estado["consorcio_id"])

    return {"persistido": persistido, "costo": costo, "segundos_total": segundos_total}


# ------------------------------------------------------------------ grafo


def construir_grafo():
    g = StateGraph(EstadoIngesta)
    g.add_node("clasificar", nodo_clasificar)
    g.add_node("extraer_texto", nodo_extraer_texto)
    g.add_node("extraer_tesseract", nodo_extraer_tesseract)
    g.add_node("extraer_vision", nodo_extraer_vision)
    g.add_node("estructurar", nodo_estructurar)
    g.add_node("persistir", nodo_persistir)

    g.add_edge(START, "clasificar")
    g.add_conditional_edges(
        "clasificar", rutear, ["extraer_texto", "extraer_tesseract", "extraer_vision"]
    )
    for nodo in ("extraer_texto", "extraer_tesseract", "extraer_vision"):
        g.add_edge(nodo, "estructurar")
    g.add_edge("estructurar", "persistir")
    g.add_edge("persistir", END)
    return g.compile()


def ingestar_pdf(
    ruta_pdf: str,
    metodo: Metodo | None = None,
    sin_persistir: bool = False,
    paginas: str | None = None,
    reusar_texto: bool = False,
    consorcio_id: str | None = None,
) -> EstadoIngesta:
    """Punto de entrada: corre el grafo completo sobre un PDF (o un rango de páginas) y lo guarda en la
    base del consorcio indicado (por defecto, `config.consorcio_por_defecto`)."""
    from ..observabilidad import trazar_corrida

    grafo = construir_grafo()
    consorcio_id = consorcio_id or config.consorcio_por_defecto
    entrada: EstadoIngesta = {
        "ruta_pdf": ruta_pdf, "metodo_forzado": metodo, "sin_persistir": sin_persistir, "paginas_rango": paginas,
        "reusar_texto": reusar_texto, "consorcio_id": consorcio_id,
    }
    etiquetas = {"archivo": Path(ruta_pdf).name, "metodo_forzado": metodo or "auto", "paginas": paginas or "todas",
                 "consorcio": consorcio_id}
    with trazar_corrida("ingesta", etiquetas) as traza:
        final = grafo.invoke(entrada)
        traza.registrar(final)
    return final


# ------------------------------------------------------------------ utilidades


def hash_archivo(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()[:16]


def slug(nombre: str) -> str:
    base = Path(nombre).stem.lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:60] or "documento"


def carpeta_procesados(estado: EstadoIngesta) -> Path:
    nombre = f"{slug(estado['nombre_archivo'])}-{hash_de(estado['documento_id'])[:8]}"
    if estado.get("paginas_rango"):
        nombre += "-pags-" + re.sub(r"[^0-9,-]", "", estado["paginas_rango"])
    nombre += f"-{config.llm_provider}-{slug(config.modelo)}"
    return config.ruta_procesados() / nombre


def transcripcion_markdown(estado: EstadoIngesta) -> str:
    clasif = estado["clasificacion"]
    lineas = [
        f"# Transcripción de `{estado['nombre_archivo']}`",
        "",
        f"- Documento: `{estado['documento_id']}`",
        f"- Método principal: **{clasif['metodo']}** (confianza {clasif['confianza']}) — {clasif['motivo']}",
        f"- Páginas: {clasif['total_paginas']}",
        "",
    ]
    for p in estado.get("paginas", []):
        conf = f", confianza {p['confianza']:.0f}" if p.get("confianza") is not None else ""
        lineas += [f"## Página {p['numero']}", "", f"_método: {p['metodo']}{conf}_", "", p["texto"], ""]
    return "\n".join(lineas)
