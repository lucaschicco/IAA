"""Evaluación de las consultas contra `datos/evaluacion/preguntas.csv`.

Columnas del CSV: id, particion (dev|test), pregunta, documento (fragmento del nombre
del archivo esperado), seccion_clave (palabra o frase que tiene que aparecer en el título
o resumen de la sección esperada), debe_derivar (si|no: preguntas que el sistema NO debe
responder, p. ej. el DNI de un vecino), respuesta_esperada (texto libre, para revisión).

Mide dos cosas por separado, como pide la consigna:
1. Retrieval: para cada método de búsqueda (tsvector = sistema, palabras = baseline),
   si la sección esperada está entre las k recuperadas (recall@k) y en qué posición (MRR).
2. Generación (opcional, gasta tokens): cuántas preguntas se respondieron, cuántas citas
   se verificaron y cuánto costó. Revisa la respuesta igual que el agente (`validar_respuesta`),
   así que mide lo que ve el vecino en la web; las corridas anteriores al 02/11 no la revisaban.

La corrida con modelo sobre test se guarda sola en `docs/evaluaciones/`: es lo que lee el CRM.
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from ..config import RAIZ_REPO, config
from ..texto import normalizar
from .buscador import buscar_secciones

log = logging.getLogger("consorcio.evaluar")

METODOS = ("tsvector", "palabras")
DIR_EVALUACIONES = RAIZ_REPO / "docs" / "evaluaciones"


def leer_preguntas(ruta_csv: str, particion: str = "dev") -> list[dict]:
    with open(ruta_csv, encoding="utf-8", newline="") as f:
        filas = [dict(r) for r in csv.DictReader(f)]
    if particion != "todas":
        filas = [r for r in filas if r["particion"] == particion]
    return filas


def es_acierto(resultado: dict, esperado: dict) -> bool:
    # `documento` admite alternativas separadas por "|": el acta del 30/04/2026 transcribe los
    # artículos del reglamento de cocheras, así que cualquiera de los dos es una fuente válida
    archivo = normalizar(resultado["nombre_archivo"])
    doc_ok = any(normalizar(d) in archivo for d in esperado["documento"].split("|") if d.strip())
    clave = normalizar(esperado["seccion_clave"])
    sec_ok = not clave or clave in normalizar(f"{resultado['titulo']} {resultado['resumen']}")
    return doc_ok and sec_ok


def debe_derivar(q: dict) -> bool:
    return (q.get("debe_derivar") or "no").strip().lower() in ("si", "sí", "true", "1")


def evaluar_retrieval(preguntas: list[dict], k: int = 3, consorcio_id: str | None = None) -> dict:
    preguntas = [q for q in preguntas if not debe_derivar(q)]  # las "trampa" no miden retrieval
    por_metodo: dict[str, dict] = {}
    detalle = []
    for m in METODOS:
        aciertos = 0
        mrr = 0.0
        for q in preguntas:
            res = buscar_secciones(q["pregunta"], k, m, consorcio_id)
            rango = next((i for i, r in enumerate(res, start=1) if es_acierto(r, q)), None)
            if rango:
                aciertos += 1
                mrr += 1 / rango
            detalle.append({"id": q["id"], "metodo": m, "rango": rango,
                            "recuperadas": [f"{r['nombre_archivo'][:30]} · {r['titulo'][:50]}" for r in res]})
        n = max(len(preguntas), 1)
        por_metodo[m] = {"recall_at_k": round(aciertos / n, 3), "mrr": round(mrr / n, 3), "aciertos": aciertos, "n": len(preguntas)}
    return {"k": k, "por_metodo": por_metodo, "detalle": detalle}


def evaluar_generacion(
    preguntas: list[dict], k: int = 3, metodo: str = "tsvector", consorcio_id: str | None = None
) -> dict:
    from .grafo import preguntar

    filas = []
    for q in preguntas:
        final = preguntar(q["pregunta"], k=k, metodo_busqueda=metodo, origen="evaluacion", consorcio_id=consorcio_id,
                          validar_respuesta=True)
        fuente = final.get("fuente") or {}
        filas.append(
            {
                "id": q["id"], "pregunta": q["pregunta"], "derivada": final.get("derivada"),
                "verificada": final.get("verificada"), "motivo": final.get("motivo"),
                "debe_derivar": debe_derivar(q),
                "fuente_correcta": es_acierto(fuente, q) if fuente else False,
                "correcta": bool(final.get("derivada")) if debe_derivar(q) else (es_acierto(fuente, q) if fuente else False),
                "respuesta": (final.get("respuesta") or {}).get("respuesta"),
                "cita": (final.get("respuesta") or {}).get("cita"),
                "respuesta_esperada": q.get("respuesta_esperada"),
                "costo_usd": (final.get("costo") or {}).get("costo_usd"), "segundos": final.get("segundos"),
            }
        )
    n = max(len(filas), 1)
    costos = [f["costo_usd"] for f in filas if f["costo_usd"] is not None]
    return {
        "n": len(filas),
        "respondidas": sum(1 for f in filas if not f["derivada"]),
        "tasa_respondidas": round(sum(1 for f in filas if not f["derivada"]) / n, 3),
        "tasa_citas_verificadas": round(sum(1 for f in filas if f["verificada"]) / n, 3),
        "tasa_fuente_correcta": round(sum(1 for f in filas if f["fuente_correcta"]) / n, 3),
        "tasa_correctas": round(sum(1 for f in filas if f["correcta"]) / n, 3),
        "derivadas_cuando_debia": sum(1 for f in filas if f["debe_derivar"] and f["derivada"]),
        "debian_derivar": sum(1 for f in filas if f["debe_derivar"]),
        "costo_usd_total": round(sum(costos), 4) if costos else None,
        "segundos_promedio": round(sum(f["segundos"] or 0 for f in filas) / n, 1),
        "detalle": filas,
    }


def evaluar_consultas(
    ruta_csv: str, particion: str = "dev", k: int = 3, con_modelo: bool = False, consorcio_id: str | None = None
) -> dict:
    """Evalúa sobre la base de un consorcio (el set de `datos/evaluacion/` es de Mercedes 274)."""
    from ..observabilidad import trazar_corrida

    consorcio_id = consorcio_id or config.consorcio_por_defecto
    preguntas = leer_preguntas(ruta_csv, particion)
    salida: dict = {"archivo": Path(ruta_csv).name, "particion": particion, "n": len(preguntas), "consorcio": consorcio_id}
    with trazar_corrida("evaluacion-consultas", {"particion": particion, "k": k, "con_modelo": con_modelo,
                                                 "consorcio": consorcio_id}) as traza:
        salida["retrieval"] = evaluar_retrieval(preguntas, k, consorcio_id)
        metricas = {f"recall_{m}": v["recall_at_k"] for m, v in salida["retrieval"]["por_metodo"].items()}
        metricas |= {f"mrr_{m}": v["mrr"] for m, v in salida["retrieval"]["por_metodo"].items()}
        if con_modelo:
            salida["generacion"] = evaluar_generacion(preguntas, k, consorcio_id=consorcio_id)
            g = salida["generacion"]
            metricas |= {
                "tasa_respondidas": g["tasa_respondidas"], "tasa_citas_verificadas": g["tasa_citas_verificadas"],
                "tasa_fuente_correcta": g["tasa_fuente_correcta"], "tasa_correctas": g["tasa_correctas"],
                "costo_usd_total": g["costo_usd_total"] or 0.0,
            }
        traza.registrar_metricas({"n_preguntas": str(len(preguntas))}, metricas)
    return salida


def guardar_evaluacion(salida: dict) -> Path | None:
    """Guarda `<fecha>-consultas-test.json` si la corrida vale para el CRM: con modelo real, sobre test y
    sobre el consorcio del set. Una del fake o de otro consorcio mostraría como exactitud del sistema algo
    que no lo es, y de un mismo día queda la última. Sin `detalle`, que trae preguntas y respuestas: el
    repo es público."""
    if salida["particion"] != "test" or "generacion" not in salida:
        return None
    if config.llm_provider == "fake" or salida["consorcio"] != config.consorcio_por_defecto:
        log.warning("corrida no guardada: el CRM solo muestra las de un modelo real sobre %s", config.consorcio_por_defecto)
        return None
    sin_detalle = {
        **salida, "proveedor": config.llm_provider, "modelo": config.modelo,
        "retrieval": {k: v for k, v in salida["retrieval"].items() if k != "detalle"},
        "generacion": {k: v for k, v in salida["generacion"].items() if k != "detalle"},
    }
    ruta = DIR_EVALUACIONES / f"{datetime.now(UTC).date().isoformat()}-consultas-test.json"
    ruta.write_text(json.dumps(sin_detalle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ruta
