"""Búsqueda de secciones en Postgres y armado del contexto para el modelo.

Dos métodos, para medir uno contra otro con `consorcio evaluar-consultas`:
- `tsvector`: búsqueda de texto completo de Postgres en español (stemming + ranking
  `ts_rank_cd`) sobre título, tema y resumen de cada sección. Es el método del sistema.
- `palabras`: baseline. Cuenta cuántas palabras de la pregunta aparecen literalmente
  (sin stemming) en la sección y ordena por esa cuenta.

Decisión (26/10/2026): empezar con búsqueda léxica, sin embeddings. Las secciones ya
tienen título, tema y resumen escritos por el modelo al ingestar, que funcionan como
un índice "semántico" barato. Si la evaluación muestra que no alcanza, el paso
siguiente es pgvector sobre la misma tabla.

La cita no se toma del resumen (que escribió el modelo) sino del texto original de las
páginas: por eso `armar_contexto` trae las páginas de cada sección desde `paginas`.
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..almacen.postgres import Documento, Pagina, Seccion, crear_tablas, engine
from ..config import config
from ..texto import normalizar, palabras_clave

MAX_CHARS_POR_PAGINA = 6_000
PAGINAS_SIN_UBICACION = 3  # si la sección no trae página, se usan las primeras N del documento

_COLUMNAS = """
    s.id AS seccion_id, s.documento_id, d.nombre_archivo, d.titulo AS documento, d.tipo_documento,
    d.fecha_documento, s.titulo, s.tema, s.resumen, s.pagina_inicio, s.pagina_fin
"""
_TSV = "to_tsvector('spanish', coalesce(s.titulo,'') || ' ' || coalesce(s.tema,'') || ' ' || coalesce(s.resumen,''))"


def buscar_secciones(
    pregunta: str, k: int = 3, metodo: str = "tsvector", consorcio_id: str | None = None
) -> list[dict]:
    """Busca solo en los documentos activos del consorcio (por defecto, `config.consorcio_por_defecto`)."""
    crear_tablas()
    cid = consorcio_id or config.consorcio_por_defecto
    if metodo == "tsvector":
        return _buscar_tsvector(pregunta, k, cid)
    if metodo == "palabras":
        return _buscar_palabras(pregunta, k, cid)
    raise ValueError(f"método de búsqueda desconocido: {metodo!r}")


def _buscar_tsvector(pregunta: str, k: int, cid: str) -> list[dict]:
    with engine().connect() as con:
        filas = con.execute(
            text(
                f"SELECT {_COLUMNAS}, ts_rank_cd({_TSV}, q) AS puntaje "
                f"FROM secciones s JOIN documentos d ON d.id = s.documento_id, "
                f"websearch_to_tsquery('spanish', :pregunta) q "
                f"WHERE d.activo AND d.consorcio_id = :cid AND {_TSV} @@ q ORDER BY puntaje DESC, s.id LIMIT :k"
            ),
            {"pregunta": pregunta, "k": k, "cid": cid},
        ).mappings().all()
        if not filas:
            # websearch_to_tsquery exige todas las palabras; si no hay nada, probamos con "o"
            palabras = palabras_clave(pregunta)
            if not palabras:
                return []
            filas = con.execute(
                text(
                    f"SELECT {_COLUMNAS}, ts_rank_cd({_TSV}, q) AS puntaje "
                    f"FROM secciones s JOIN documentos d ON d.id = s.documento_id, "
                    f"to_tsquery('spanish', :consulta) q "
                    f"WHERE d.activo AND d.consorcio_id = :cid AND {_TSV} @@ q ORDER BY puntaje DESC, s.id LIMIT :k"
                ),
                {"consulta": " | ".join(palabras), "k": k, "cid": cid},
            ).mappings().all()
    return [dict(f) | {"puntaje": round(float(f["puntaje"]), 4), "metodo": "tsvector"} for f in filas]


def _buscar_palabras(pregunta: str, k: int, cid: str) -> list[dict]:
    palabras = palabras_clave(pregunta)
    if not palabras:
        return []
    with Session(engine()) as s:
        filas = s.execute(
            select(Seccion, Documento).join(Documento, Documento.id == Seccion.documento_id)
            .where(Documento.activo, Documento.consorcio_id == cid).order_by(Seccion.id)
        ).all()
    puntuadas = []
    for sec, doc in filas:
        texto_sec = normalizar(f"{sec.titulo} {sec.tema} {sec.resumen}")
        puntaje = sum(1 for p in palabras if p in texto_sec)
        if puntaje:
            puntuadas.append((puntaje, sec, doc))
    puntuadas.sort(key=lambda t: (-t[0], t[1].id))
    return [
        {
            "seccion_id": sec.id, "documento_id": sec.documento_id, "nombre_archivo": doc.nombre_archivo,
            "documento": doc.titulo, "tipo_documento": doc.tipo_documento, "fecha_documento": doc.fecha_documento,
            "titulo": sec.titulo, "tema": sec.tema, "resumen": sec.resumen,
            "pagina_inicio": sec.pagina_inicio, "pagina_fin": sec.pagina_fin,
            "puntaje": puntaje, "metodo": "palabras",
        }
        for puntaje, sec, doc in puntuadas[:k]
    ]


def paginas_de(documento_id: str, desde: int | None, hasta: int | None) -> list[dict]:
    """Texto original de las páginas de una sección (para citar y para verificar la cita)."""
    with Session(engine()) as s:
        q = select(Pagina).where(Pagina.documento_id == documento_id)
        if desde is None:
            q = q.where(Pagina.numero <= PAGINAS_SIN_UBICACION)
        else:
            q = q.where(Pagina.numero >= desde, Pagina.numero <= (hasta or desde))
        filas = s.scalars(q.order_by(Pagina.numero)).all()
    return [{"numero": p.numero, "texto": p.texto[:MAX_CHARS_POR_PAGINA]} for p in filas]


def armar_contexto(resultados: list[dict]) -> tuple[str, dict[int, list[dict]]]:
    """Devuelve el texto que recibe el modelo y, por número de fuente, sus páginas
    (lo que después se usa para verificar la cita)."""
    bloques = []
    paginas_por_fuente: dict[int, list[dict]] = {}
    for i, r in enumerate(resultados, start=1):
        paginas = paginas_de(r["documento_id"], r["pagina_inicio"], r["pagina_fin"])
        paginas_por_fuente[i] = paginas
        cuerpo = "\n\n".join(f"=== Página {p['numero']} ===\n{p['texto']}" for p in paginas) or "(sin texto)"
        bloques.append(
            f"[Fuente {i}] Documento: {r['documento']} ({r['tipo_documento']}, fecha {r['fecha_documento'] or 'sin fecha'})\n"
            f"Sección: {r['titulo']} · tema: {r['tema']}\n"
            f"Resumen de la sección (orientativo, no citar): {r['resumen']}\n"
            f"Texto de las páginas:\n{cuerpo}"
        )
    return "\n\n----------\n\n".join(bloques), paginas_por_fuente


def verificar_cita(cita: str | None, paginas: list[dict], minimo: int = 15) -> tuple[bool, str]:
    """La cita tiene que aparecer, normalizada, dentro del texto original de las páginas."""
    if not cita:
        return False, "sin cita"
    c = normalizar(cita)
    if len(c) < minimo:
        return False, "cita demasiado corta"
    texto = normalizar(" ".join(p["texto"] for p in paginas))
    if c in texto:
        return True, "cita encontrada en el texto original"
    return False, "la cita no aparece textualmente en las páginas de la fuente"
