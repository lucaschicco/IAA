"""Métricas del CRM: SQL puro sobre tablas sin texto libre, para el equipo de plataforma.

El CRM cruza consorcios a propósito: es la excepción autorizada a "nada cruza entre consorcios", solo
con agregados y solo detrás de `exigir_plataforma`. Por eso acá no se lee el texto de nadie: ni
`mensajes`, ni `mensaje`/`titulo`/`detalle` de `solicitudes`, ni nombres de usuarios o consorcios.

Las evaluaciones no cuentan: `turnos` se filtra por `origen`, y `solicitudes`, que no tiene esa columna,
por el turno que la creó. `gastos_modelo` tampoco la tiene: el costo de los turnos de evaluación se resta
junto con el resto, y lo que evalúa sin pasar por `atender` queda en "ingesta y otros", porque se pagó.

Qué depende del período: `usuarios` son ventanas móviles desde ahora y `documentos` y
`secciones_indexadas` son el estado de hoy (salvo `duplicados_evitados`); el resto es del período.
Con filtro de consorcio solo `costos.tope` sigue global: el tope es uno solo, de toda la plataforma.
`actividad` va por día UTC y `periodo_anterior` es la ventana del mismo largo justo antes de `desde`.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session

from .agente.registro import FUNCIONALIDADES
from .almacen.postgres import (
    Carga,
    Consorcio,
    Consulta,
    Documento,
    GastoModelo,
    Membresia,
    Seccion,
    Solicitud,
    Turno,
    Usuario,
    crear_tablas,
    engine,
)
from .consulta.evaluar import DIR_EVALUACIONES
from .tope import estado_tope

log = logging.getLogger("consorcio.crm")

_ARCHIVO_EVALUACION = re.compile(r"(\d{4}-\d{2}-\d{2})-consultas-test\.json")

ORIGEN_EVALUACION = "evaluacion"
FUNCIONALIDAD_CON_CITA = "reglamento"  # la única que manda secciones al modelo
SIN_CLASIFICAR = "sin_clasificar"  # el router falló o el turno no llegó al router
SIN_FUNCIONALIDAD = "sin_funcionalidad"  # solo el router: fuera de alcance o router inválido
ESTADOS_DUPLICADO = ("duplicado", "periodo_duplicado")  # cortes sin OCR ni modelo
RESULTADO_DERIVADA = "derivada"
TOP_USUARIOS = 20
MINUTOS_CONECTADO = 5


@dataclass(frozen=True)
class _Filtro:
    desde: datetime
    hasta: datetime
    consorcio_id: str | None

    def periodo(self, q, tabla):
        """Del consorcio elegido y con `desde <= creado_en < hasta`."""
        q = q.where(tabla.creado_en >= self.desde, tabla.creado_en < self.hasta)
        return self.consorcio(q, tabla)

    def consorcio(self, q, tabla):
        return q.where(tabla.consorcio_id == self.consorcio_id) if self.consorcio_id else q

    def turnos(self, q):
        return self.periodo(q, Turno).where(Turno.origen != ORIGEN_EVALUACION)

    def solicitudes(self, q, tipo: str):
        """Sin `origen` propio: una solicitud es de evaluación si la creó un turno de evaluación."""
        de_evaluacion = select(Turno.solicitud_id).where(
            Turno.origen == ORIGEN_EVALUACION, Turno.solicitud_id.is_not(None)
        )
        return self.periodo(q, Solicitud).where(Solicitud.tipo == tipo, Solicitud.id.not_in(de_evaluacion))


def _cociente(parte: int, total: int) -> float | None:
    """None y no 0: sin casos no hay medición, que es distinto de medir cero."""
    return round(parte / total, 4) if total else None


def _conteo_por(s: Session, q) -> dict:
    return {clave: n for clave, n in s.execute(q).all()}


def _usuarios(s: Session, f: _Filtro) -> dict:
    """`ultima_actividad` es por usuario, no por consorcio: con filtro cuenta a los miembros de ese
    consorcio que estuvieron activos, aunque haya sido en otro de sus consorcios."""
    ahora = datetime.now(UTC)
    q = select(func.count()).select_from(Usuario)
    if f.consorcio_id:
        q = q.where(Usuario.id.in_(select(Membresia.usuario_id).where(Membresia.consorcio_id == f.consorcio_id)))

    def activos_desde(ventana: timedelta) -> int:
        return s.scalar(q.where(Usuario.ultima_actividad >= ahora - ventana))

    return {
        "conectados": activos_desde(timedelta(minutes=MINUTOS_CONECTADO)),
        "activos_dia": activos_desde(timedelta(days=1)),
        "activos_semana": activos_desde(timedelta(days=7)),
        "activos_mes": activos_desde(timedelta(days=30)),
    }


def _uso(s: Session, f: _Filtro) -> dict:
    cantidad = func.count().label("turnos")
    top = f.turnos(select(Turno.usuario_id, cantidad).group_by(Turno.usuario_id))
    filas = s.execute(top.order_by(cantidad.desc(), Turno.usuario_id).limit(TOP_USUARIOS)).all()
    return {
        "por_usuario": [{"usuario_id": usuario_id, "turnos": turnos} for usuario_id, turnos in filas],
        "por_consorcio": _conteo_por(s, f.turnos(select(Turno.consorcio_id, cantidad).group_by(Turno.consorcio_id))),
        "por_rol": _conteo_por(s, f.turnos(select(Turno.rol, cantidad).group_by(Turno.rol))),
    }


def _documentos(s: Session, f: _Filtro) -> dict:
    activos = f.consorcio(select(Documento.activo, func.count()).group_by(Documento.activo), Documento)
    por_activo = _conteo_por(s, activos)
    duplicados = f.periodo(select(func.count()).select_from(Carga), Carga).where(Carga.estado.in_(ESTADOS_DUPLICADO))
    return {
        "por_tipo": _conteo_por(s, f.consorcio(
            select(Documento.tipo_documento, func.count()).group_by(Documento.tipo_documento), Documento)),
        "por_estado": _conteo_por(s, f.consorcio(
            select(Documento.estado, func.count()).group_by(Documento.estado), Documento)),
        "activos": por_activo.get(True, 0),
        "inactivos": por_activo.get(False, 0),
        "duplicados_evitados": s.scalar(duplicados),
    }


def _contextos(s: Session, f: _Filtro) -> dict:
    """Base: los turnos de la funcionalidad que usa contexto, incluidos los que no encontraron nada.
    `secciones_indexadas` son las que el buscador puede usar, las de documentos activos."""
    indexadas = f.consorcio(
        select(func.count()).select_from(Seccion).join(Documento, Seccion.documento_id == Documento.id)
        .where(Documento.activo.is_(True)),
        Documento,
    )
    cantidad = func.json_array_length(Turno.secciones_usadas).label("secciones")
    q = f.turnos(select(cantidad, func.count()).where(Turno.funcionalidad == FUNCIONALIDAD_CON_CITA).group_by(cantidad))
    distribucion = dict(sorted(_conteo_por(s, q).items()))
    turnos = sum(distribucion.values())
    usadas = sum(secciones * n for secciones, n in distribucion.items())
    return {
        "secciones_indexadas": s.scalar(indexadas),
        "secciones_por_respuesta": {"promedio": _cociente(usadas, turnos), "distribucion": distribucion},
        "respuestas_con_contexto": turnos - distribucion.get(0, 0),
    }


def _actividad(s: Session, f: _Filtro) -> list[dict]:
    """Los días sin uso van en cero: sin ellos el gráfico saltaría fechas sin que se note."""
    dia = func.date_trunc("day", func.timezone("UTC", Turno.creado_en)).label("dia")
    derivadas = func.count().filter(Turno.resultado == RESULTADO_DERIVADA)
    filas = s.execute(f.turnos(select(dia, func.count(), derivadas).group_by(dia))).all()
    por_dia = {momento.date(): (turnos, derivados) for momento, turnos, derivados in filas}
    inicio, fin = f.desde.astimezone(UTC).date(), (f.hasta - timedelta(microseconds=1)).astimezone(UTC).date()
    dias = [date.fromordinal(n) for n in range(inicio.toordinal(), fin.toordinal() + 1)]
    return [{"dia": d.isoformat(), "turnos": por_dia.get(d, (0, 0))[0], "derivadas": por_dia.get(d, (0, 0))[1]} for d in dias]


def _periodo_anterior(s: Session, f: _Filtro) -> dict:
    previo = _Filtro(f.desde - (f.hasta - f.desde), f.desde, f.consorcio_id)
    return {
        "preguntas": s.scalar(previo.turnos(select(func.count()).select_from(Turno))),
        "elevaciones": s.scalar(previo.solicitudes(select(func.count()).select_from(Solicitud), "derivacion")),
    }


def _preguntas(s: Session, f: _Filtro) -> dict:
    por_resultado = _conteo_por(s, f.turnos(select(Turno.resultado, func.count()).group_by(Turno.resultado)))
    return {"total": sum(por_resultado.values()), "por_resultado": por_resultado}


def _funcionalidades(s: Session, f: _Filtro) -> dict:
    q = f.turnos(select(Turno.funcionalidad, func.count()).group_by(Turno.funcionalidad))
    conteos = _conteo_por(s, q)
    return {nombre: conteos.get(nombre, 0) for nombre in FUNCIONALIDADES}


def _intenciones(s: Session, f: _Filtro) -> dict:
    q = f.turnos(select(Turno.intencion, func.count()).group_by(Turno.intencion))
    return {intencion or SIN_CLASIFICAR: n for intencion, n in s.execute(q).all()}


def _elevaciones(s: Session, f: _Filtro) -> dict:
    """Las derivaciones creadas en el período; `pendientes` no incluye las anteriores. La mediana es
    espera hasta la resolución, no esfuerzo humano."""
    por_motivo = _conteo_por(s, f.solicitudes(
        select(Solicitud.motivo, func.count()).group_by(Solicitud.motivo), "derivacion"))
    por_estado = _conteo_por(s, f.solicitudes(
        select(Solicitud.estado, func.count()).group_by(Solicitud.estado), "derivacion"))
    espera = cast(func.extract("epoch", Solicitud.resuelta_en - Solicitud.creado_en), Float)
    mediana = s.scalar(f.solicitudes(select(func.percentile_cont(0.5).within_group(espera)), "derivacion")
                       .where(Solicitud.estado == "resuelta"))
    return {
        "total": sum(por_motivo.values()), "por_motivo": por_motivo, "resueltas": por_estado.get("resuelta", 0),
        "pendientes": por_estado.get("pendiente", 0),
        "tiempo_hasta_resolucion_mediana_s": None if mediana is None else round(float(mediana), 1),
    }


def _acciones(s: Session, f: _Filtro) -> dict:
    por_estado = _conteo_por(s, f.solicitudes(
        select(Solicitud.estado, func.count()).group_by(Solicitud.estado), "reclamo"))
    return {
        "aprobadas": por_estado.get("aprobada", 0), "editadas": por_estado.get("editada", 0),
        "rechazadas": por_estado.get("rechazada", 0), "pendientes": por_estado.get("pendiente", 0),
    }


def _costos(s: Session, f: _Filtro) -> dict:
    """Los turnos sin precio suman 0 acá: el aviso de `modelos_sin_precio` dice que el gasto está subestimado.
    La resta usa los turnos de todos los orígenes: el libro asienta también lo de evaluación, y descontarlo
    es lo que evita que un turno de evaluación aparezca como "ingesta y otros". `max` porque restar sumas
    de float puede dar apenas negativo."""
    q = f.turnos(select(Turno.funcionalidad, func.count(), func.coalesce(func.sum(Turno.costo_usd), 0.0))
                 .group_by(Turno.funcionalidad))
    por_funcionalidad = {nombre: {"turnos": 0, "costo_usd": 0.0} for nombre in (*FUNCIONALIDADES, SIN_FUNCIONALIDAD)}
    for funcionalidad, turnos, costo in s.execute(q).all():
        fila = por_funcionalidad[funcionalidad if funcionalidad in FUNCIONALIDADES else SIN_FUNCIONALIDAD]
        fila["turnos"] += turnos
        fila["costo_usd"] = round(fila["costo_usd"] + costo, 6)
    libro = s.scalar(f.periodo(select(func.coalesce(func.sum(GastoModelo.costo_usd), 0.0)), GastoModelo))
    turnos = s.scalar(f.periodo(select(func.coalesce(func.sum(Turno.costo_usd), 0.0)), Turno))
    return {
        "por_funcionalidad": por_funcionalidad, "ingesta_y_otros": round(max(libro - turnos, 0.0), 6),
        "tope": {**estado_tope(), "alcance": "global"},
    }


def _cita_verificada(s: Session, f: _Filtro) -> dict:
    """Las derivadas cuentan en `n`: no haber podido citar es parte de lo que se mide."""
    q = f.turnos(
        select(func.count(), func.coalesce(func.sum(cast(Consulta.verificada, Integer)), 0))
        .select_from(Turno).join(Consulta, Turno.consulta_id == Consulta.id)
        .where(Turno.funcionalidad == FUNCIONALIDAD_CON_CITA)
    )
    n, aciertos = s.execute(q).one()
    return {"valor": _cociente(aciertos, n), "aciertos": aciertos, "n": n}


def _feedback_positivo(s: Session, f: _Filtro, turnos: int) -> dict:
    """`cobertura`: qué parte de los turnos del período recibió feedback."""
    por_valor = _conteo_por(s, f.turnos(select(Turno.feedback, func.count()).group_by(Turno.feedback)))
    positivos = por_valor.get(1, 0)
    n = positivos + por_valor.get(-1, 0)
    return {"valor": _cociente(positivos, n), "positivos": positivos, "n": n, "cobertura": _cociente(n, turnos)}


def _exactitud_evaluacion() -> dict:
    """La última corrida de `consorcio evaluar-consultas` sobre test. Mide `preguntar`, no el router.
    Solo vale la última: si no tiene generación, volver a una anterior mostraría un dato viejo como vigente."""
    sin_corrida = {"valor": None, "estado": "sin corrida"}
    archivos = sorted(a for a in DIR_EVALUACIONES.glob("*-consultas-test.json") if _ARCHIVO_EVALUACION.fullmatch(a.name))
    if not archivos:
        return sin_corrida
    ultimo = archivos[-1]
    try:
        corrida = json.loads(ultimo.read_text(encoding="utf-8"))
        generacion = corrida.get("generacion") or {}
        valor, n, particion = generacion.get("tasa_correctas"), generacion.get("n"), corrida.get("particion")
    except (OSError, ValueError, AttributeError) as e:
        log.warning("no se pudo leer la evaluación %s: %s", ultimo.name, type(e).__name__)
        return sin_corrida
    if valor is None or particion != "test":
        return sin_corrida
    return {"valor": valor, "n": n, "fecha": _ARCHIVO_EVALUACION.fullmatch(ultimo.name).group(1), "fuente": ultimo.name}


def consorcios() -> list[str]:
    """Los ids, para el filtro de la pantalla: el nombre no hace falta y `chat-<id>` ya es un slug."""
    crear_tablas()
    with Session(engine()) as s:
        return list(s.scalars(select(Consorcio.id).order_by(Consorcio.id)))


def panel(desde: datetime, hasta: datetime, consorcio_id: str | None = None) -> dict:
    """Todas las métricas de `desde <= creado_en < hasta`; serializable con `json.dumps`.

    `acertadas` son tres indicadores que miden cosas distintas: no se promedian."""
    crear_tablas()
    f = _Filtro(desde, hasta, consorcio_id)
    with Session(engine()) as s:
        preguntas = _preguntas(s, f)
        return {
            "usuarios": _usuarios(s, f),
            "uso": _uso(s, f),
            "documentos": _documentos(s, f),
            "contextos": _contextos(s, f),
            "preguntas": preguntas,
            "actividad": _actividad(s, f),
            "periodo_anterior": _periodo_anterior(s, f),
            "funcionalidades": _funcionalidades(s, f),
            "intenciones": _intenciones(s, f),
            "elevaciones": _elevaciones(s, f),
            "acciones": _acciones(s, f),
            "costos": _costos(s, f),
            "acertadas": {
                "cita_verificada": _cita_verificada(s, f),
                "feedback_positivo": _feedback_positivo(s, f, preguntas["total"]),
                "exactitud_evaluacion": _exactitud_evaluacion(),
            },
        }
