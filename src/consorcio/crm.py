"""Métricas del CRM: SQL puro sobre tablas sin texto libre, para el equipo de plataforma.

El CRM cruza consorcios a propósito: es la excepción autorizada a "nada cruza entre consorcios", solo
con agregados y solo detrás de `exigir_plataforma`. Por eso acá no se lee el texto de nadie: ni
`mensajes`, ni `mensaje`/`titulo`/`detalle` de `solicitudes`, ni `respuesta_correcta` de `revisiones`,
ni nombres de usuarios o consorcios.

Las evaluaciones no cuentan: `turnos` se filtra por `origen`, y `solicitudes`, que no tiene esa columna,
por el turno que la creó. `gastos_modelo` tampoco la tiene: el costo de los turnos de evaluación se resta
junto con el resto, y lo que evalúa sin pasar por `atender` queda en "ingesta y otros", porque se pagó.

Qué depende del período: `usuarios` son ventanas móviles desde ahora y `documentos` y
`secciones_indexadas` son el estado de hoy (salvo `duplicados_evitados`); el resto es del período.
Con filtro de consorcio solo `costos.tope` sigue global: el tope es uno solo, de toda la plataforma.
`actividad` va por día UTC y `periodo_anterior` es la ventana del mismo largo justo antes de `desde`.

El filtro de Administración (`administracion`: id o `SIN_ADMINISTRACION`) se aplica igual que el de consorcio, a
toda tabla con `consorcio_id`: ningún número incluye consorcios de otra Administración. `pendientes` es el estado de
hoy, sin período. `consumo` sale del libro `gastos_modelo` menos los turnos de evaluación (ver `_libro_neto`); el
nombre de la Administración es el del cliente de la plataforma y es el único nombre que se lee.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.orm import Session

from .agente import certeza
from .agente.registro import FUNCIONALIDADES
from .agente.revision import REVISABLES
from .almacen.postgres import (
    MOTIVOS_REVISION,
    VEREDICTOS,
    Administracion,
    Carga,
    Consorcio,
    Consulta,
    Documento,
    GastoModelo,
    Membresia,
    Revision,
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
SIN_ADMINISTRACION = "sin"  # valor del filtro: los consorcios con `administracion_id` NULL
ROTULO_SIN_ADMINISTRACION = "Sin administración"
CANALES_FUERA_DEL_AGENTE = ("cli", "telegram")  # `consultas.origen` que no pasan por `atender`
ROLES_VECINO = ("consejo", "propietario", "inquilino")


@dataclass(frozen=True)
class _Filtro:
    desde: datetime
    hasta: datetime
    consorcio_id: str | None
    administracion: int | str | None = None  # id, SIN_ADMINISTRACION o None (todas)

    def periodo(self, q, tabla):
        """Del consorcio elegido y con `desde <= creado_en < hasta`."""
        q = q.where(tabla.creado_en >= self.desde, tabla.creado_en < self.hasta)
        return self.consorcio(q, tabla)

    def consorcio(self, q, tabla, columna=None):
        """Del consorcio y de la Administración elegidos. `columna`: para tablas cuyo consorcio no se llama
        `consorcio_id` (la propia `consorcios`)."""
        columna = tabla.consorcio_id if columna is None else columna
        if self.consorcio_id:
            q = q.where(columna == self.consorcio_id)
        if self.administracion is not None:
            q = q.where(columna.in_(self.consorcios_de_la_administracion()))
        return q

    def consorcios_de_la_administracion(self):
        if self.administracion == SIN_ADMINISTRACION:
            return select(Consorcio.id).where(Consorcio.administracion_id.is_(None))
        return select(Consorcio.id).where(Consorcio.administracion_id == self.administracion)

    def turnos(self, q):
        return self.periodo(q, Turno).where(Turno.origen != ORIGEN_EVALUACION)

    def solicitudes(self, q, tipo: str):
        """Sin `origen` propio: una solicitud es de evaluación si la creó un turno de evaluación."""
        return self.periodo(q, Solicitud).where(Solicitud.tipo == tipo, Solicitud.id.not_in(_de_evaluacion()))


def _de_evaluacion():
    """Ids de las solicitudes que creó un turno de evaluación."""
    return select(Turno.solicitud_id).where(Turno.origen == ORIGEN_EVALUACION, Turno.solicitud_id.is_not(None))


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
    if f.consorcio_id or f.administracion is not None:
        q = q.where(Usuario.id.in_(f.consorcio(select(Membresia.usuario_id), Membresia)))

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
    previo = replace(f, desde=f.desde - (f.hasta - f.desde), hasta=f.desde)
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


def _tasa(filas: list[tuple]) -> dict:
    n = len(filas)
    correctas = sum(1 for f in filas if f[0] == "correcta")
    return {"n": n, "correctas": correctas, "tasa": _cociente(correctas, n)}


def _calidad(s: Session, f: _Filtro) -> dict:
    """Revisiones de la administración sobre los turnos del período, y la certeza calculada.

    Solo veredicto, motivo, intención y certeza: `revisiones.respuesta_correcta` es texto del consorcio y
    acá no se lee. `calibracion` compara, por nivel de certeza, cuántas revisadas resultaron correctas:
    si el medidor sirve, la tasa baja de `alta` a `baja`."""
    filas = s.execute(f.turnos(
        select(Revision.veredicto, Revision.motivo, Turno.intencion, Turno.certeza)
        .select_from(Revision).join(Turno, Turno.id == Revision.turno_id)
    )).all()
    revisables = s.scalar(f.turnos(
        select(func.count()).select_from(Turno)
        .where(Turno.resultado.in_(REVISABLES), Turno.mensaje_asistente_id.is_not(None))
    ))
    certezas = list(s.scalars(f.turnos(select(Turno.certeza).where(Turno.certeza.is_not(None)))))
    por_intencion: dict[str, list] = {}
    por_nivel: dict[str, list] = {n: [] for n in (*certeza.NIVELES, "sin_certeza")}
    for fila in filas:
        por_intencion.setdefault(fila.intencion or SIN_CLASIFICAR, []).append(fila)
        por_nivel[certeza.nivel(fila.certeza) or "sin_certeza"].append(fila)
    general = _tasa(filas)
    return {
        **general, "revisables": revisables, "cobertura": _cociente(general["n"], revisables),
        "por_veredicto": {v: sum(1 for x in filas if x.veredicto == v) for v in VEREDICTOS},
        "por_motivo": {m: sum(1 for x in filas if x.motivo == m) for m in MOTIVOS_REVISION if any(x.motivo == m for x in filas)},
        "por_intencion": {k: _tasa(v) for k, v in sorted(por_intencion.items())},
        "calibracion": {k: _tasa(v) for k, v in por_nivel.items()},
        "certeza": {
            "promedio": round(sum(certezas) / len(certezas), 3) if certezas else None,
            "por_nivel": {n: sum(1 for c in certezas if certeza.nivel(c) == n) for n in certeza.NIVELES},
            "version": certeza.VERSION,
        },
    }


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


# ------------------------------------------------------------------ uso por persona, tasas y pendientes


def _usuarios_que_preguntaron(s: Session, f: _Filtro, preguntas: int) -> dict:
    """U3 y U4: personas distintas con al menos un turno en el período. Por rol y por consorcio una misma
    persona puede contar en más de una fila, así que esas filas no suman el total."""
    distintos = func.count(func.distinct(Turno.usuario_id))
    total = s.scalar(f.turnos(select(distintos)))
    return {
        "total": total,
        "por_rol": _conteo_por(s, f.turnos(select(Turno.rol, distintos).group_by(Turno.rol))),
        "por_consorcio": _conteo_por(s, f.turnos(select(Turno.consorcio_id, distintos).group_by(Turno.consorcio_id))),
        "preguntas_por_usuario": None if not total else round(preguntas / total, 2),
    }


def _tasas(s: Session, f: _Filtro, preguntas: dict, acciones: dict) -> dict:
    """U6, U11 y U12, cada una con su numerador y su denominador, y None sin casos.

    U12: derivaciones `elevada_usuario` del período sobre turnos `respondida` del período. Por funcionalidad, la
    elevación cuenta en la funcionalidad del turno que se elevó (`turnos.solicitud_id`)."""
    derivadas = preguntas["por_resultado"].get(RESULTADO_DERIVADA, 0)
    decididas = acciones["aprobadas"] + acciones["editadas"] + acciones["rechazadas"]
    clave = func.coalesce(Turno.funcionalidad, SIN_FUNCIONALIDAD)
    respondidas_por = _conteo_por(s, f.turnos(
        select(clave, func.count()).where(Turno.resultado == "respondida").group_by(clave)))
    elevadas_por = _conteo_por(s, f.solicitudes(
        select(clave, func.count()).select_from(Solicitud).join(Turno, Turno.solicitud_id == Solicitud.id)
        .where(Solicitud.motivo == "elevada_usuario").group_by(clave), "derivacion"))
    por_motivo = _conteo_por(s, f.solicitudes(
        select(Solicitud.motivo, func.count()).where(Solicitud.motivo.in_(("elevada_usuario", "pedido_directo")))
        .group_by(Solicitud.motivo), "derivacion"))
    elevadas, respondidas = por_motivo.get("elevada_usuario", 0), sum(respondidas_por.values())
    return {
        "derivacion": {"valor": _cociente(derivadas, preguntas["total"]), "derivadas": derivadas,
                       "n": preguntas["total"]},
        "edicion_reclamos": {"valor": _cociente(acciones["editadas"], decididas), "editadas": acciones["editadas"],
                             "decididas": decididas},
        "elevadas_tras_respuesta": {
            "valor": _cociente(elevadas, respondidas), "elevadas": elevadas, "respondidas": respondidas,
            "pedidos_directos": por_motivo.get("pedido_directo", 0),
            "por_funcionalidad": {
                nombre: {"elevadas": elevadas_por.get(nombre, 0), "respondidas": respondidas_por.get(nombre, 0),
                         "valor": _cociente(elevadas_por.get(nombre, 0), respondidas_por.get(nombre, 0))}
                for nombre in sorted(set(respondidas_por) | set(elevadas_por))
            },
        },
    }


def _antiguedad(ahora: datetime, momento: datetime | None) -> float | None:
    return None if momento is None else round(max((ahora - momento).total_seconds(), 0.0), 1)


def _pendientes(s: Session, f: _Filtro, ahora: datetime) -> dict:
    """U10: lo que espera a la administración hoy, sin filtro de período. La antigüedad es la de la más vieja."""
    filas = s.execute(f.consorcio(
        select(Solicitud.consorcio_id, Solicitud.tipo, func.count(), func.min(Solicitud.creado_en))
        .where(Solicitud.estado == "pendiente", Solicitud.id.not_in(_de_evaluacion()))
        .group_by(Solicitud.consorcio_id, Solicitud.tipo), Solicitud)).all()
    por_tipo: dict[str, int] = {}
    por_consorcio: dict[str, dict] = {}
    for cid, tipo, n, primera in filas:
        por_tipo[tipo] = por_tipo.get(tipo, 0) + n
        fila = por_consorcio.setdefault(cid, {"n": 0, "primera": primera})
        fila["n"] += n
        fila["primera"] = min(fila["primera"], primera)
    primera = min((c["primera"] for c in por_consorcio.values()), default=None)
    return {
        "total": sum(por_tipo.values()), "por_tipo": por_tipo, "mas_antigua_s": _antiguedad(ahora, primera),
        "por_consorcio": {cid: {"n": c["n"], "mas_antigua_s": _antiguedad(ahora, c["primera"])}
                          for cid, c in sorted(por_consorcio.items(), key=lambda x: x[1]["primera"])},
    }


def _cargas(s: Session, f: _Filtro) -> dict:
    """U14: cómo terminaron las cargas del período."""
    return _conteo_por(s, f.periodo(select(Carga.estado, func.count()).group_by(Carga.estado), Carga))


def _latencia(s: Session, f: _Filtro) -> dict:
    """C9: p50 y p95 de `turnos.segundos`, lo que esperó la persona (con o sin llamada al modelo)."""
    clave = func.coalesce(Turno.funcionalidad, SIN_FUNCIONALIDAD)
    percentiles = (func.count(), func.percentile_cont(0.5).within_group(Turno.segundos),
                   func.percentile_cont(0.95).within_group(Turno.segundos))

    def fila(n, p50, p95) -> dict:
        return {"n": n, "p50_s": None if p50 is None else round(float(p50), 2),
                "p95_s": None if p95 is None else round(float(p95), 2)}

    total = s.execute(f.turnos(select(*percentiles))).one()
    por = s.execute(f.turnos(select(clave, *percentiles).group_by(clave).order_by(clave))).all()
    return {"total": fila(*total), "por_funcionalidad": {nombre: fila(*resto) for nombre, *resto in por}}


# ------------------------------------------------------------------ consumo


_CERO = (0.0, 0, 0)


def _libro_neto(s: Session, f: _Filtro, clave) -> dict:
    """Costo y tokens del libro `gastos_modelo` agrupados por `clave(tabla)`, menos los turnos de evaluación del
    mismo grupo: el libro no tiene `origen`, y sin la resta una evaluación aparecería como consumo. Lo que se
    evalúa sin pasar por `atender` (`evaluar-consultas`) queda, porque se pagó. Nunca negativo."""
    def agrupado(tabla, solo_evaluacion: bool) -> dict:
        k = clave(tabla).label("clave")
        q = select(k, func.coalesce(func.sum(tabla.costo_usd), 0.0), func.coalesce(func.sum(tabla.tokens_entrada), 0),
                   func.coalesce(func.sum(tabla.tokens_salida), 0))
        if solo_evaluacion:
            q = q.where(Turno.origen == ORIGEN_EVALUACION)
        return {k_: (c, te, ts) for k_, c, te, ts in s.execute(f.periodo(q, tabla).group_by(k))}

    libro, evaluacion = agrupado(GastoModelo, False), agrupado(Turno, True)
    return {
        k: {"costo_usd": round(max(c - evaluacion.get(k, _CERO)[0], 0.0), 6),
            "tokens_entrada": max(te - evaluacion.get(k, _CERO)[1], 0),
            "tokens_salida": max(ts - evaluacion.get(k, _CERO)[2], 0)}
        for k, (c, te, ts) in libro.items()
    }


def _vacio() -> dict:
    return {"costo_usd": 0.0, "tokens_entrada": 0, "tokens_salida": 0, "preguntas": 0}


def _sumar(destino: dict, origen: dict) -> None:
    for campo in ("costo_usd", "tokens_entrada", "tokens_salida", "preguntas"):
        destino[campo] += origen.get(campo, 0)
    destino["costo_usd"] = round(destino["costo_usd"], 6)


def _por_administracion(s: Session, f: _Filtro) -> dict:
    """C4 y C5: Administración → consorcio, con costo y tokens del libro neto y las preguntas.

    Los consorcios sin Administración (Telegram, terminal) y los ids del libro que no están en `consorcios` van a
    "Sin administración"; lo del libro sin consorcio va a `sin_consorcio`, solo sin filtro (con filtro no es de
    nadie). Se listan todas las Administraciones del filtro, y solo los consorcios con consumo o preguntas."""
    neto = _libro_neto(s, f, lambda t: t.consorcio_id)
    preguntas = _conteo_por(s, f.turnos(select(Turno.consorcio_id, func.count()).group_by(Turno.consorcio_id)))
    de_consorcio = dict(s.execute(f.consorcio(select(Consorcio.id, Consorcio.administracion_id), None, Consorcio.id)).all())
    q = select(Administracion.id, Administracion.nombre).order_by(Administracion.nombre, Administracion.id)
    if f.administracion == SIN_ADMINISTRACION:
        q = q.where(Administracion.id.is_(None))
    elif f.administracion is not None:
        q = q.where(Administracion.id == f.administracion)
    if f.consorcio_id:
        q = q.where(Administracion.id.in_(select(Consorcio.administracion_id).where(Consorcio.id == f.consorcio_id)))
    filas = {i: {"administracion_id": i, "nombre": nombre, **_vacio(), "consorcios": []} for i, nombre in s.execute(q)}
    sin = {"administracion_id": None, "nombre": ROTULO_SIN_ADMINISTRACION, **_vacio(), "consorcios": []}
    for cid in sorted((set(neto) | set(preguntas)) - {None}):
        consorcio = {"consorcio_id": cid, **_vacio()}
        _sumar(consorcio, {**neto.get(cid, {}), "preguntas": preguntas.get(cid, 0)})
        fila = filas.get(de_consorcio.get(cid), sin)
        fila["consorcios"].append(consorcio)
        _sumar(fila, consorcio)
    resultado = [*filas.values()]
    if sin["consorcios"] or f.administracion == SIN_ADMINISTRACION:
        resultado.append(sin)
    for fila in resultado:
        fila["consorcios"].sort(key=lambda c: (-c["costo_usd"], c["consorcio_id"]))
    filtrado = f.consorcio_id or f.administracion is not None
    sin_consorcio = None if filtrado else neto.get(None, {"costo_usd": 0.0, "tokens_entrada": 0, "tokens_salida": 0})
    return {"filas": resultado, "sin_consorcio": sin_consorcio}


def _costo_por_dia(s: Session, f: _Filtro) -> list[dict]:
    """C6: el libro neto por día UTC, con los días sin gasto en cero."""
    neto = _libro_neto(s, f, lambda t: func.date_trunc("day", func.timezone("UTC", t.creado_en)))
    por_dia = {momento.date(): fila["costo_usd"] for momento, fila in neto.items()}
    inicio, fin = f.desde.astimezone(UTC).date(), (f.hasta - timedelta(microseconds=1)).astimezone(UTC).date()
    return [{"dia": date.fromordinal(n).isoformat(), "costo_usd": por_dia.get(date.fromordinal(n), 0.0)}
            for n in range(inicio.toordinal(), fin.toordinal() + 1)]


def _por_pregunta_y_tokens(s: Session, f: _Filtro) -> dict:
    """C7 y C8. El costo por pregunta divide solo por los turnos con precio: uno sin precio no es gratis."""
    columnas = (func.count(Turno.costo_usd), func.coalesce(func.sum(Turno.costo_usd), 0.0),
                func.coalesce(func.sum(Turno.tokens_entrada), 0), func.coalesce(func.sum(Turno.tokens_salida), 0),
                func.count())

    def fila(con_precio, costo, entrada, salida, turnos) -> dict:
        return {"turnos": turnos, "con_precio": con_precio,
                "costo_por_pregunta_usd": round(costo / con_precio, 6) if con_precio else None,
                "tokens_entrada": entrada, "tokens_salida": salida}

    def por(clave) -> dict:
        return {nombre: fila(*resto) for nombre, *resto in
                s.execute(f.turnos(select(clave, *columnas).group_by(clave).order_by(clave)))}

    return {
        "total": fila(*s.execute(f.turnos(select(*columnas))).one()),
        "por_funcionalidad": por(func.coalesce(Turno.funcionalidad, SIN_FUNCIONALIDAD)),
        "por_modelo": por(Turno.modelo),
    }


def _por_canal(s: Session, f: _Filtro) -> dict:
    """C10: web desde `turnos` (el agente); CLI y Telegram desde `consultas.origen`, que no pasan por `atender`.
    Las consultas que hace el agente llevan `origen='web'` y ya están en su turno: no se suman dos veces. La
    ingesta no tiene canal en el libro y queda afuera."""
    canales: dict[str, dict] = {}
    turnos = f.turnos(select(Turno.canal, func.count(), func.coalesce(func.sum(Turno.costo_usd), 0.0)).group_by(Turno.canal))
    consultas = f.periodo(
        select(Consulta.origen, func.count(), func.coalesce(func.sum(Consulta.costo_usd), 0.0))
        .where(Consulta.origen.in_(CANALES_FUERA_DEL_AGENTE)).group_by(Consulta.origen), Consulta)
    for q in (turnos, consultas):
        for canal, n, costo in s.execute(q):
            fila = canales.setdefault(canal, {"preguntas": 0, "costo_usd": 0.0})
            fila["preguntas"] += n
            fila["costo_usd"] = round(fila["costo_usd"] + costo, 6)
    return dict(sorted(canales.items()))


def _ingesta_por_documento(s: Session, f: _Filtro) -> dict:
    """C11: `documentos.costo` (el resumen del `Contador`) y `segundos_total`, por método de extracción; estado de
    hoy. Un documento sin `costo_usd` (semilla, modelo sin precio, o solo Tesseract) cuenta en `sin_precio` y no
    entra en el promedio."""
    por_metodo: dict[str, dict] = {}
    for metodo, costo, segundos in s.execute(f.consorcio(
            select(Documento.metodo_extraccion, Documento.costo, Documento.segundos_total), Documento)):
        fila = por_metodo.setdefault(metodo, {"documentos": 0, "costos": [], "segundos": []})
        fila["documentos"] += 1
        valor = costo.get("costo_usd") if isinstance(costo, dict) else None
        if isinstance(valor, int | float) and not isinstance(valor, bool):
            fila["costos"].append(float(valor))
        if segundos is not None:
            fila["segundos"].append(float(segundos))
    return {
        metodo: {
            "documentos": fila["documentos"], "con_precio": len(fila["costos"]),
            "sin_precio": fila["documentos"] - len(fila["costos"]),
            "costo_promedio_usd": round(sum(fila["costos"]) / len(fila["costos"]), 6) if fila["costos"] else None,
            "segundos_promedio": round(sum(fila["segundos"]) / len(fila["segundos"]), 1) if fila["segundos"] else None,
        }
        for metodo, fila in sorted(por_metodo.items())
    }


def _consumo(s: Session, f: _Filtro) -> dict:
    return {
        "por_administracion": _por_administracion(s, f),
        "por_dia": _costo_por_dia(s, f),
        "por_pregunta": _por_pregunta_y_tokens(s, f),
        "por_canal": _por_canal(s, f),
        "ingesta_por_documento": _ingesta_por_documento(s, f),
    }


# ------------------------------------------------------------------ entrada


def consorcios(administracion: int | str | None = None) -> list[str]:
    """Los ids, para el filtro de la pantalla: el nombre no hace falta y `chat-<id>` ya es un slug.
    Con Administración, solo los suyos (o los que no tienen ninguna, con `SIN_ADMINISTRACION`)."""
    crear_tablas()
    q = select(Consorcio.id).order_by(Consorcio.id)
    if administracion == SIN_ADMINISTRACION:
        q = q.where(Consorcio.administracion_id.is_(None))
    elif administracion is not None:
        q = q.where(Consorcio.administracion_id == administracion)
    with Session(engine()) as s:
        return list(s.scalars(q))


def administraciones() -> list[dict]:
    """Id y nombre de cada Administración, para el filtro."""
    crear_tablas()
    with Session(engine()) as s:
        return [{"id": i, "nombre": n} for i, n in s.execute(
            select(Administracion.id, Administracion.nombre).order_by(Administracion.nombre, Administracion.id))]


def panel(desde: datetime, hasta: datetime, consorcio_id: str | None = None,
          administracion: int | str | None = None) -> dict:
    """Todas las métricas de `desde <= creado_en < hasta`; serializable con `json.dumps`.

    `acertadas` son tres indicadores que miden cosas distintas: no se promedian."""
    crear_tablas()
    f = _Filtro(desde, hasta, consorcio_id, administracion)
    with Session(engine()) as s:
        preguntas = _preguntas(s, f)
        acciones = _acciones(s, f)
        return {
            "usuarios_que_preguntaron": _usuarios_que_preguntaron(s, f, preguntas["total"]),
            "tasas": _tasas(s, f, preguntas, acciones),
            "pendientes": _pendientes(s, f, datetime.now(UTC)),
            "cargas": _cargas(s, f),
            "latencia": _latencia(s, f),
            "consumo": _consumo(s, f),
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
            "acciones": acciones,
            "costos": _costos(s, f),
            "calidad": _calidad(s, f),
            "acertadas": {
                "cita_verificada": _cita_verificada(s, f),
                "feedback_positivo": _feedback_positivo(s, f, preguntas["total"]),
                "exactitud_evaluacion": _exactitud_evaluacion(),
            },
        }
