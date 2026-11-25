"""Grafo de LangGraph de la ingesta de un PDF.

    START → clasificar ─┬→ extraer_texto ─────┐
                        ├→ extraer_tesseract ─┼→ estructurar → guardar_disco → preparar_guardado → persistir → END
                        └→ extraer_vision ────┘

- `clasificar` decide en código (sin modelo) y la arista condicional rutea.
- Los extractores respetan la decisión por página: una página con capa de texto
  siempre se lee de la capa de texto aunque el documento sea "escaneado".
- `extraer_tesseract` puede escalar a visión las páginas con baja confianza
  (OCR_ESCALAR_A_VISION), que es el gasto de tokens "sólo cuando hace falta".
- `guardar_disco` escribe la transcripción en datos/procesados/ apenas termina el modelo:
  lo ya pagado sobrevive aunque después falle el bucket o Postgres.
- `persistir` guarda los datos en Postgres. Todavía no hay revisión humana: el documento
  queda en estado `extraido`; la bandeja de aprobación (interrupt) se agrega después.
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

from ..almacen import crudos
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
    forzar: bool  # pedido explícito de reprocesar y de reemplazar otro PDF del mismo período
    consorcio_id: str  # base a la que va el documento (cada consorcio tiene la suya)
    sha256: str  # del archivo completo; se calcula antes de clasificar
    documento_id: str  # <consorcio>:<hash del archivo>
    crudo_clave: str | None  # clave del PDF en el bucket; None si no hay bucket configurado
    estado: str  # procesado | solo_disco | duplicado | en_proceso | periodo_duplicado | tope_excedido
    documento_existente: dict  # el documento del mismo consorcio que provocó un corte
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
    return {
        "clasificacion": clasif.como_dict(),
        "_clasificacion_obj": clasif,
        "_contador": Contador(consorcio_id=estado.get("consorcio_id")),
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
    for p in postgres.paginas_guardadas(estado["documento_id"], estado["consorcio_id"]):
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


def nodo_guardar_disco(estado: EstadoIngesta) -> dict:
    """Transcripción legible y resultado estructurado, siempre, en el repo. Va antes del bucket y de
    Postgres: si alguno falla, lo que costó OCR y modelo ya quedó guardado."""
    contador: Contador = estado["_contador"]
    doc = DocumentoExtraido.model_validate(estado["extraccion"])
    costo = contador.resumen()
    segundos_total = round(time.perf_counter() - estado["_t0"], 2)
    carpeta = carpeta_procesados(estado)
    carpeta.mkdir(parents=True, exist_ok=True)

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
    return {"persistido": {"carpeta": str(carpeta)}, "costo": costo, "segundos_total": segundos_total}


def nodo_preparar_guardado(estado: EstadoIngesta) -> dict:
    """Último paso antes de guardar. El período recién se conoce al extraer la liquidación, así que este
    corte llega después de gastar modelo. Si el documento sigue, sube el crudo: un fallo del bucket
    frena acá, sin fila en Postgres. `forzar` es solo el explícito: `--reusar-texto` no autoriza a
    reemplazar otro PDF."""
    if estado.get("sin_persistir"):
        return {}
    from ..almacen import postgres

    periodo = (estado.get("liquidacion") or {}).get("periodo")
    if postgres.periodo_valido(periodo) and not estado.get("forzar"):
        otro = postgres.documento_del_periodo(estado["consorcio_id"], periodo, estado["documento_id"])
        if otro:
            return {"sin_persistir": True, "estado": "periodo_duplicado", "documento_existente": otro}
    return {"crudo_clave": crudos.subir_crudo(estado["ruta_pdf"], estado["consorcio_id"], estado["sha256"])}


def nodo_persistir(estado: EstadoIngesta) -> dict:
    if estado.get("sin_persistir"):
        return {}
    from ..almacen import postgres

    fila = postgres.guardar_documento(
        documento_id=estado["documento_id"],
        sha256=estado.get("sha256"),
        crudo_clave=estado.get("crudo_clave"),
        reemplazar_periodo=estado.get("forzar", False),
        nombre_archivo=estado["nombre_archivo"],
        ruta=estado["ruta_pdf"],
        clasificacion=estado["clasificacion"],
        paginas=estado["paginas"],
        doc=DocumentoExtraido.model_validate(estado["extraccion"]),
        costo=estado["costo"],
        llamadas=estado["_contador"].detalle(),
        segundos_total=estado["segundos_total"],
        liquidacion=estado.get("liquidacion"),
        consorcio_id=estado["consorcio_id"],
        incompleto=bool(estado.get("errores")) and not estado["extraccion"].get("secciones"),
    )
    return {"persistido": {**estado["persistido"], "postgres": fila}}


# ------------------------------------------------------------------ grafo


def construir_grafo():
    g = StateGraph(EstadoIngesta)
    g.add_node("clasificar", nodo_clasificar)
    g.add_node("extraer_texto", nodo_extraer_texto)
    g.add_node("extraer_tesseract", nodo_extraer_tesseract)
    g.add_node("extraer_vision", nodo_extraer_vision)
    g.add_node("estructurar", nodo_estructurar)
    g.add_node("guardar_disco", nodo_guardar_disco)
    g.add_node("preparar_guardado", nodo_preparar_guardado)
    g.add_node("persistir", nodo_persistir)

    g.add_edge(START, "clasificar")
    g.add_conditional_edges(
        "clasificar", rutear, ["extraer_texto", "extraer_tesseract", "extraer_vision"]
    )
    for nodo in ("extraer_texto", "extraer_tesseract", "extraer_vision"):
        g.add_edge(nodo, "estructurar")
    g.add_edge("estructurar", "guardar_disco")
    g.add_edge("guardar_disco", "preparar_guardado")
    g.add_edge("preparar_guardado", "persistir")
    g.add_edge("persistir", END)
    return g.compile()


def ingestar_pdf(
    ruta_pdf: str,
    metodo: Metodo | None = None,
    sin_persistir: bool = False,
    paginas: str | None = None,
    reusar_texto: bool = False,
    consorcio_id: str | None = None,
    forzar: bool = False,
    usuario_id: int | None = None,
) -> EstadoIngesta:
    """Punto de entrada: corre el grafo sobre un PDF (o un rango de páginas) y lo guarda en la base del
    consorcio indicado, que es obligatorio.

    `final["estado"]` dice cómo terminó: `procesado`; `solo_disco` (sin_persistir o una corrida parcial,
    que nunca toca Postgres ni el bucket); `duplicado` (ya estaba en este consorcio: corta sin OCR ni
    modelo); `en_proceso` (otra carga del mismo PDF y consorcio en curso); `periodo_duplicado` (otro PDF
    del consorcio tiene esa liquidación); `tope_excedido` (se alcanzó el tope mensual: no se gastó nada).
    `documento_existente` siempre es del mismo consorcio.
    `forzar` reprocesa y reemplaza; `reusar_texto` implica reprocesar, pero no reemplaza otro PDF.
    `usuario_id` solo queda en `cargas`, que registra cómo terminó toda corrida que no sea solo de disco."""
    if not consorcio_id:
        raise ValueError("ingestar_pdf exige consorcio_id: no hay consorcio por defecto en la capa de datos")
    from ..observabilidad import trazar_corrida

    sha256 = sha256_archivo(ruta_pdf)
    solo_disco = sin_persistir or bool(paginas)  # una corrida parcial no debe pisar el documento completo
    entrada: EstadoIngesta = {
        "ruta_pdf": ruta_pdf, "metodo_forzado": metodo, "sin_persistir": solo_disco, "paginas_rango": paginas,
        "reusar_texto": reusar_texto, "forzar": forzar, "consorcio_id": consorcio_id, "sha256": sha256,
        "documento_id": id_documento(consorcio_id, sha256[:16]), "nombre_archivo": Path(ruta_pdf).name,
    }
    etiquetas = {"archivo": Path(ruta_pdf).name, "metodo_forzado": metodo or "auto", "paginas": paginas or "todas",
                 "consorcio": consorcio_id}
    with trazar_corrida("ingesta", etiquetas) as traza:
        final = _correr_solo_disco(entrada) if solo_disco else _correr_con_candado(entrada)
        traza.registrar(final)
    if not solo_disco:
        _registrar_carga(consorcio_id, usuario_id, final["estado"])
    return final


def _registrar_carga(consorcio_id: str, usuario_id: int | None, estado: str) -> None:
    """El documento ya quedó como quedó: si esto falla se pierde una estadística, no la carga."""
    from ..almacen import postgres

    try:
        postgres.registrar_carga(consorcio_id, usuario_id, estado)
    except Exception as e:
        log.warning("no se pudo registrar la carga (%s)", type(e).__name__)


def _tope_excedido_sin_postgres() -> bool:
    """Una corrida solo de disco tiene que poder andar sin Postgres: si no se puede leer el tope, sigue."""
    from ..tope import tope_excedido

    try:
        return tope_excedido()
    except Exception as e:
        log.warning("no se pudo leer el tope de gasto (%s); se sigue", type(e).__name__)
        return False


def _correr_solo_disco(entrada: EstadoIngesta) -> EstadoIngesta:
    if _tope_excedido_sin_postgres():
        return _corte(entrada, "tope_excedido")
    return {**construir_grafo().invoke(entrada), "estado": "solo_disco"}


def _correr_con_candado(entrada: EstadoIngesta) -> EstadoIngesta:
    from ..almacen import postgres
    from ..tope import tope_excedido

    consorcio_id, sha256 = entrada["consorcio_id"], entrada["sha256"]
    with postgres.candado_ingesta(consorcio_id, sha256) as obtenido:
        if not obtenido:
            return _corte(entrada, "en_proceso")
        # Dentro del candado: si no, una carga que acaba de terminar se vería como documento nuevo
        if not (entrada["forzar"] or entrada["reusar_texto"]):
            existente = postgres.buscar_documento(entrada["documento_id"], consorcio_id)
            if existente and existente.get("estado") == "incompleto":
                # Quedó guardado sin secciones porque falló `estructurar` (p. ej. sin cupo del modelo): no se
                # puede buscar en él. Subirlo de nuevo lo reprocesa reusando las páginas ya transcriptas.
                entrada = {**entrada, "reusar_texto": True}
            elif existente:
                return _corte(entrada, "duplicado", documento_existente=existente)
        if tope_excedido():  # después de `duplicado`, que no gasta y sigue contestando
            return _corte(entrada, "tope_excedido")
        if crudos.configurado():
            crudos.verificar_bucket()  # antes de gastar OCR o modelo
        else:
            log.warning("sin S3_ENDPOINT_URL: el PDF original queda solo en local, no se guarda el crudo")
        try:
            final = construir_grafo().invoke(entrada)
        except postgres.PeriodoDuplicado as e:  # otro PDF del período ganó la carrera
            _borrar_crudo_descartado(entrada)
            return _corte(entrada, "periodo_duplicado", documento_existente=e.existente)
    _borrar_crudos_reemplazados(final)
    return {**final, "estado": final.get("estado", "procesado")}


def _corte(entrada: EstadoIngesta, estado: str, **extra) -> EstadoIngesta:
    """Salida de una ingesta que no llegó a guardar: lleva lo que leen `traza.registrar`,
    `_imprimir_resumen` y `formatear_ingesta`."""
    return {"estado": estado, "consorcio_id": entrada["consorcio_id"], "documento_id": entrada["documento_id"],
            "nombre_archivo": entrada["nombre_archivo"], "errores": [], "costo": {}, **extra}


def _borrar_crudo_descartado(entrada: EstadoIngesta) -> None:
    """Saca el crudo que subió una ingesta rechazada. Si el documento ya existía (`--reusar-texto`
    sobre un PDF cargado), esa clave es la de su fila y el objeto no se toca."""
    from ..almacen import postgres

    if crudos.configurado() and not postgres.buscar_documento(entrada["documento_id"], entrada["consorcio_id"]):
        crudos.borrar_crudo(crudos.clave_crudo(entrada["consorcio_id"], entrada["sha256"]))


def _borrar_crudos_reemplazados(final: EstadoIngesta) -> None:
    """Después del commit: si falla, queda un objeto huérfano en el bucket, con warning."""
    reemplazado = ((final.get("persistido") or {}).get("postgres") or {}).get("reemplazado")
    if not reemplazado:
        return
    for ficha in (reemplazado, *reemplazado.get("otros", [])):
        if ficha["crudo_clave"] and ficha["crudo_clave"] != final.get("crudo_clave"):
            crudos.borrar_crudo(ficha["crudo_clave"])


# ------------------------------------------------------------------ utilidades


def sha256_archivo(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def hash_archivo(ruta: str) -> str:
    return sha256_archivo(ruta)[:16]


def slug(nombre: str) -> str:
    base = Path(nombre).stem.lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    return base[:60] or "documento"


def carpeta_procesados(estado: EstadoIngesta) -> Path:
    nombre = f"{slug(estado['nombre_archivo'])}-{hash_de(estado['documento_id'])[:8]}"
    if estado.get("consorcio_id"):  # evaluar-ocr no lo pasa: no guarda nada en ningún consorcio
        nombre += "-" + re.sub(r"[^A-Za-z0-9_-]+", "-", estado["consorcio_id"])
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
