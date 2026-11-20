"""Ficha de un consorcio para la pantalla Consorcios: documentos, inquilinos, integrantes y eventos importantes.

Todo sale de una `Identidad` resuelta para ese consorcio, así que nada cruza a un consorcio ajeno. Inquilinos e
integrantes son datos personales (nombre de cada vecino): solo los ve quien administra el consorcio; un vecino ve
documentos y eventos.

Eventos importantes junta lo que tiene fecha y le importa al edificio: los vencimientos de cada liquidación, los
eventos que la ingesta extrajo de los documentos activos (asambleas, obras, plazos del reglamento) y, para la
administración, los reclamos y derivaciones. Las fechas de los documentos vienen como las devolvió el modelo, por
eso se muestran tal cual y se ordenan por la fecha de carga, no por el texto.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..almacen.postgres import (
    Documento,
    Evento,
    Liquidacion,
    Membresia,
    Solicitud,
    UnidadFuncional,
    Usuario,
    crear_tablas,
    engine,
    listar_documentos,
)
from ..identidad import Identidad

SECCIONES = {"documentos": "Documentos", "inquilinos": "Inquilinos", "integrantes": "Integrantes",
             "eventos": "Eventos importantes"}
SOLO_ADMINISTRACION = {"inquilinos", "integrantes"}
MAX_EVENTOS = 12
ORDEN_ROL = {"administracion": 0, "consejo": 1, "propietario": 2, "inquilino": 3}


def secciones_para(identidad: Identidad) -> dict[str, str]:
    return {k: v for k, v in SECCIONES.items() if identidad.es_administracion or k not in SOLO_ADMINISTRACION}


def ficha(identidad: Identidad) -> dict:
    crear_tablas()
    cid = identidad.consorcio_id
    datos: dict = {"secciones": secciones_para(identidad), "documentos": listar_documentos(cid)}
    with Session(engine()) as s:
        if identidad.es_administracion:
            miembros = _miembros(s, cid)
            datos["integrantes"] = _integrantes(miembros)
            datos["inquilinos"] = sorted((m for m in miembros if m["unidad"]),
                                         key=lambda m: (m["unidad"], ORDEN_ROL.get(m["rol"], 9), m["nombre"]))
        datos["eventos"] = _eventos(s, cid, identidad.es_administracion)
    return datos


def _miembros(s: Session, cid: str) -> list[dict]:
    filas = s.execute(
        select(Usuario.nombre, Usuario.email, Membresia.rol, UnidadFuncional.piso, UnidadFuncional.depto)
        .join(Usuario, Usuario.id == Membresia.usuario_id)
        .outerjoin(UnidadFuncional, UnidadFuncional.id == Membresia.unidad_id)
        .where(Membresia.consorcio_id == cid, Membresia.activo, Usuario.activo)
    ).all()
    return [{"nombre": n or e or "Sin nombre", "rol": r, "unidad": f"{p}-{d}" if p or d else ""}
            for n, e, r, p, d in filas]


def _integrantes(miembros: list[dict]) -> list[dict]:
    """Una fila por persona con todos sus roles: alguien puede ser propietario de dos unidades y del consejo."""
    por_persona: dict[str, dict] = {}
    for m in miembros:
        p = por_persona.setdefault(m["nombre"], {"nombre": m["nombre"], "roles": [], "unidades": []})
        if m["rol"] not in p["roles"]:
            p["roles"].append(m["rol"])
        if m["unidad"] and m["unidad"] not in p["unidades"]:
            p["unidades"].append(m["unidad"])
    for p in por_persona.values():
        p["roles"].sort(key=lambda r: ORDEN_ROL.get(r, 9))
    return sorted(por_persona.values(), key=lambda p: (ORDEN_ROL.get(p["roles"][0], 9), p["nombre"]))


def _eventos(s: Session, cid: str, administra: bool) -> list[dict]:
    eventos = []
    for periodo, venc1, venc2, cargado in s.execute(
        select(Liquidacion.periodo, Liquidacion.primer_vencimiento, Liquidacion.segundo_vencimiento, Documento.creado_en)
        .join(Documento, Documento.id == Liquidacion.documento_id)
        .where(Documento.consorcio_id == cid, Documento.activo)
    ).all():
        vence = " y ".join(v for v in (venc1, venc2) if v)
        eventos.append({"tipo": "vencimiento", "fecha": vence or "sin fecha", "orden": cargado,
                        "texto": f"Vencimiento de las expensas {periodo}"})
    for fecha, tipo, descripcion, titulo, cargado in s.execute(
        select(Evento.fecha, Evento.tipo, Evento.descripcion, Documento.titulo, Documento.creado_en)
        .join(Documento, Documento.id == Evento.documento_id)
        .where(Documento.consorcio_id == cid, Documento.activo)
    ).all():
        eventos.append({"tipo": tipo or "evento", "fecha": fecha or "sin fecha", "orden": cargado,
                        "texto": descripcion, "fuente": titulo})
    if administra:
        for tipo, estado, titulo, creado in s.execute(
            select(Solicitud.tipo, Solicitud.estado, Solicitud.titulo, Solicitud.creado_en)
            .where(Solicitud.consorcio_id == cid).order_by(Solicitud.creado_en.desc()).limit(MAX_EVENTOS)
        ).all():
            eventos.append({"tipo": tipo, "fecha": creado.strftime("%d/%m/%Y"), "orden": creado,
                            "texto": f"{'Reclamo' if tipo == 'reclamo' else 'Consulta a la administración'}: {titulo}",
                            "estado": estado})
    eventos.sort(key=lambda e: e["orden"], reverse=True)
    return eventos[:MAX_EVENTOS]
