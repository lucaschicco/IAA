"""Conversaciones y mensajes: solo para el router y la pantalla del chat.

Una conversación es de un usuario en un consorcio: la misma persona en otro consorcio no la ve."""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..almacen.postgres import Conversacion, Mensaje, crear_tablas, engine
from ..identidad import Identidad

_ID_CONVERSACION = re.compile(r"[0-9a-f]{32}")


def _propia(identidad: Identidad, conversacion_id):
    return select(Conversacion).where(
        Conversacion.id == conversacion_id, Conversacion.usuario_id == identidad.usuario_id,
        Conversacion.consorcio_id == identidad.consorcio_id,
    )


def conversacion_valida(identidad: Identidad, conversacion_id: str) -> bool:
    """El id llega de afuera: lo que no tiene forma de id no llega a la base."""
    if not _ID_CONVERSACION.fullmatch(conversacion_id or ""):
        return False
    crear_tablas()
    with Session(engine()) as s:
        return s.scalars(_propia(identidad, conversacion_id)).first() is not None


def conversaciones_de(identidad: Identidad, limite: int = 20) -> list[dict]:
    crear_tablas()
    with Session(engine()) as s:
        filas = s.scalars(
            select(Conversacion).where(
                Conversacion.usuario_id == identidad.usuario_id, Conversacion.consorcio_id == identidad.consorcio_id,
            ).order_by(Conversacion.actualizada_en.desc()).limit(limite)
        ).all()
        return [{"id": c.id, "actualizada_en": c.actualizada_en} for c in filas]


def historial(identidad: Identidad, conversacion_id: str) -> list[dict]:
    """Toda la conversación, de la más vieja a la más nueva; [] si es ajena."""
    if not conversacion_valida(identidad, conversacion_id):
        return []
    with Session(engine()) as s:
        filas = s.scalars(
            select(Mensaje).where(Mensaje.conversacion_id == conversacion_id).order_by(Mensaje.id)
        ).all()
        return [{"rol": m.rol, "texto": m.texto, "creado_en": m.creado_en} for m in filas]


def ultimos_mensajes(conversacion_id: str, cantidad: int) -> list[dict]:
    """Los últimos de una conversación ya validada, en orden: lo que ve el router."""
    with Session(engine()) as s:
        filas = s.scalars(
            select(Mensaje).where(Mensaje.conversacion_id == conversacion_id).order_by(Mensaje.id.desc()).limit(cantidad)
        ).all()
        return [{"rol": m.rol, "texto": m.texto} for m in reversed(filas)]
