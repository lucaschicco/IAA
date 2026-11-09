"""Normalización de texto compartida (búsqueda, verificación de citas, evaluación)."""
from __future__ import annotations

import re
import unicodedata

# Palabras que no aportan a una búsqueda por palabras clave (baseline)
PALABRAS_VACIAS = {
    "para", "como", "cual", "cuales", "donde", "cuando", "quien", "quienes", "este", "esta",
    "estos", "estas", "sobre", "entre", "desde", "hasta", "puedo", "puede", "pueden", "tiene",
    "tienen", "hacer", "debe", "deben", "dice", "dicen", "tengo", "hay", "que", "los",
    "las", "del", "una", "uno", "con", "por", "sin", "mas", "menos", "ser", "son",
}


def id_documento(consorcio_id: str, hash_archivo: str) -> str:
    """Id de un documento en Postgres: `<consorcio>:<hash>`. El mismo PDF en dos consorcios
    son dos documentos (cada consorcio tiene su base); dentro de uno, la hash lo hace idempotente."""
    return f"{consorcio_id}:{hash_archivo}"


def hash_de(documento_id: str) -> str:
    return documento_id.rsplit(":", 1)[-1]


def consorcio_de_chat_id(chat_id: int) -> str:
    """Slug de la base propia de un número/grupo de Telegram (los grupos tienen id negativo)."""
    return f"chat-{chat_id}" if chat_id >= 0 else f"grupo-{-chat_id}"


def normalizar(texto: str) -> str:
    """Minúsculas, sin acentos, solo letras/números/espacios, espacios colapsados."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9ñ\s]", " ", t)
    return " ".join(t.split())


def palabras_clave(texto: str, minimo: int = 4) -> list[str]:
    """Palabras normalizadas de al menos `minimo` letras, sin las vacías, sin repetir."""
    vistas: list[str] = []
    for p in normalizar(texto).split():
        if len(p) >= minimo and p not in PALABRAS_VACIAS and p not in vistas:
            vistas.append(p)
    return vistas
