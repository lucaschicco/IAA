"""Panel de la administración: todos los consorcios que administra una persona, de un vistazo.

Cada número sale de una `Identidad` por consorcio, resuelta con `resolver_identidad`: nada se suma de un consorcio
que la persona no administra. Lo que ya tiene su función con alcance (`listar_solicitudes`, `revision.listar`) se
reusa; lo demás filtra por `consorcio_id` de esa identidad.

El cobro sale de la última liquidación cargada: `saldo_anterior` es lo que la unidad arrastra del período anterior
según el PDF, no un registro de pagos. Por eso se muestra con el período y como "según la liquidación".
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..almacen.postgres import (
    Documento,
    Liquidacion,
    LiquidacionUnidad,
    Mensaje,
    Turno,
    crear_tablas,
    engine,
)
from ..identidad import Identidad, membresias_de, resolver_identidad
from . import revision
from .bandeja import listar_solicitudes

DIAS = 7
MAX_LISTA = 6


def consorcios_administrados(sujeto_externo: str) -> list[tuple[Identidad, str]]:
    """Identidad y nombre de cada consorcio donde la persona es administración."""
    return [(resolver_identidad(sujeto_externo, m["consorcio_id"]), m["nombre"])
            for m in membresias_de(sujeto_externo) if m["rol"] == "administracion"]


def panel(sujeto_externo: str, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    consorcios = [_consorcio(identidad, nombre, ahora) for identidad, nombre in consorcios_administrados(sujeto_externo)]
    pendientes = sorted((p for c in consorcios for p in c["pendientes"]), key=lambda p: p["creado_en"])
    consultas = sorted((q for c in consorcios for q in c["consultas"]), key=lambda q: q["creado_en"], reverse=True)
    return {
        "consorcios": consorcios,
        "pendientes": pendientes[:MAX_LISTA * 2],  # lo más viejo primero: es lo que más espera
        "consultas": consultas[:MAX_LISTA],
        "totales": {
            "pendientes": sum(c["total_pendientes"] for c in consorcios),
            "consultas": sum(c["semana"]["consultas"] for c in consorcios),
            "sin_revisar": sum(c["sin_revisar"] for c in consorcios),
            "saldo": sum(c["cobro"]["saldo"] for c in consorcios if c["cobro"]),
            "unidades_con_saldo": sum(c["cobro"]["con_saldo"] for c in consorcios if c["cobro"]),
        },
        "dias": DIAS,
    }


def _consorcio(identidad: Identidad, nombre: str, ahora: datetime) -> dict:
    crear_tablas()
    pendientes = [{**s, "consorcio_id": identidad.consorcio_id, "consorcio": nombre}
                  for s in listar_solicitudes(identidad, estado="pendiente")]
    with Session(engine()) as s:
        semana = _semana(s, identidad.consorcio_id, ahora - timedelta(days=DIAS))
        consultas = [{**q, "consorcio_id": identidad.consorcio_id, "consorcio": nombre}
                     for q in _consultas(s, identidad.consorcio_id)]
        cobro = _cobro(s, identidad.consorcio_id)
        documentos = s.scalar(select(func.count()).select_from(Documento).where(
            Documento.consorcio_id == identidad.consorcio_id, Documento.activo)) or 0
    return {
        "consorcio_id": identidad.consorcio_id, "nombre": nombre, "pendientes": pendientes,
        "total_pendientes": len(pendientes), "sin_revisar": len(revision.listar(identidad, "pendientes")),
        "semana": semana, "consultas": consultas, "cobro": cobro, "documentos": documentos,
    }


def _semana(s: Session, consorcio_id: str, desde: datetime) -> dict:
    fila = s.execute(
        select(func.count(),
               func.count().filter(Turno.resultado == "derivada"),
               func.count().filter(Turno.resultado == "reclamo_pendiente"),
               func.count().filter(Turno.feedback == -1))
        .where(Turno.consorcio_id == consorcio_id, Turno.origen == "usuario", Turno.creado_en >= desde)
    ).one()
    return dict(zip(("consultas", "derivadas", "reclamos", "negativas"), fila, strict=True))


def _consultas(s: Session, consorcio_id: str) -> list[dict]:
    """Las últimas preguntas, sin quién las hizo: como en Revisar respuestas."""
    filas = s.execute(
        select(Turno.creado_en, Turno.resultado, Turno.feedback, Mensaje.texto)
        .join(Mensaje, Mensaje.id == Turno.mensaje_usuario_id)
        .where(Turno.consorcio_id == consorcio_id, Turno.origen == "usuario")
        .order_by(Turno.creado_en.desc()).limit(MAX_LISTA)
    ).all()
    return [{"creado_en": c, "resultado": r, "feedback": f, "pregunta": t} for c, r, f, t in filas]


def _cobro(s: Session, consorcio_id: str) -> dict | None:
    ultima = s.execute(
        select(Liquidacion.documento_id, Liquidacion.periodo)
        .join(Documento, Documento.id == Liquidacion.documento_id)
        .where(Documento.consorcio_id == consorcio_id, Documento.activo)
        .order_by(Liquidacion.periodo.desc()).limit(1)
    ).first()
    if ultima is None:
        return None
    unidades = s.execute(
        select(LiquidacionUnidad.piso_depto, LiquidacionUnidad.saldo_anterior, LiquidacionUnidad.a_pagar)
        .where(LiquidacionUnidad.documento_id == ultima.documento_id)
    ).all()
    con_saldo = sorted(((u.piso_depto, u.saldo_anterior) for u in unidades if (u.saldo_anterior or 0) > 0),
                       key=lambda u: u[1], reverse=True)
    return {
        "periodo": ultima.periodo, "unidades": len(unidades), "con_saldo": len(con_saldo),
        "saldo": sum(v for _, v in con_saldo), "a_pagar": sum(u.a_pagar or 0 for u in unidades),
        "deudores": [{"unidad": u, "saldo": v} for u, v in con_saldo[:MAX_LISTA]],
    }
