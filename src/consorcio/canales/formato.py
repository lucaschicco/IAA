"""Cómo se le muestran al usuario los resultados de las tools. Funciones puras: sin red y sin bases
de datos. Las usan los canales y el agente, y por eso no dependen de ninguno."""
from __future__ import annotations

LIMITE_MENSAJE = 4000  # Telegram corta en 4096


def _recortar(texto: str, limite: int = LIMITE_MENSAJE) -> str:
    return texto if len(texto) <= limite else texto[: limite - 20].rstrip() + "\n[… recortado]"


def _pesos(v) -> str:
    return "—" if v is None else f"$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def formatear_ingesta(final: dict) -> str:
    """Resumen corto del estado final de `ingestar_pdf` para mandar por chat."""
    estado = final.get("estado")
    nombre = final.get("nombre_archivo", "documento")
    existente = final.get("documento_existente") or {}  # siempre del consorcio de este chat
    previo = " · ".join(str(x) for x in (existente.get("nombre_archivo"), existente.get("titulo")) if x)
    if estado == "duplicado":
        return _recortar(f"Ese PDF ya estaba cargado en esta base: {previo or nombre}. No lo procesé de nuevo.")
    if estado == "en_proceso":
        return _recortar(f"{nombre} ya se está procesando en esta base. Te alcanza con esperar a que termine.")
    if estado == "periodo_duplicado":
        periodo = existente.get("periodo") or "de este PDF"
        return _recortar(f"No guardé {nombre}: esta base ya tiene la liquidación del período {periodo}"
                         + (f" ({previo})" if previo else "")
                         + ". Para reemplazarla hay que reprocesarlo con --forzar desde la consola.")
    if estado == "tope_excedido":
        return _recortar(f"No procesé {nombre}: se alcanzó el tope mensual de gasto en modelos.")
    ext = final.get("extraccion", {}) or {}
    meta = ext.get("metadatos", {}) or {}
    clas = final.get("clasificacion", {}) or {}
    costo = final.get("costo", {}) or {}
    lineas = [f"Listo: {final.get('nombre_archivo', 'documento')}"]
    if meta.get("titulo"):
        lineas.append(f"Título: {meta['titulo']}")
    if meta.get("tipo_documento"):
        lineas.append(f"Tipo: {meta['tipo_documento']}" + (f" · fecha {meta['fecha_documento']}" if meta.get("fecha_documento") else ""))
    if clas.get("metodo"):
        lineas.append(f"Método: {clas['metodo']} ({clas.get('motivo', '')})".rstrip("( )"))
    n = {k: len(ext.get(k, []) or []) for k in ("secciones", "personas", "eventos", "montos")}
    lineas.append(f"Secciones: {n['secciones']} · personas: {n['personas']} · eventos: {n['eventos']} · montos: {n['montos']}")
    liq = final.get("liquidacion")
    if liq:
        lineas.append(
            f"Liquidación {liq.get('periodo')}: {len(liq.get('rubros', []))} rubros, {len(liq.get('gastos', []))} gastos, "
            f"{liq.get('filas_prorrateo')} unidades ({liq.get('filas_no_parseadas', 0)} filas sin parsear)"
        )
    usd = costo.get("costo_usd")
    lineas.append(
        f"{costo.get('llamadas', 0)} llamadas · {costo.get('tokens_entrada', 0)} + {costo.get('tokens_salida', 0)} tokens"
        + (f" · {usd:.4f} USD" if usd is not None else " · costo sin precio")
        + f" · {final.get('segundos_total', 0)} s"
    )
    if final.get("persistido"):
        lineas.append("Guardado en Postgres; ya se puede preguntar sobre esto.")
    for e in final.get("errores", []) or []:
        lineas.append(f"⚠ {e}")
    return _recortar("\n".join(lineas))


def formatear_respuesta(final: dict) -> str:
    """Respuesta de `preguntar` tal como la ve el vecino: texto, cita y fuente, o derivación.

    No reutiliza `mensaje` (que ya trae fuente y cita en formato de consola): arma el texto desde
    los campos estructurados para que el chat muestre siempre lo mismo.
    """
    if final.get("derivada") or not final.get("respuesta"):
        return _recortar(final.get("mensaje") or "")
    r = final.get("respuesta", {}) or {}
    fuente = final.get("fuente") or {}
    partes = [r.get("respuesta") or final.get("mensaje") or ""]
    # cada cita con su propia fuente: pueden venir de documentos distintos
    citas = r.get("citas") or ([{**fuente, "cita": r["cita"], "pagina": r.get("pagina")}] if r.get("cita") else [])
    for c in citas:
        partes.append(f"\n«{c['cita']}»")
        pie = [str(x) for x in (c.get("nombre_archivo"), c.get("titulo")) if x]
        if c.get("pagina"):
            pie.append(f"pág. {c['pagina']}")
        if pie:
            partes.append("Fuente: " + " · ".join(pie))
    if final.get("verificada"):
        partes.append("(citas verificadas contra el documento original)" if len(citas) > 1
                      else "(cita verificada contra el documento original)")
    return _recortar("\n".join(partes))


def formatear_expensas(que: str, filas: list[dict]) -> str:
    if not filas:
        return "No hay datos para esa consulta. Probá /expensas resumen para ver los períodos cargados."
    out: list[str] = []
    if que == "resumen":
        out.append("Liquidaciones cargadas:")
        for f in filas:
            out.append(f"• {f['periodo']}: total {_pesos(f['total_gastos'])} · {f['rubros']} rubros · {f['unidades']} unidades · vence {f['primer_vencimiento']}")
    elif que == "rubros":
        periodo = None
        for f in filas:
            if f["periodo"] != periodo:
                periodo = f["periodo"]
                out.append(f"\nRubros {periodo}:")
            pct = f" ({f['porcentaje']:.1f} %)" if f.get("porcentaje") is not None else ""
            out.append(f"• {f['rubro']}: {_pesos(f['total'])}{pct}")
    elif que == "gastos":
        for f in filas[:40]:
            quien = " · ".join(x for x in (f.get("proveedor"), f.get("concepto")) if x)
            out.append(f"• {f['periodo']} {f['rubro']}: {quien} {_pesos(f['importe'])}")
        if len(filas) > 40:
            out.append(f"… y {len(filas) - 40} gastos más; filtrá con un período o un texto.")
    elif que == "unidades":
        for f in filas[:30]:
            out.append(
                f"• {f['periodo']} UF {f['uf']} ({f['piso_depto']}, {f['porcentual']} %): expensas {_pesos(f['expensas_ordinarias'])}"
                f" · total {_pesos(f['total_expensa'])} · a pagar {_pesos(f['a_pagar'])}"
            )
        if len(filas) > 30:
            out.append(f"… y {len(filas) - 30} unidades más; indicá la unidad (5-D) o el período.")
    return _recortar("\n".join(out).strip())


def formatear_documentos(docs: list[dict]) -> str:
    if not docs:
        return "No hay documentos cargados todavía. Mandame un PDF."
    out = [f"{len(docs)} documentos:"]
    for d in docs:
        marca = "" if d.get("activo", True) else " (fuera del buscador)"
        out.append(f"• {d.get('archivo')} · {d.get('tipo') or '?'} · {d.get('paginas') or '?'} pág.{marca}")
    return _recortar("\n".join(out))
