"""Panel de la administración: todos los consorcios que administra una persona, de un vistazo.

Cada número sale de una `Identidad` por consorcio, resuelta con `resolver_identidad`: nada se suma de un consorcio
que la persona no administra. Lo que ya tiene su función con alcance (`listar_solicitudes`, `revision.listar`) se
reusa; lo demás filtra por `consorcio_id` de esa identidad.

El cobro sale de la última liquidación cargada: `saldo_anterior` es lo que la unidad arrastra del período anterior
según el PDF, no un registro de pagos. Por eso se muestra con el período y como "según la liquidación".

Sin montos en dólares: el costo del modelo es de la plataforma y no hay precio por cliente; queda en el CRM. Acá van
las métricas de uso que le sirven a la administración: vecinos que usaron el asistente en la semana (de `turnos`, no
de `ultima_actividad`, que es global), cuánto hace que espera lo pendiente más viejo y la mediana de lo que tardó en
resolver lo que resolvió en la semana.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from ..almacen.postgres import (
    Documento,
    Liquidacion,
    LiquidacionUnidad,
    Mensaje,
    Solicitud,
    Turno,
    crear_tablas,
    engine,
)
from ..identidad import Identidad, membresias_de, resolver_identidad
from . import revision
from .bandeja import listar_solicitudes

DIAS = 7
MAX_LISTA = 6
ROLES_VECINO = ("consejo", "propietario", "inquilino")


def consorcios_administrados(sujeto_externo: str) -> list[tuple[Identidad, str]]:
    """Identidad y nombre de cada consorcio donde la persona es administración."""
    return [(resolver_identidad(sujeto_externo, m["consorcio_id"]), m["nombre"])
            for m in membresias_de(sujeto_externo) if m["rol"] == "administracion"]


def panel(sujeto_externo: str, ahora: datetime | None = None) -> dict:
    ahora = ahora or datetime.now(UTC)
    consorcios = [_consorcio(identidad, nombre, ahora) for identidad, nombre in consorcios_administrados(sujeto_externo)]
    pendientes = sorted((p for c in consorcios for p in c["pendientes"]), key=lambda p: p["creado_en"])
    esperas = [c["espera_mas_vieja_s"] for c in consorcios if c["espera_mas_vieja_s"] is not None]
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
            "vecinos": sum(c["semana"]["vecinos"] for c in consorcios),  # una persona en dos consorcios cuenta dos veces
            "espera_mas_vieja_s": max(esperas) if esperas else None,
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
        resolucion = _mediana_resolucion(s, identidad.consorcio_id, ahora - timedelta(days=DIAS))
        documentos = s.scalar(select(func.count()).select_from(Documento).where(
            Documento.consorcio_id == identidad.consorcio_id, Documento.activo)) or 0
    primera = min((p["creado_en"] for p in pendientes), default=None)
    return {
        "consorcio_id": identidad.consorcio_id, "nombre": nombre, "pendientes": pendientes,
        "espera_mas_vieja_s": None if primera is None else max((ahora - primera).total_seconds(), 0.0),
        "resolucion_mediana_s": resolucion,
        "total_pendientes": len(pendientes), "sin_revisar": len(revision.listar(identidad, "pendientes")),
        "semana": semana, "consultas": consultas, "cobro": cobro, "documentos": documentos,
    }


def _semana(s: Session, consorcio_id: str, desde: datetime) -> dict:
    fila = s.execute(
        select(func.count(),
               func.count().filter(Turno.resultado == "derivada"),
               func.count().filter(Turno.resultado == "reclamo_pendiente"),
               func.count().filter(Turno.feedback == -1),
               func.count(func.distinct(Turno.usuario_id)).filter(Turno.rol.in_(ROLES_VECINO)))
        .where(Turno.consorcio_id == consorcio_id, Turno.origen == "usuario", Turno.creado_en >= desde)
    ).one()
    return dict(zip(("consultas", "derivadas", "reclamos", "negativas", "vecinos"), fila, strict=True))


def _mediana_resolucion(s: Session, consorcio_id: str, desde: datetime) -> float | None:
    """Mediana de `resuelta_en - creado_en` de lo que se resolvió (derivaciones y reclamos) desde `desde`. Es espera
    del vecino, no esfuerzo de la administración. None si no se resolvió nada."""
    espera = cast(func.extract("epoch", Solicitud.resuelta_en - Solicitud.creado_en), Float)
    valor = s.scalar(select(func.percentile_cont(0.5).within_group(espera)).where(
        Solicitud.consorcio_id == consorcio_id, Solicitud.resuelta_en.is_not(None), Solicitud.resuelta_en >= desde))
    return None if valor is None else round(float(valor), 1)


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
