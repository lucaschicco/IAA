"""Quién autentica. Es la única pieza de la web que sabe del login: entrega un `sujeto_externo` en `/entrar`
y el resto no sabe cómo se obtuvo.

- `ProveedorLogin`: mail y contraseña, con la lógica de Lucas (`auth/cuentas.py`): Argon2, bloqueo tras
  intentos fallidos y registro en `accesos`. Es el de siempre.
- `SelectorDesarrollo`: solo con `WEB_SELECTOR_USUARIOS=true`; ofrece los usuarios `demo-*` además del login.

La sesión guarda el `nombre` del proveedor, y por eso una cookie del selector deja de valer en cuanto se apaga
el selector.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import Request

from ..auth.cuentas import (
    Bloqueado,
    CredencialesInvalidas,
    CuentaExistente,
    InvitacionInvalida,
    PedidoInvalido,
    aceptar_invitacion,
    autenticar,
    invitacion_vigente,
)
from ..auth.db import conectar
from ..config import config
from ..identidad import listar_usuarios_demo

MAX_EMAIL = 320
MAX_CONTRASENA = 1024  # Argon2 acepta más, pero nadie la escribe tan larga: corta abusos de CPU


class LoginFallido(Exception):
    """El login no prosperó. `mensaje` se muestra tal cual en el formulario."""

    def __init__(self, mensaje: str, status_code: int = 401) -> None:
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.status_code = status_code


class ProveedorIdentidad(Protocol):
    nombre: str

    def sujeto(self, request: Request) -> str | None: ...


def _formulario(request: Request):
    return getattr(request.state, "formulario", None) or {}


def _texto(datos, campo: str, maximo: int) -> str:
    valor = datos.get(campo)
    return valor[:maximo] if isinstance(valor, str) else ""


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


class ProveedorLogin:
    """Mail y contraseña contra `usuarios`. Mismo error para mail inexistente y contraseña incorrecta."""

    nombre = "login"

    def sujeto(self, request: Request) -> str | None:
        datos = _formulario(request)
        email, contrasena = _texto(datos, "email", MAX_EMAIL).strip(), _texto(datos, "contrasena", MAX_CONTRASENA)
        if not email or not contrasena:
            raise LoginFallido("Completá el mail y la contraseña.", 422)
        with conectar() as con:
            try:
                return autenticar(con, email, contrasena, _ip(request))["sujeto_externo"]
            except Bloqueado as e:
                raise LoginFallido("Demasiados intentos. Probá de nuevo en unos minutos.", 429) from e
            except CredencialesInvalidas as e:
                raise LoginFallido("Mail o contraseña incorrectos.") from e


class SelectorDesarrollo:
    """Solo modo desarrollo: ofrece los usuarios `demo-*` y valida el pedido contra esa misma lista."""

    nombre = "selector"

    def sujeto(self, request: Request) -> str | None:
        elegido = _formulario(request).get("sujeto")
        return elegido if elegido in {u["sujeto_externo"] for u in listar_usuarios_demo()} else None


def proveedor_para(request: Request) -> ProveedorIdentidad:
    """El selector solo atiende si está prendido y el formulario eligió un usuario demo."""
    if config.web_selector_usuarios and "sujeto" in _formulario(request):
        return SelectorDesarrollo()
    return ProveedorLogin()


def datos_invitacion(token: str) -> dict | None:
    if not token or len(token) > 200:
        return None
    with conectar() as con:
        return invitacion_vigente(con, token)


def registrar(request: Request) -> str:
    """Acepta la invitación del formulario y devuelve el `sujeto_externo` de la cuenta (nueva o existente)."""
    datos = _formulario(request)
    token = _texto(datos, "token", 200)
    nombre = _texto(datos, "nombre", 200).strip()
    contrasena = _texto(datos, "contrasena", MAX_CONTRASENA)
    if not token or not contrasena:
        raise LoginFallido("Faltan datos.", 422)
    if contrasena != _texto(datos, "repetida", MAX_CONTRASENA):
        raise LoginFallido("Las contraseñas no coinciden.", 422)
    with conectar() as con:
        try:
            return aceptar_invitacion(con, token, nombre or "Sin nombre", contrasena)["sujeto_externo"]
        except InvitacionInvalida as e:
            raise LoginFallido("La invitación no existe, ya se usó o venció.", 400) from e
        except CuentaExistente as e:
            raise LoginFallido("Ese mail ya tiene cuenta: usá tu contraseña actual.") from e
        except PedidoInvalido as e:
            raise LoginFallido(str(e), 422) from e
