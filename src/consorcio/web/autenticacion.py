"""Quién autentica. Es la única pieza que reemplaza el login real: entrega un `sujeto_externo` en `/entrar`
y el resto de la web no sabe cómo se obtuvo.

`proveedor_actual` elige la implementación. La sesión guarda el `nombre` del proveedor, y por eso una
cookie del selector deja de valer en cuanto se apaga el selector.
"""
from __future__ import annotations

from typing import Protocol

from fastapi import HTTPException, Request

from ..config import config
from ..identidad import listar_usuarios_demo


class ProveedorIdentidad(Protocol):
    nombre: str

    def sujeto(self, request: Request) -> str | None: ...


class SelectorDesarrollo:
    """Solo modo desarrollo: ofrece los usuarios `demo-*` y valida el pedido contra esa misma lista."""

    nombre = "selector"

    def sujeto(self, request: Request) -> str | None:
        formulario = getattr(request.state, "formulario", None)
        elegido = formulario.get("sujeto") if formulario is not None else None
        return elegido if elegido in {u["sujeto_externo"] for u in listar_usuarios_demo()} else None


class SinProveedor:
    """Sin selector y sin login real no entra nadie: `/entrar` responde 401."""

    nombre = "ninguno"

    def sujeto(self, request: Request) -> str | None:
        return None


def proveedor_actual() -> ProveedorIdentidad:
    return SelectorDesarrollo() if config.web_selector_usuarios else SinProveedor()


def exigir_proveedor() -> ProveedorIdentidad:
    proveedor = proveedor_actual()
    if isinstance(proveedor, SinProveedor):
        raise HTTPException(status_code=401)
    return proveedor
