"""Invitaciones desde la web: la administración arma el link para que alguien se sume a su consorcio.

Reusa `auth.cuentas`, lo mismo que la API: el token se genera acá, se muestra una sola vez y a la base va solo su
hash, así que el link no se puede volver a ver; si se pierde, se genera otro (y el anterior queda anulado). No se
manda por mail: la administración lo copia y lo comparte por donde quiera. Quien lo abre cae en `/registro`.
"""
from __future__ import annotations

import re
from datetime import datetime

from ..auth.cuentas import (
    PedidoInvalido,
    anular_invitacion,
    anular_pendientes,
    crear_invitacion,
    listar_invitaciones,
    unidad_para,
)
from ..auth.db import conectar
from ..identidad import Identidad

ROLES_INVITABLES = {"propietario": "Propietario", "inquilino": "Inquilino", "consejo": "Consejo",
                    "administracion": "Administración"}
CON_UNIDAD = {"propietario", "inquilino"}
MAX_EMAIL = 320
_FORMA_MAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")


class InvitacionRechazada(ValueError):
    """Datos del formulario que no alcanzan para invitar; el mensaje es para mostrarle a la administración."""


def invitar(identidad: Identidad, email: str, rol: str, piso: str, depto: str) -> tuple[str, datetime]:
    """Devuelve el token y su vencimiento. Solo al consorcio de la identidad, que la ruta ya exigió que administre."""
    email = email.strip().lower()
    if len(email) > MAX_EMAIL or not _FORMA_MAIL.fullmatch(email):
        raise InvitacionRechazada("Escribí un mail válido.")
    if rol not in ROLES_INVITABLES:
        raise InvitacionRechazada("Elegí qué va a ser en el consorcio.")
    with conectar() as con:
        try:
            unidad_id = unidad_para(con, identidad.consorcio_id, piso, depto) if rol in CON_UNIDAD else None
            anular_pendientes(con, identidad.consorcio_id, email)
            return crear_invitacion(con, identidad.consorcio_id, email, rol, unidad_id, identidad.usuario_id)
        except PedidoInvalido as e:
            raise InvitacionRechazada(f"{e}.") from e


def invitaciones(identidad: Identidad) -> list[dict]:
    with conectar() as con:
        return listar_invitaciones(con, identidad.consorcio_id)


def anular(identidad: Identidad, invitacion_id: int) -> bool:
    with conectar() as con:
        return anular_invitacion(con, identidad.consorcio_id, invitacion_id)
