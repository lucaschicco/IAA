"""Quién es el usuario de la API JSON y qué puede hacer.

La web no usa esto: resuelve la identidad con `consorcio.identidad`, a partir del `sujeto_externo` que le
entrega el login."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import obtener_conexion
from .seguridad import leer_token_sesion

_bearer = HTTPBearer(auto_error=False)


def _no_autenticado():
    return HTTPException(status.HTTP_401_UNAUTHORIZED, "No autenticado",
                         headers={"WWW-Authenticate": "Bearer"})


def usuario_actual(credenciales: HTTPAuthorizationCredentials | None = Depends(_bearer),
                   con=Depends(obtener_conexion)) -> dict:
    """Lee el token del pedido y devuelve el usuario con sus membresías vigentes."""
    if credenciales is None:
        raise _no_autenticado()
    usuario_id = leer_token_sesion(credenciales.credentials)
    if usuario_id is None:
        raise _no_autenticado()
    usuario = con.execute(
        "SELECT id, email, nombre, activo FROM usuarios WHERE id = %s", (usuario_id,)
    ).fetchone()
    if usuario is None or not usuario["activo"]:
        raise _no_autenticado()
    usuario["membresias"] = con.execute(
        """SELECT m.consorcio_id, c.nombre AS consorcio, m.unidad_id,
                  u.piso, u.depto, u.uf, m.rol
           FROM membresias m
           JOIN consorcios c ON c.id = m.consorcio_id
           LEFT JOIN unidades_funcionales u ON u.id = m.unidad_id
           WHERE m.usuario_id = %s AND m.activo
           ORDER BY m.consorcio_id, m.rol""",
        (usuario_id,),
    ).fetchall()
    return usuario


def tiene_rol(usuario: dict, consorcio_id: str, roles: set[str]) -> bool:
    return any(m["consorcio_id"] == consorcio_id and m["rol"] in roles
               for m in usuario["membresias"])


def exigir_rol(usuario: dict, consorcio_id: str, roles: set[str]) -> None:
    if not tiene_rol(usuario, consorcio_id, roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No tenés permiso para esto")


def unidades_propias(usuario: dict, consorcio_id: str) -> list[int]:
    """Las unidades (ids de `unidades_funcionales`) que la persona puede consultar en ese consorcio."""
    return sorted({m["unidad_id"] for m in usuario["membresias"]
                   if m["consorcio_id"] == consorcio_id
                   and m["rol"] in ("propietario", "inquilino")
                   and m["unidad_id"] is not None})
