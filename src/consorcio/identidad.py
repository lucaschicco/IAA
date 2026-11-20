"""Quién es el usuario dentro de un consorcio.

Agnóstico al login: quien autentique solo entrega `sujeto_externo` y `resolver_identidad` devuelve
la identidad en ese consorcio, o falla si no hay membresía activa. El login de Lucas (`auth/`) guarda varias
filas por persona y consorcio (una por unidad y rol); acá se juntan en una sola `Identidad`.

El rol y las unidades informan, no filtran: dentro de un consorcio los propietarios pueden ver las expensas por
unidad, porque así circula la liquidación. Lo que nunca se cruza es de un consorcio a otro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .almacen.postgres import (
    ROLES,
    Administracion,
    Consorcio,
    Membresia,
    UnidadFuncional,
    Usuario,
    crear_tablas,
    engine,
)
from .texto import normalizar

# El rol que manda cuando una persona tiene varios en el mismo consorcio
PRIORIDAD_ROLES = {rol: i for i, rol in enumerate(ROLES)}  # administracion > consejo > propietario > inquilino


class SinMembresia(Exception):
    """El mensaje es siempre el mismo: no revela si falta el usuario, el consorcio o la membresía."""


MENSAJE_SIN_MEMBRESIA = "No hay membresía para ese usuario en ese consorcio."


def rol_principal(roles) -> str:
    return min(roles, key=PRIORIDAD_ROLES.__getitem__)


@dataclass(frozen=True)
class Identidad:
    usuario_id: int
    consorcio_id: str
    rol: str  # el principal: administracion | consejo | propietario | inquilino
    unidades: tuple[int, ...]  # número de UF de cada unidad propia (las que no tienen UF cargada no cuentan)
    roles: frozenset[str] = frozenset()

    @property
    def es_administracion(self) -> bool:
        return "administracion" in self.roles or self.rol == "administracion"


def _membresias_activas(sujeto_externo: str):
    return (
        select(Membresia.consorcio_id, Membresia.usuario_id, Membresia.rol, UnidadFuncional.uf)
        .join(Usuario, Usuario.id == Membresia.usuario_id)
        .outerjoin(UnidadFuncional, UnidadFuncional.id == Membresia.unidad_id)
        .where(Usuario.sujeto_externo == sujeto_externo, Usuario.activo, Membresia.activo)
    )


def resolver_identidad(sujeto_externo: str, consorcio_id: str) -> Identidad:
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(_membresias_activas(sujeto_externo).where(Membresia.consorcio_id == consorcio_id)).all()
    if not filas:
        raise SinMembresia(MENSAJE_SIN_MEMBRESIA)
    roles = frozenset(f.rol for f in filas)
    return Identidad(
        usuario_id=filas[0].usuario_id, consorcio_id=consorcio_id, rol=rol_principal(roles),
        unidades=tuple(sorted({f.uf for f in filas if f.uf is not None and f.rol in ("propietario", "inquilino")})),
        roles=roles,
    )


def es_plataforma(sujeto_externo: str) -> bool:
    """Equipo de la plataforma: entra al CRM, sin necesidad de membresía en ningún consorcio."""
    crear_tablas()
    with Session(engine()) as s:
        return bool(s.scalar(select(Usuario.plataforma).where(Usuario.sujeto_externo == sujeto_externo,
                                                              Usuario.activo)))


def membresias_de(sujeto_externo: str) -> list[dict]:
    """Los consorcios de un usuario, para elegir en cuál entrar: uno por consorcio, con su rol principal."""
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(
            select(Membresia.consorcio_id, Consorcio.nombre, Membresia.rol)
            .join(Usuario, Usuario.id == Membresia.usuario_id).join(Consorcio, Consorcio.id == Membresia.consorcio_id)
            .where(Usuario.sujeto_externo == sujeto_externo, Usuario.activo, Membresia.activo)
        ).all()
    por_consorcio: dict[str, tuple[str, set[str]]] = {}
    for cid, nombre, rol in filas:
        por_consorcio.setdefault(cid, (nombre, set()))[1].add(rol)
    return sorted(
        ({"consorcio_id": cid, "nombre": nombre, "rol": rol_principal(roles)}
         for cid, (nombre, roles) in por_consorcio.items()),
        key=lambda m: m["nombre"],
    )


class ConsorcioInvalido(ValueError):
    """Nombre vacío o demasiado largo, o dirección demasiado larga."""


class AdministracionInvalida(ValueError):
    """La Administración elegida no es una de las de los consorcios que la persona administra, o falta elegirla."""


def administraciones_de(sujeto_externo: str) -> list[dict]:
    """Las Administraciones de los consorcios donde la persona es administración (sin repetir, por nombre).
    Es lo único que puede heredar un consorcio que crea: nunca una Administración ajena."""
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(
            select(Administracion.id, Administracion.nombre).distinct()
            .join(Consorcio, Consorcio.administracion_id == Administracion.id)
            .join(Membresia, Membresia.consorcio_id == Consorcio.id)
            .join(Usuario, Usuario.id == Membresia.usuario_id)
            .where(Usuario.sujeto_externo == sujeto_externo, Usuario.activo, Membresia.activo,
                   Membresia.rol == "administracion")
            .order_by(Administracion.nombre, Administracion.id)
        ).all()
    return [{"id": i, "nombre": n} for i, n in filas]


def _administracion_heredada(sujeto_externo: str, elegida: int | None) -> int | None:
    """Única → esa (y si se eligió otra, error); varias → la elegida, que tiene que ser una de esas; ninguna → NULL.
    Los consorcios administrados sin Administración no cuentan: no hay nada que heredar de ellos."""
    opciones = [a["id"] for a in administraciones_de(sujeto_externo)]
    if not opciones:
        if elegida is not None:
            raise AdministracionInvalida(elegida)
        return None
    if len(opciones) == 1 and elegida is None:
        return opciones[0]
    if elegida not in opciones:
        raise AdministracionInvalida(elegida)
    return elegida


def puede_crear_consorcio(sujeto_externo: str) -> bool:
    """Crea consorcios quien ya administra alguno: una administración que suma un edificio a su cartera."""
    return any(m["rol"] == "administracion" for m in membresias_de(sujeto_externo))


def crear_consorcio(sujeto_externo: str, nombre: str, direccion: str = "", administracion_id: int | None = None) -> str:
    """Crea un consorcio vacío con quien lo pide como su administración y devuelve el id.

    Nunca toca un consorcio existente: si el slug del nombre ya está tomado se le agrega un número. Así nadie
    se suma como administración de un consorcio ajeno eligiendo un nombre igual. La Administración se hereda de
    los consorcios que la persona ya administra (`_administracion_heredada`); `administracion_id` solo hace
    falta si tiene más de una.
    """
    nombre, direccion = " ".join(nombre.split()), " ".join(direccion.split())
    if not 3 <= len(nombre) <= 120 or len(direccion) > 200:
        raise ConsorcioInvalido(nombre)
    if not puede_crear_consorcio(sujeto_externo):
        raise PermissionError(sujeto_externo)
    administracion_id = _administracion_heredada(sujeto_externo, administracion_id)
    base = re.sub(r"\s+", "-", normalizar(nombre))[:50].strip("-") or "consorcio"
    with Session(engine()) as s, s.begin():
        usuario_id = s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == sujeto_externo, Usuario.activo))
        s.execute(text("SELECT pg_advisory_xact_lock(hashtext('crear_consorcio'))"))  # dos altas con el mismo nombre
        tomados = set(s.scalars(select(Consorcio.id).where(Consorcio.id.like(f"{base}%"))))
        consorcio_id = base if base not in tomados else next(
            f"{base}-{n}" for n in range(2, 10_000) if f"{base}-{n}" not in tomados)
        s.add(Consorcio(id=consorcio_id, nombre=nombre, direccion=direccion or None, administracion_id=administracion_id))
        s.flush()
        s.add(Membresia(usuario_id=usuario_id, consorcio_id=consorcio_id, rol="administracion", unidad_id=None))
    return consorcio_id


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
