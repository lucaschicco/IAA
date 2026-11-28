"""Tapar datos personales en un texto antes de mandarlo a MLflow (opción B de docs/observabilidad.md;
spec en docs/2026-11-27-spec-privacidad-trazas.md).

Por patrón: mails, CUIT y CUIL, teléfonos, DNI, direcciones (después de "calle", "av.", "domicilio
en"…) y nombres (después de "Sra.", "vecino", "propietaria"…). Un nombre sin una de esas palabras
delante no se detecta. No tapa importes, períodos, fechas ni unidades: tienen que seguir legibles para
depurar.

No toca Postgres ni la web, donde el texto sigue completo detrás del login.
"""
from __future__ import annotations

import re
from typing import Any

_LETRA = "A-Za-zÁÉÍÓÚÜÑáéíóúüñ"
_MAYUSCULA = "A-ZÁÉÍÓÚÜÑ"

_MAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CUIT = re.compile(r"(?<![\d.,-])(?:20|23|24|27|30|33|34)-?\d{8}-?\d(?![\d])")
_TELEFONO = re.compile(
    r"(?<![\d.,$])"
    r"(?:"
    r"(?:\+?54[\s-]?)?(?:9[\s-]?)?(?:\(?0?\d{2,4}\)?[\s-]?)?(?:15[\s-]?)?\d{3,4}[\s-]\d{4}"
    r"|\d{10}"  # celular sin separadores: código de área y número
    r")(?![\d]|[.,]\d)"
)
# 7 u 8 dígitos, con o sin puntos. No es un importe si va después de "$" o sigue con decimales
_DNI = re.compile(r"(?<![\d.,$/-])(?<!\$ )\d{1,2}\.?\d{3}\.?\d{3}(?![\d]|[.,]\d)")

_PALABRA = rf"[{_LETRA}][{_LETRA}0-9.'°-]*"
# "calle", "av."… son parte de la dirección; "domicilio en" queda y se tapa lo que sigue
_DIRECCION = re.compile(
    rf"(?i:\b(?:calle|avenida|avda\.|av\.|pasaje|pje\.|diagonal)\s+)(?:{_PALABRA}\s+){{1,4}}?\d{{1,5}}\b"
)
_DOMICILIO = re.compile(
    rf"(?P<antes>(?i:\b(?:domicilio|domiciliad[oa])\s+en\s+))(?:{_PALABRA}\s+){{1,4}}?\d{{1,5}}\b"
)
# La palabra de antes queda y se tapan hasta tres palabras con mayúscula
_NOMBRE = re.compile(
    r"(?P<antes>(?i:(?:\bsrta\.|\bsra\.|\bsr\.|\bdra\.|\bdr\.|\bseñora\b|\bseñor\b|\bdoña\b|\bdon\b"
    r"|\bvecin[oa]\b|\bpropietari[oa]\b|\binquilin[oa]\b))\s+)"
    rf"[{_MAYUSCULA}][{_LETRA}'-]+(?:\s+[{_MAYUSCULA}][{_LETRA}'-]+){{0,2}}"
)


def tapar(texto: str) -> str:
    """Reemplaza los datos personales que detectan los patrones por [MAIL], [CUIT], [TELEFONO], [DNI],
    [DIRECCION] o [NOMBRE]. El orden importa: el CUIT antes que el DNI que lleva adentro."""
    texto = _MAIL.sub("[MAIL]", texto)
    texto = _CUIT.sub("[CUIT]", texto)
    texto = _TELEFONO.sub("[TELEFONO]", texto)
    texto = _DNI.sub("[DNI]", texto)
    texto = _DIRECCION.sub("[DIRECCION]", texto)
    texto = _DOMICILIO.sub(r"\g<antes>[DIRECCION]", texto)
    return _NOMBRE.sub(r"\g<antes>[NOMBRE]", texto)


def tapar_valor(valor: Any) -> Any:
    """Tapa cada texto dentro de diccionarios, listas y tuplas, como la entrada o la salida de un span.
    Las claves, los números y lo demás quedan igual."""
    if isinstance(valor, str):
        return tapar(valor)
    if isinstance(valor, dict):
        return {clave: tapar_valor(v) for clave, v in valor.items()}
    if isinstance(valor, list):
        return [tapar_valor(v) for v in valor]
    if isinstance(valor, tuple):
        return tuple(tapar_valor(v) for v in valor)
    return valor
