"""Carga de skills: instrucciones versionadas en Markdown que el modelo recibe
como contexto (práctica, diseño 2). Iterar la receta es editar el .md, no el código.

Formato de `skills/<nombre>/SKILL.md`: secciones `## <clave>` ; `cargar_skill("clave")`
devuelve el texto de esa sección.
"""
from __future__ import annotations

import re
from functools import cache
from pathlib import Path

from ..config import RAIZ_REPO

DIR_SKILLS = RAIZ_REPO / "skills"


@cache
def _leer(nombre_skill: str) -> dict[str, str]:
    ruta = DIR_SKILLS / nombre_skill / "SKILL.md"
    texto = ruta.read_text(encoding="utf-8")
    secciones: dict[str, str] = {}
    actual = None
    for linea in texto.splitlines():
        m = re.match(r"^##\s+(\S+)", linea)
        if m:
            actual = m.group(1).strip()
            secciones[actual] = ""
        elif actual:
            secciones[actual] += linea + "\n"
    return {k: v.strip() for k, v in secciones.items()}


def cargar_skill(seccion: str, nombre_skill: str = "ingesta") -> str:
    secciones = _leer(nombre_skill)
    if seccion not in secciones:
        raise KeyError(f"La sección '{seccion}' no existe en skills/{nombre_skill}/SKILL.md")
    return secciones[seccion]


def version_skill(nombre_skill: str = "ingesta") -> str:
    m = re.search(r"^version:\s*(\S+)", (DIR_SKILLS / nombre_skill / "SKILL.md").read_text(encoding="utf-8"), re.MULTILINE)
    return m.group(1) if m else "sin-version"


def ruta_skill(nombre_skill: str = "ingesta") -> Path:
    return DIR_SKILLS / nombre_skill / "SKILL.md"
