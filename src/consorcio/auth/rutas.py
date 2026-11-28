"""Funciones HTTP de la API JSON: invitar, registrarse, login y quién soy. La lógica está en `cuentas.py`."""
import re
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AfterValidator, BaseModel, Field, StringConstraints

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

MAX_EMAIL = 320  # el mismo tope que la web
MAX_TEXTO = 1024  # token y contraseña: de sobra, y Argon2 no recibe megas
MAX_ID = 2**31 - 1
_FORMA_MAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s.]+")


def _mail(valor: str) -> str:
    """Validación propia y no `EmailStr`: email-validator rechaza los dominios reservados (`@demo.test` de la
    semilla), que la web sí acepta. Alcanza con la forma usuario@dominio.tld: la invitación prueba el resto."""
    valor = valor.strip().lower()
    if not _FORMA_MAIL.fullmatch(valor):
        raise ValueError("no es un mail válido")
    return valor


def _sin_nul(valor: str) -> str:
    if "\x00" in valor:
        raise ValueError("no puede contener el carácter NUL")
    return valor


Mail = Annotated[str, StringConstraints(max_length=MAX_EMAIL), AfterValidator(_sin_nul), AfterValidator(_mail)]
Texto = Annotated[str, StringConstraints(max_length=MAX_TEXTO), AfterValidator(_sin_nul)]
# `strip_whitespace` corre antes de `min_length`: un nombre de puros espacios no llega a la base
Nombre = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200), AfterValidator(_sin_nul)]


class PedidoInvitacion(BaseModel):
    consorcio_id: Annotated[str, StringConstraints(max_length=200), AfterValidator(_sin_nul)]
    email: Mail
    rol: Rol
    unidad_id: int | None = Field(default=None, gt=0, le=MAX_ID)


class PedidoRegistro(BaseModel):
    token: Texto
    nombre: Nombre
    contrasena: Annotated[Texto, Field(min_length=config.LARGO_MINIMO_CONTRASENA)]


class PedidoLogin(BaseModel):
    email: Mail
    contrasena: Texto


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
