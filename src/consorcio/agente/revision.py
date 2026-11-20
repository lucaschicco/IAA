"""Revisión de respuestas: la administración dice si el agente respondió bien a los vecinos.

Solo un administrador del consorcio revisa, y solo respuestas de su consorcio: una de otro se trata
igual que una que no existe. La pantalla no muestra quién preguntó: para juzgar la respuesta no hace
falta. Lo primero en la fila es lo que más probablemente esté mal: un pulgar abajo del vecino, certeza
baja, una derivación. Cada revisión se cuelga también de la traza del turno en MLflow (sin el texto).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import case, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from ..almacen.postgres import (
    MOTIVOS_REVISION,
    VEREDICTOS,
    Mensaje,
    Revision,
    Turno,
    crear_tablas,
    engine,
)
from ..identidad import Identidad
from ..observabilidad import registrar_revision
from . import certeza
from .bandeja import SinPermiso

# Lo que el agente contestó por su cuenta. Reclamos y errores no: el reclamo lo decide la bandeja
REVISABLES = ("respondida", "sin_datos", "derivada", "fuera_de_alcance")
MAX_RESPUESTA_CORRECTA = 4000
PRIORIDADES = ("feedback_negativo", "certeza_baja", "derivada", "otra")


class RespuestaInexistente(Exception):
    """No existe, es de otro consorcio o no es una respuesta que se pueda revisar."""


class RevisionInvalida(ValueError):
    """Veredicto o motivo fuera del vocabulario, o falta el motivo de una respuesta con errores."""


def _exigir_administrador(identidad: Identidad) -> None:
    if not identidad.es_administracion:
        raise SinPermiso("solo un administrador del consorcio puede revisar respuestas")


def _revisables(identidad: Identidad):
    return (Turno.consorcio_id == identidad.consorcio_id, Turno.origen == "usuario", Turno.resultado.in_(REVISABLES),
            Turno.mensaje_asistente_id.is_not(None))


def _prioridad():
    return case(
        (Turno.feedback == -1, 0),
        (Turno.certeza < certeza.UMBRAL_MEDIA, 1),
        (Turno.resultado == "derivada", 2),
        else_=3,
    )


def listar(identidad: Identidad, ver: str = "pendientes", limite: int = 50) -> list[dict]:
    """`ver`: `pendientes` (sin revisar, lo más dudoso primero) o `revisadas` (lo último revisado primero)."""
    _exigir_administrador(identidad)
    crear_tablas()
    pregunta, respuesta = aliased(Mensaje), aliased(Mensaje)
    q = (
        select(Turno, pregunta.texto, respuesta.texto, Revision, _prioridad())
        .outerjoin(pregunta, pregunta.id == Turno.mensaje_usuario_id)
        .join(respuesta, respuesta.id == Turno.mensaje_asistente_id)
        .outerjoin(Revision, Revision.turno_id == Turno.id)
        .where(*_revisables(identidad))
    )
    if ver == "revisadas":
        q = q.where(Revision.id.is_not(None)).order_by(Revision.actualizado_en.desc(), Turno.id.desc())
    else:
        q = q.where(Revision.id.is_(None)).order_by(_prioridad(), Turno.id.desc())
    with Session(engine()) as s:
        return [_fila(*f) for f in s.execute(q.limit(limite)).all()]


def _fila(turno: Turno, pregunta: str | None, respuesta: str, revision: Revision | None, prioridad: int) -> dict:
    return {
        "turno_id": turno.id, "creado_en": turno.creado_en, "rol": turno.rol, "intencion": turno.intencion,
        "funcionalidad": turno.funcionalidad, "resultado": turno.resultado, "certeza": turno.certeza,
        "nivel": certeza.nivel(turno.certeza), "feedback": turno.feedback, "pregunta": pregunta, "respuesta": respuesta,
        "prioridad": PRIORIDADES[prioridad],
        "revision": None if revision is None else {
            "veredicto": revision.veredicto, "motivo": revision.motivo,
            "respuesta_correcta": revision.respuesta_correcta, "actualizado_en": revision.actualizado_en,
        },
    }


def _validar(veredicto: str, motivo: str | None) -> str | None:
    if veredicto not in VEREDICTOS:
        raise RevisionInvalida(f"veredicto desconocido: {veredicto!r}")
    if veredicto == "correcta":
        return None  # una respuesta correcta no tiene motivo de error
    if motivo not in MOTIVOS_REVISION:
        raise RevisionInvalida("una respuesta parcial o incorrecta necesita el motivo")
    return motivo


def revisar(identidad: Identidad, turno_id: int, veredicto: str, motivo: str | None = None,
            respuesta_correcta: str | None = None) -> dict:
    """Guarda o pisa la revisión del turno. Quien revisa de nuevo queda como revisor."""
    _exigir_administrador(identidad)
    motivo = _validar(veredicto, motivo)
    respuesta_correcta = (respuesta_correcta or "").replace("\x00", "").strip()[:MAX_RESPUESTA_CORRECTA] or None
    crear_tablas()
    ahora = datetime.now(UTC)
    with Session(engine()) as s:
        turno = s.scalars(select(Turno).where(Turno.id == turno_id, *_revisables(identidad))).first()
        if turno is None:
            raise RespuestaInexistente(f"no hay una respuesta {turno_id} para revisar en este consorcio")
        valores = {"revisor_id": identidad.usuario_id, "veredicto": veredicto, "motivo": motivo,
                   "respuesta_correcta": respuesta_correcta, "actualizado_en": ahora}
        s.execute(
            insert(Revision).values(turno_id=turno_id, consorcio_id=identidad.consorcio_id, creado_en=ahora, **valores)
            .on_conflict_do_update(index_elements=["turno_id"], set_=valores)
        )
        s.commit()
        traza_id = turno.mlflow_traza_id
    en_mlflow = registrar_revision(traza_id, veredicto, motivo)
    return {"turno_id": turno_id, "veredicto": veredicto, "motivo": motivo, "respuesta_correcta": respuesta_correcta,
            "en_mlflow": en_mlflow}
