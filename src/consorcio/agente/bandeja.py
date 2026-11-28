"""Bandeja de aprobación: derivaciones y reclamos que esperan a un humano.

Solo un administrador del consorcio la opera. Una solicitud de otro consorcio se trata igual que una
que no existe, para no revelar nada. Cada transición es un UPDATE condicional sobre `estado`: de dos
aprobaciones en paralelo gana una. Un administrador puede aprobar su propio reclamo: si no, un
consorcio con un solo administrador queda trabado.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..almacen.postgres import Mensaje, Solicitud, Turno, Usuario, crear_tablas, engine
from ..identidad import Identidad

# Elevar una respuesta del chat o escribirle directo a la administración (objetivo C del plan de perfiles)
RESULTADOS_ELEVABLES = frozenset({"respondida", "sin_datos", "fuera_de_alcance", "tope_excedido", "error"})
MOTIVOS_DEL_VECINO = ("elevada_usuario", "pedido_directo")
CUPO_DIARIO = 10  # elevaciones + mensajes directos por usuario en 24 horas
MAX_NOTA = 500
LARGO_ASUNTO = (3, 120)
LARGO_MENSAJE = (10, 2000)


class SinPermiso(Exception):
    """La operación es solo para administradores."""


class SolicitudInexistente(Exception):
    """No existe, es de otro consorcio o no es del tipo que se pidió."""


class SolicitudYaResuelta(Exception):
    """Ya no está pendiente."""


class TurnoAjeno(Exception):
    """El turno no es de este usuario en este consorcio."""


class TurnoNoElevable(Exception):
    """Un turno sin pregunta válida (mensaje o conversación inválidos) no se pasa a la administración."""


class PedidoInvalido(ValueError):
    """Asunto, mensaje o nota fuera de los largos permitidos."""


class CupoExcedido(Exception):
    """El usuario ya mandó `CUPO_DIARIO` elevaciones y mensajes directos en las últimas 24 horas."""


def _exigir_administrador(identidad: Identidad) -> None:
    if not identidad.es_administracion:
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


def _exigir_cupo(s: Session, identidad: Identidad) -> None:
    """Anti-spam: cuenta en todos los consorcios, porque el que escribe es la persona."""
    usadas = s.scalar(select(func.count()).select_from(Solicitud).where(
        Solicitud.autor_id == identidad.usuario_id, Solicitud.motivo.in_(MOTIVOS_DEL_VECINO),
        Solicitud.creado_en >= datetime.now(UTC) - timedelta(days=1),
    ))
    if usadas >= CUPO_DIARIO:
        raise CupoExcedido(f"ya hay {usadas} mensajes a la administración en las últimas 24 horas")


def elevar_turno(identidad: Identidad, turno_id: int, nota: str = "") -> dict:
    """El vecino pasa una respuesta del chat a la administración: una derivación con su pregunta y la respuesta.

    Idempotente: el turno se ata a la solicitud con un UPDATE condicional en la misma transacción; si otro
    pedido ya lo ató (doble clic, dos pestañas), se deshace la nueva y se devuelve la existente. Un turno que
    ya tenía solicitud (derivado o reclamo) devuelve esa. No cambia `turnos.resultado`.
    Devuelve `{"solicitud_id", "nueva"}`."""
    nota = (nota or "").strip()
    if len(nota) > MAX_NOTA:
        raise PedidoInvalido(f"la nota tiene más de {MAX_NOTA} caracteres")
    crear_tablas()
    with Session(engine()) as s:
        turno = s.scalars(select(Turno).where(Turno.id == turno_id, Turno.usuario_id == identidad.usuario_id,
                                              Turno.consorcio_id == identidad.consorcio_id)).first()
        if turno is None:
            raise TurnoAjeno(f"el turno {turno_id} no es de este usuario")
        if turno.solicitud_id is not None:
            return {"solicitud_id": turno.solicitud_id, "nueva": False}
        if turno.resultado not in RESULTADOS_ELEVABLES:
            raise TurnoNoElevable(f"un turno {turno.resultado} no se eleva")
        _exigir_cupo(s, identidad)
        pregunta = s.get(Mensaje, turno.mensaje_usuario_id) if turno.mensaje_usuario_id else None
        respuesta = s.get(Mensaje, turno.mensaje_asistente_id) if turno.mensaje_asistente_id else None
        texto = pregunta.texto if pregunta else ""
        # Un turno de error del grafo no guarda la pregunta: queda lo que el vecino agregue, o el aviso
        mensaje = "\n\n".join(p for p in (texto, nota and f"Agregó: {nota}") if p) or "(la pregunta no quedó guardada)"
        solicitud = Solicitud(
            tipo="derivacion", motivo="elevada_usuario", consorcio_id=identidad.consorcio_id,
            autor_id=identidad.usuario_id, mensaje=mensaje, titulo=(texto or nota or "Consulta desde el chat")[:120],
            detalle=respuesta.texto if respuesta else "(sin respuesta del asistente)",
        )
        s.add(solicitud)
        s.flush()
        atado = s.execute(update(Turno).where(Turno.id == turno_id, Turno.solicitud_id.is_(None))
                          .values(solicitud_id=solicitud.id))
        if atado.rowcount == 0:
            s.rollback()
            return {"solicitud_id": s.scalar(select(Turno.solicitud_id).where(Turno.id == turno_id)), "nueva": False}
        s.commit()
        return {"solicitud_id": solicitud.id, "nueva": True}


def pedido_directo(identidad: Identidad, asunto: str, mensaje: str) -> int:
    """El vecino le escribe a la administración sin pasar por el modelo: sirve con el tope excedido o el agente
    caído. No crea turno."""
    asunto, mensaje = (asunto or "").strip(), (mensaje or "").strip()
    if not LARGO_ASUNTO[0] <= len(asunto) <= LARGO_ASUNTO[1] or not LARGO_MENSAJE[0] <= len(mensaje) <= LARGO_MENSAJE[1]:
        raise PedidoInvalido("asunto o mensaje fuera de largo")
    crear_tablas()
    with Session(engine()) as s:
        _exigir_cupo(s, identidad)
        solicitud = Solicitud(tipo="derivacion", motivo="pedido_directo", consorcio_id=identidad.consorcio_id,
                              autor_id=identidad.usuario_id, mensaje=mensaje, titulo=asunto, detalle=mensaje)
        s.add(solicitud)
        s.commit()
        return solicitud.id


def pendientes_por_consorcio(consorcio_ids: list[str]) -> dict[str, int]:
    """Una sola consulta para el selector de la barra. Quien llama pasa solo los consorcios que la persona
    administra (salen de sus membresías, nunca del cliente)."""
    if not consorcio_ids:
        return {}
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(
            select(Solicitud.consorcio_id, func.count())
            .where(Solicitud.consorcio_id.in_(consorcio_ids), Solicitud.estado == "pendiente")
            .group_by(Solicitud.consorcio_id)
        ).all()
    return {cid: n for cid, n in filas}


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
