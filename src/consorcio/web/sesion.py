"""Sesión firmada en una cookie, con HMAC-SHA256 de la biblioteca estándar, y dependencias de acceso.

La cookie solo dice quién es y en qué consorcio entró: rol y membresía se resuelven en cada pedido,
para que perder una membresía corte el acceso en el pedido siguiente.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import asdict, dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..almacen.postgres import Usuario, engine
from ..config import config
from ..identidad import (
    Identidad,
    SinMembresia,
    es_plataforma,
    registrar_actividad,
    resolver_identidad,
)

NOMBRE_COOKIE = "consorcio_sesion"
COOKIE_PREVIA = "consorcio_previa"  # ata el CSRF de /entrar, que todavía no tiene sesión
_DURACION_PREVIA = 3600  # segundos
_TOLERANCIA_RELOJ = 60  # segundos
MIN_LARGO_SECRETO = 32
_MIN_CARACTERES_DISTINTOS = 10  # 32 veces "a" tiene el largo y no el secreto

_secreto_de_proceso: str | None = None


@dataclass(frozen=True)
class Sesion:
    sujeto_externo: str
    consorcio_id: str | None
    proveedor: str  # quién autenticó: "selector" solo vale con el selector prendido
    nonce: str
    emitida: int  # segundos desde 1970


def _secreto() -> bytes:
    """Sin `WEB_SECRETO`, solo el modo desarrollo tiene secreto: uno aleatorio, por proceso."""
    global _secreto_de_proceso
    if config.web_secreto:
        return config.web_secreto.encode()
    if not config.web_selector_usuarios:
        raise RuntimeError("Falta WEB_SECRETO")
    _secreto_de_proceso = _secreto_de_proceso or secrets.token_urlsafe(32)
    return _secreto_de_proceso.encode()


def secreto_debil(secreto: str) -> bool:
    """Quien adivina el secreto forja una sesión de cualquier usuario, incluida la de plataforma."""
    limpio = secreto.strip()
    return len(limpio) < MIN_LARGO_SECRETO or len(set(limpio)) < _MIN_CARACTERES_DISTINTOS


def _firma(contenido: str) -> str:
    return hmac.new(_secreto(), contenido.encode(), hashlib.sha256).hexdigest()


def _iguales(enviado: str, esperado: str) -> bool:
    """En bytes: `compare_digest` con un str que no sea ASCII lanza TypeError, y eso lo manda el cliente."""
    return hmac.compare_digest(enviado.encode("utf-8", "replace"), esperado.encode())


def nueva_sesion(sujeto_externo: str, proveedor: str) -> Sesion:
    return Sesion(sujeto_externo=sujeto_externo, consorcio_id=None, proveedor=proveedor,
                  nonce=secrets.token_hex(16), emitida=int(time.time()))


def firmar_sesion(sesion: Sesion) -> str:
    cuerpo = base64.urlsafe_b64encode(json.dumps(asdict(sesion)).encode()).decode()
    return f"{cuerpo}.{_firma(cuerpo)}"


def leer_sesion(token: str) -> Sesion | None:
    """None si la firma no coincide, el contenido no es una sesión o venció."""
    cuerpo, _, firma = token.partition(".")
    if not _iguales(firma, _firma(cuerpo)):
        return None
    try:
        sesion = Sesion(**json.loads(base64.urlsafe_b64decode(cuerpo)))
    except (binascii.Error, ValueError, TypeError):
        return None
    edad = time.time() - sesion.emitida
    return sesion if -_TOLERANCIA_RELOJ <= edad <= config.web_sesion_horas * 3600 else None


def token_csrf(sesion: Sesion) -> str:
    return _firma(f"csrf:{sesion.nonce}")


def token_csrf_previo(nonce: str) -> str:
    return _firma(f"previo:{nonce}")


def _poner(respuesta: Response, nombre: str, valor: str, segundos: int) -> None:
    respuesta.set_cookie(nombre, valor, max_age=segundos, path="/", httponly=True, samesite="lax",
                         secure=not config.web_selector_usuarios)


def poner_cookie_sesion(respuesta: Response, sesion: Sesion) -> None:
    """La vida de la cookie cuenta desde `emitida`: cambiar de consorcio no alarga la sesión."""
    restante = sesion.emitida + config.web_sesion_horas * 3600 - int(time.time())
    _poner(respuesta, NOMBRE_COOKIE, firmar_sesion(sesion), max(restante, 0))


def poner_cookie_previa(respuesta: Response, nonce: str) -> None:
    _poner(respuesta, COOKIE_PREVIA, nonce, _DURACION_PREVIA)


def usuario_activo(sujeto_externo: str) -> int | None:
    """El id del usuario si existe y sigue activo. Como la API: dar de baja corta la sesión en el pedido siguiente."""
    with Session(engine()) as s:
        return s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == sujeto_externo, Usuario.activo.is_(True)))


def sesion_de_cookie(request: Request) -> Sesion | None:
    """La sesión firmada y vigente, sin mirar la base. Una cookie `selector` vieja no sobrevive a apagar el selector."""
    token = request.cookies.get(NOMBRE_COOKIE)
    sesion = leer_sesion(token) if token else None
    if sesion is None or (sesion.proveedor == "selector" and not config.web_selector_usuarios):
        return None
    return sesion


def sesion_actual(request: Request) -> Sesion:
    """401 (y de ahí a `/entrar`) si la cookie no vale o el usuario ya no existe o está dado de baja."""
    sesion = sesion_de_cookie(request)
    usuario_id = usuario_activo(sesion.sujeto_externo) if sesion else None
    if sesion is None or usuario_id is None:
        raise HTTPException(status_code=401)
    registrar_actividad(usuario_id)  # por usuario y no por identidad: quien entra solo al CRM no tiene membresías
    return sesion


SesionActual = Annotated[Sesion, Depends(sesion_actual)]


class ElegirConsorcio(Exception):
    """Con sesión y sin consorcio elegido: no es falta de permiso, falta elegir. La app lo manda a `/consorcios`."""


def identidad_actual(sesion: SesionActual) -> Identidad:
    """Sin consorcio elegido, a elegirlo; con uno elegido sin membresía, 403."""
    if not sesion.consorcio_id:
        raise ElegirConsorcio
    try:
        return resolver_identidad(sesion.sujeto_externo, sesion.consorcio_id)
    except SinMembresia as e:
        raise HTTPException(status_code=403) from e


def exigir_administrador(identidad: Annotated[Identidad, Depends(identidad_actual)]) -> Identidad:
    if not identidad.es_administracion:
        raise HTTPException(status_code=403)
    return identidad


def exigir_plataforma(sesion: SesionActual) -> Sesion:
    """404 y no 403: quien no es del equipo no se entera de que la sección existe."""
    if not es_plataforma(sesion.sujeto_externo):
        raise HTTPException(status_code=404)
    return sesion


async def verificar_csrf(request: Request, sesion: SesionActual) -> None:
    enviado = request.headers.get("X-CSRF")
    if enviado is None:
        enviado = (await request.form()).get("csrf")
    if not isinstance(enviado, str) or not _iguales(enviado, token_csrf(sesion)):
        raise HTTPException(status_code=403)


async def verificar_csrf_previo(request: Request) -> None:
    """El CSRF de `POST /entrar`: el token del formulario tiene que coincidir con la cookie previa."""
    nonce = request.cookies.get(COOKIE_PREVIA)
    enviado = (await request.form()).get("csrf")
    if not nonce or not isinstance(enviado, str) or not _iguales(enviado, token_csrf_previo(nonce)):
        raise HTTPException(status_code=403)
