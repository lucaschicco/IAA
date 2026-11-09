"""Funciones HTTP de la API JSON: invitar, registrarse, login y quién soy. La lógica está en `cuentas.py`."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from . import config
from .cuentas import (
    Bloqueado,
    CredencialesInvalidas,
    CuentaExistente,
    InvitacionInvalida,
    PedidoInvalido,
    aceptar_invitacion,
    autenticar,
    crear_invitacion,
)
from .db import obtener_conexion
from .permisos import exigir_rol, usuario_actual
from .seguridad import crear_token_sesion

router = APIRouter(prefix="/auth", tags=["autenticación"])

Rol = Literal["administracion", "consejo", "propietario", "inquilino"]


class PedidoInvitacion(BaseModel):
    consorcio_id: str
    email: EmailStr
    rol: Rol
    unidad_id: int | None = None


class PedidoRegistro(BaseModel):
    token: str
    nombre: str = Field(min_length=1)
    contrasena: str = Field(min_length=config.LARGO_MINIMO_CONTRASENA)


class PedidoLogin(BaseModel):
    email: EmailStr
    contrasena: str


class RespuestaToken(BaseModel):
    token: str
    tipo: str = "bearer"


@router.post("/invitaciones", status_code=status.HTTP_201_CREATED)
def invitar(pedido: PedidoInvitacion, usuario=Depends(usuario_actual), con=Depends(obtener_conexion)):
    """Solo la administración del consorcio puede invitar."""
    exigir_rol(usuario, pedido.consorcio_id, {"administracion"})
    try:
        token, vence_en = crear_invitacion(con, pedido.consorcio_id, pedido.email, pedido.rol,
                                           pedido.unidad_id, usuario["id"])
    except PedidoInvalido as e:
        raise HTTPException(422, str(e)) from e
    # Pendiente: mandar el link por mail. Por ahora se devuelve para probar; la web lo acepta en /registro.
    return {"token_invitacion": token, "vence_en": vence_en}


@router.post("/registro", response_model=RespuestaToken)
def registrarse(pedido: PedidoRegistro, con=Depends(obtener_conexion)):
    try:
        usuario = aceptar_invitacion(con, pedido.token, pedido.nombre, pedido.contrasena)
    except InvitacionInvalida as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitación inválida, usada o vencida") from e
    except CuentaExistente as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Ese mail ya tiene cuenta: usá tu contraseña actual") from e
    except PedidoInvalido as e:
        raise HTTPException(422, str(e)) from e
    return RespuestaToken(token=crear_token_sesion(usuario["id"]))


@router.post("/login", response_model=RespuestaToken)
def login(pedido: PedidoLogin, request: Request, con=Depends(obtener_conexion)):
    try:
        usuario = autenticar(con, pedido.email, pedido.contrasena, request.client.host if request.client else None)
    except Bloqueado as e:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Demasiados intentos. Probá de nuevo en unos minutos.") from e
    except CredencialesInvalidas as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mail o contraseña incorrectos") from e
    return RespuestaToken(token=crear_token_sesion(usuario["id"]))


@router.get("/yo")
def quien_soy(usuario=Depends(usuario_actual)):
    return usuario
