"""Quién es el usuario dentro de un consorcio.

Agnóstico al login: quien autentique solo entrega `sujeto_externo` y `resolver_identidad` devuelve
la identidad en ese consorcio, o falla si no hay membresía. El rol y las unidades informan, no
filtran: dentro de un consorcio los propietarios pueden ver las expensas por unidad, porque así
circula la liquidación. Lo que nunca se cruza es de un consorcio a otro.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .almacen.postgres import Consorcio, Membresia, Usuario, crear_tablas, engine


class SinMembresia(Exception):
    """El mensaje es siempre el mismo: no revela si falta el usuario, el consorcio o la membresía."""


MENSAJE_SIN_MEMBRESIA = "No hay membresía para ese usuario en ese consorcio."


@dataclass(frozen=True)
class Identidad:
    usuario_id: int
    consorcio_id: str
    rol: str  # administrador | propietario
    unidades: tuple[int, ...]  # UF de la membresía


def resolver_identidad(sujeto_externo: str, consorcio_id: str) -> Identidad:
    crear_tablas()
    with Session(engine()) as s:
        membresia = s.scalars(
            select(Membresia).join(Usuario, Usuario.id == Membresia.usuario_id).where(
                Usuario.sujeto_externo == sujeto_externo, Membresia.consorcio_id == consorcio_id
            )
        ).first()
        if membresia is None:
            raise SinMembresia(MENSAJE_SIN_MEMBRESIA)
        return Identidad(
            usuario_id=membresia.usuario_id, consorcio_id=membresia.consorcio_id, rol=membresia.rol,
            unidades=tuple(sorted(u.uf for u in membresia.unidades)),
        )


def es_plataforma(sujeto_externo: str) -> bool:
    """Equipo de la plataforma: entra al CRM, sin necesidad de membresía en ningún consorcio."""
    crear_tablas()
    with Session(engine()) as s:
        return bool(s.scalar(select(Usuario.plataforma).where(Usuario.sujeto_externo == sujeto_externo)))


def membresias_de(sujeto_externo: str) -> list[dict]:
    """Los consorcios de un usuario, para elegir en cuál entrar."""
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(
            select(Membresia.consorcio_id, Consorcio.nombre, Membresia.rol)
            .join(Usuario, Usuario.id == Membresia.usuario_id).join(Consorcio, Consorcio.id == Membresia.consorcio_id)
            .where(Usuario.sujeto_externo == sujeto_externo).order_by(Consorcio.nombre)
        ).all()
    return [{"consorcio_id": cid, "nombre": nombre, "rol": rol} for cid, nombre, rol in filas]


def registrar_actividad(usuario_id: int, cada_segundos: int = 60) -> None:
    """Un solo UPDATE condicional: pedidos en paralelo no escriben más de una vez por intervalo."""
    crear_tablas()
    with engine().begin() as con:
        con.execute(
            text("UPDATE usuarios SET ultima_actividad = now() WHERE id = :id AND "
                 "(ultima_actividad IS NULL OR ultima_actividad < now() - make_interval(secs => :s))"),
            {"id": usuario_id, "s": cada_segundos},
        )


def listar_usuarios_demo() -> list[dict]:
    """Lo único que ofrece el selector de desarrollo: nunca lista usuarios reales."""
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(
            select(Usuario.sujeto_externo, Usuario.nombre).where(Usuario.sujeto_externo.like("demo-%"))
            .order_by(Usuario.sujeto_externo)
        ).all()
    return [{"sujeto_externo": sujeto, "nombre": nombre} for sujeto, nombre in filas]
