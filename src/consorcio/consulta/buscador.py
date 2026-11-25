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

import re
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..almacen.postgres import Documento, Pagina, Seccion, crear_tablas, engine, exigir_consorcio
from ..texto import normalizar, palabras_clave

MAX_CHARS_POR_PAGINA = 6_000
PAGINAS_SIN_UBICACION = 3  # si la sección no trae página, se usan las primeras N del documento
MAX_PALABRAS_SIN_RESPALDO = 0.25  # sin calibrar contra un modelo real: no hay clave todavía
_LARGO_RAIZ = 5  # "tenencia" y "tenencias" cuentan igual, sin stemmer
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
# Un monto se reconoce por su moneda o por su escala: «$ 15», «15 pesos», «USD 15», «15 mil», «15 M», «15k»
_MONTO = re.compile(
    r"(?P<usd>u\$s|us\$|usd)?\s*(?P<signo>\$)?\s*(?P<numero>\d+(?:[.,]\d+)*)\s*"
    r"(?P<escala>k\b|(?-i:M)\b|mil\b|mill[oó]n(?:es)?\b)?\s*(?:de\s+)?(?P<moneda>pesos\b|d[oó]lares\b|usd\b)?",
    re.IGNORECASE,
)
_ESCALAS = {"k": 1_000, "mil": 1_000, "m": 1_000_000, "millon": 1_000_000, "millones": 1_000_000}
# En letras, ya normalizado: «uno», «una» y «un» quedan afuera porque casi nunca son cantidades
_PALABRAS_NUMERO = {
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17,
    "dieciocho": 18, "diecinueve": 19, "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60,
    "setenta": 70, "ochenta": 80, "noventa": 90, "cien": 100, "ciento": 100, "doscientos": 200,
    "trescientos": 300, "cuatrocientos": 400, "quinientos": 500, "seiscientos": 600, "setecientos": 700,
    "ochocientos": 800, "novecientos": 900, "mil": 1_000, "millon": 1_000_000, "millones": 1_000_000,
    "billon": 10**12, "billones": 10**12,
}
_LETRAS = "|".join(_PALABRAS_NUMERO)
_MONTO_EN_LETRAS = re.compile(rf"\b(?:{_LETRAS})(?:\s+(?:{_LETRAS}|y))*\s+(?:de\s+)?(?:pesos|dolares)\b")
# Palabras que arman una respuesta sin afirmar nada del documento
_PALABRAS_DE_ENLACE = {
    "podes", "poder", "podria", "tener", "debes", "deber", "segun", "porque", "pero", "aunque", "tambien",
    "solo", "cada", "todo", "toda", "todos", "todas", "otro", "otra", "otros", "otras", "ademas", "entonces",
    "pesos",
    # cómo habla `redactar` de sus citas cuando no alcanzan ("las citas no muestran…"): no es contenido (25/11)
    "cita", "citas", "muestra", "muestran", "aparece", "aparecen", "encuentro", "indica", "indican", "menciona",
    "mencionan", "confirmar", "detalla", "detallan", "surge", "surgen", "ninguna", "ninguno", "tampoco",
}

_COLUMNAS = """
    s.id AS seccion_id, s.documento_id, d.nombre_archivo, d.titulo AS documento, d.tipo_documento,
    d.fecha_documento, s.titulo, s.tema, s.resumen, s.pagina_inicio, s.pagina_fin
"""
_TSV = "to_tsvector('spanish', coalesce(s.titulo,'') || ' ' || coalesce(s.tema,'') || ' ' || coalesce(s.resumen,''))"


def buscar_secciones(
    pregunta: str, k: int = 3, metodo: str = "tsvector", consorcio_id: str | None = None
) -> list[dict]:
    """Busca solo en los documentos activos del consorcio, que es obligatorio."""
    cid = exigir_consorcio(consorcio_id)
    crear_tablas()
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


def paginas_de(
    documento_id: str, desde: int | None, hasta: int | None, consorcio_id: str | None = None
) -> list[dict]:
    """Texto original de las páginas de una sección (para citar y para verificar la cita).
    Un documento de otro consorcio no tiene páginas para quien pregunta."""
    cid = exigir_consorcio(consorcio_id)
    with Session(engine()) as s:
        q = select(Pagina).join(Documento, Documento.id == Pagina.documento_id).where(
            Pagina.documento_id == documento_id, Documento.consorcio_id == cid
        )
        if desde is None:
            q = q.where(Pagina.numero <= PAGINAS_SIN_UBICACION)
        else:
            q = q.where(Pagina.numero >= desde, Pagina.numero <= (hasta or desde))
        filas = s.scalars(q.order_by(Pagina.numero)).all()
    return [{"numero": p.numero, "texto": p.texto[:MAX_CHARS_POR_PAGINA]} for p in filas]


def armar_contexto(
    resultados: list[dict], consorcio_id: str | None = None
) -> tuple[str, dict[int, list[dict]]]:
    """Devuelve el texto que recibe el modelo y, por número de fuente, sus páginas
    (lo que después se usa para verificar la cita)."""
    cid = exigir_consorcio(consorcio_id)
    bloques = []
    paginas_por_fuente: dict[int, list[dict]] = {}
    for i, r in enumerate(resultados, start=1):
        paginas = paginas_de(r["documento_id"], r["pagina_inicio"], r["pagina_fin"], cid)
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


def texto_de_fuentes(resultados: list[dict], paginas_por_fuente: dict[int, list[dict]]) -> str:
    """Lo que el modelo recibió como dato. Sin el resumen, que también lo escribió un modelo."""
    partes = [
        f"{r['documento']} {r['nombre_archivo']} {r['titulo']} {r['tema']} {r['fecha_documento'] or ''}" for r in resultados
    ]
    for paginas in paginas_por_fuente.values():
        partes += [f"{p['numero']} {p['texto']}" for p in paginas]
    return " ".join(partes)


def _valor(numero: str) -> Decimal | str:
    """Valor en formato argentino: el punto separa miles y la coma, decimales. «1.500» y «1.500,00» son el
    mismo número, y «1,500» es 1,5. Lo que no es un número, como una fecha, se compara tal cual."""
    enteros, coma, decimales = numero.partition(",")
    if "," in decimales or "." in decimales:
        return numero
    if "." in enteros and not re.fullmatch(r"\d{1,3}(?:\.\d{3})+", enteros):
        return numero if coma or enteros.count(".") > 1 else Decimal(enteros)
    return Decimal(f"{enteros.replace('.', '')}.{decimales or 0}")


def _numeros(texto: str) -> set[Decimal | str]:
    return {_valor(n) for n in _NUMERO.findall(texto)}


def _cantidades(texto: str) -> set[Decimal | str]:
    """Las cifras y las cantidades en letras de un texto."""
    letras = {Decimal(_PALABRAS_NUMERO[p]) for p in normalizar(texto).split() if p in _PALABRAS_NUMERO}
    return _numeros(texto) | letras


def _montos(texto: str) -> set[tuple[str, Decimal | str]]:
    """Moneda y valor de cada monto; los números sueltos no son montos."""
    montos = set()
    for m in _MONTO.finditer(texto):
        if not (m["usd"] or m["signo"] or m["escala"] or m["moneda"]):
            continue
        valor = _valor(m["numero"])
        if m["escala"] and isinstance(valor, Decimal):
            valor *= _ESCALAS[normalizar(m["escala"])]
        en_dolares = m["usd"] or normalizar(m["moneda"] or "") in ("dolares", "usd")
        montos.add(("usd" if en_dolares else "pesos", valor))
    return montos


def _monto_en_letras_ajeno(respuesta: str, cita: str) -> bool:
    return any(m.group() not in normalizar(cita) for m in _MONTO_EN_LETRAS.finditer(normalizar(respuesta)))


def verificar_respuesta(respuesta: str, cita: str, fuentes: str, pregunta: str = "") -> tuple[bool, str]:
    """Que la cita exista no prueba que la respuesta salga de ella. Los montos no los inventa el modelo:
    un monto tiene que estar en la cita, porque otro número de la misma página puede ser un horario, y
    cualquier otra cantidad, en las fuentes, en cifras o en letras. Las palabras pasan por una cuota, porque
    una respuesta legítima parafrasea; es un filtro de lo más burdo y no prueba que la respuesta se siga de
    la cita. Lo que falla se deriva, que es el lado seguro."""
    base = f"{cita} {fuentes}"
    if _montos(respuesta) - _montos(cita) or _monto_en_letras_ajeno(respuesta, cita):
        return False, "respuesta sin respaldo: trae montos que no están en la cita"
    if _cantidades(respuesta) - _cantidades(base):
        return False, "respuesta sin respaldo: trae números que no están en las fuentes"
    # retomar las palabras de la pregunta no es inventar; los números no se aceptan desde la pregunta
    raices = {p[:_LARGO_RAIZ] for p in palabras_clave(f"{base} {pregunta}")}
    palabras = [p for p in palabras_clave(respuesta) if p.isalpha() and p not in _PALABRAS_DE_ENLACE]
    ajenas = [p for p in palabras if p[:_LARGO_RAIZ] not in raices]
    if palabras and len(ajenas) / len(palabras) > MAX_PALABRAS_SIN_RESPALDO:
        return False, "respuesta sin respaldo: habla de cosas que no están en las fuentes"
    return True, "cita encontrada y respuesta respaldada por las fuentes"


def pagina_de_cita(cita: str, paginas: list[dict], declarada: int | None) -> int | None:
    """La página sale del texto, no del modelo. Una cita que cruza dos páginas no está entera en
    ninguna: ahí vale la declarada, solo si es una de las de la fuente."""
    c = normalizar(cita)
    encontrada = next((p["numero"] for p in paginas if c in normalizar(p["texto"])), None)
    if encontrada is not None:
        return encontrada
    return declarada if declarada in {p["numero"] for p in paginas} else None
