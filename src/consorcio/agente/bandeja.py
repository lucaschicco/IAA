"""Bandeja de aprobación: derivaciones y reclamos que esperan a un humano.

Solo un administrador del consorcio la opera. Una solicitud de otro consorcio se trata igual que una
que no existe, para no revelar nada. Cada transición es un UPDATE condicional sobre `estado`: de dos
aprobaciones en paralelo gana una. Un administrador puede aprobar su propio reclamo: si no, un
consorcio con un solo administrador queda trabado.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..almacen.postgres import Solicitud, Turno, Usuario, crear_tablas, engine
from ..identidad import Identidad


class SinPermiso(Exception):
    """La operación es solo para administradores."""


class SolicitudInexistente(Exception):
    """No existe, es de otro consorcio o no es del tipo que se pidió."""


class SolicitudYaResuelta(Exception):
    """Ya no está pendiente."""


class TurnoAjeno(Exception):
    """El turno no es de este usuario en este consorcio."""


def _exigir_administrador(identidad: Identidad) -> None:
    if identidad.rol != "administrador":
        raise SinPermiso("solo un administrador del consorcio puede hacer esto")


def _fila(solicitud: Solicitud, autor_nombre: str | None) -> dict:
    campos = ("id", "tipo", "estado", "motivo", "mensaje", "titulo", "detalle", "unidad", "titulo_final",
              "detalle_final", "respuesta", "autor_id", "resuelta_por", "creado_en", "resuelta_en")
    return {**{c: getattr(solicitud, c) for c in campos}, "autor_nombre": autor_nombre}


def _con_autor():
    return select(Solicitud, Usuario.nombre).join(Usuario, Usuario.id == Solicitud.autor_id)


def crear_solicitud(
    identidad: Identidad, tipo: str, motivo: str, mensaje: str, titulo: str, detalle: str, unidad: str | None = None
) -> int:
    """Solo inserta: ninguna acción se ejecuta antes de la aprobación."""
    crear_tablas()
    with Session(engine()) as s:
        solicitud = Solicitud(tipo=tipo, motivo=motivo, consorcio_id=identidad.consorcio_id,
                              autor_id=identidad.usuario_id, mensaje=mensaje, titulo=titulo, detalle=detalle,
                              unidad=unidad)
        s.add(solicitud)
        s.commit()
        return solicitud.id


def listar_solicitudes(
    identidad: Identidad, estado: str | None = None, tipo: str | None = None, limite: int = 100
) -> list[dict]:
    _exigir_administrador(identidad)
    crear_tablas()
    q = _con_autor().where(Solicitud.consorcio_id == identidad.consorcio_id)
    if estado:
        q = q.where(Solicitud.estado == estado)
    if tipo:
        q = q.where(Solicitud.tipo == tipo)
    with Session(engine()) as s:
        return [_fila(f, nombre) for f, nombre in s.execute(q.order_by(Solicitud.id.desc()).limit(limite)).all()]


def mis_solicitudes(identidad: Identidad, limite: int = 50) -> list[dict]:
    crear_tablas()
    q = _con_autor().where(Solicitud.consorcio_id == identidad.consorcio_id, Solicitud.autor_id == identidad.usuario_id)
    with Session(engine()) as s:
        return [_fila(f, nombre) for f, nombre in s.execute(q.order_by(Solicitud.id.desc()).limit(limite)).all()]


def _transicion(identidad: Identidad, solicitud_id: int, tipo: str, **valores) -> dict:
    _exigir_administrador(identidad)
    crear_tablas()
    filtro = (Solicitud.id == solicitud_id, Solicitud.consorcio_id == identidad.consorcio_id, Solicitud.tipo == tipo)
    with Session(engine()) as s:
        cambio = s.execute(
            update(Solicitud).where(*filtro, Solicitud.estado == "pendiente")
            .values(resuelta_por=identidad.usuario_id, resuelta_en=datetime.now(UTC), **valores)
        )
        if cambio.rowcount == 0:
            if s.scalars(select(Solicitud.id).where(*filtro)).first() is None:
                raise SolicitudInexistente(f"no hay una solicitud {solicitud_id} en este consorcio")
            raise SolicitudYaResuelta(f"la solicitud {solicitud_id} ya no está pendiente")
        s.commit()
        fila, nombre = s.execute(_con_autor().where(Solicitud.id == solicitud_id)).one()
        return _fila(fila, nombre)


def resolver_derivacion(identidad: Identidad, solicitud_id: int, respuesta: str) -> dict:
    return _transicion(identidad, solicitud_id, "derivacion", estado="resuelta", respuesta=respuesta)


def aprobar_reclamo(identidad: Identidad, solicitud_id: int, respuesta: str | None = None) -> dict:
    return _transicion(identidad, solicitud_id, "reclamo", estado="aprobada", respuesta=respuesta)


def editar_reclamo(
    identidad: Identidad, solicitud_id: int, titulo: str, detalle: str, respuesta: str | None = None
) -> dict:
    """Aprueba con cambios: lo propuesto por el modelo queda en `titulo` y `detalle`."""
    return _transicion(identidad, solicitud_id, "reclamo", estado="editada", titulo_final=titulo, detalle_final=detalle,
                       respuesta=respuesta)


def rechazar_reclamo(identidad: Identidad, solicitud_id: int, respuesta: str) -> dict:
    return _transicion(identidad, solicitud_id, "reclamo", estado="rechazada", respuesta=respuesta)


def registrar_feedback(identidad: Identidad, turno_id: int, valor: int) -> None:
    """Pulgar arriba (1) o abajo (-1) sobre un turno propio; volver a votar pisa el voto anterior."""
    if valor not in (-1, 1):
        raise ValueError("el feedback es 1 o -1")
    crear_tablas()
    with Session(engine()) as s:
        cambio = s.execute(
            update(Turno).where(Turno.id == turno_id, Turno.usuario_id == identidad.usuario_id,
                                Turno.consorcio_id == identidad.consorcio_id).values(feedback=valor)
        )
        if cambio.rowcount == 0:
            raise TurnoAjeno(f"el turno {turno_id} no es de este usuario")
        s.commit()
