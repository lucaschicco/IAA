"""Aplicación FastAPI. Modo desarrollo = `WEB_SELECTOR_USUARIOS=true`: sin eso, exige `WEB_SECRETO`."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from ..config import config
from ..identidad import SinMembresia, resolver_identidad
from . import crm_rutas, rutas
from .sesion import (
    MIN_LARGO_SECRETO,
    ElegirConsorcio,
    secreto_debil,
    sesion_de_cookie,
    token_csrf,
    usuario_activo,
)

log = logging.getLogger("consorcio.web")

DIR_ESTATICOS = Path(__file__).resolve().parent / "estaticos"

CABECERAS = [
    (b"content-security-policy", b"default-src 'self'"),
    (b"x-content-type-options", b"nosniff"),
]
MENSAJES_ERROR = {
    401: "Hay que iniciar sesión.",
    403: "No tenés acceso a esta sección.",
    404: "No encontramos lo que buscás.",
    409: "Esa solicitud ya estaba resuelta.",
    411: "El pedido tiene que indicar su tamaño.",
    413: "El archivo supera el tamaño que acepta la carga.",
    415: "El archivo no es un PDF.",
    422: "Faltan datos o no son válidos.",
    500: "Algo falló de nuestro lado. Probá de nuevo en un rato.",
}


class Cabeceras:
    """ASGI puro: también cubre los estáticos y las respuestas de error."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        async def enviar(mensaje) -> None:
            if mensaje["type"] == "http.response.start":
                extra = CABECERAS
                # Los estáticos se revalidan siempre (ETag): sin esto el navegador sigue con el CSS viejo tras un cambio
                if scope.get("path", "").startswith("/estaticos/"):
                    extra = [*CABECERAS, (b"cache-control", b"no-cache")]
                mensaje["headers"] = [*mensaje.get("headers", []), *extra]
            await send(mensaje)

        await self.app(scope, receive, enviar if scope["type"] == "http" else send)


def _contexto_error(request: Request) -> dict:
    """Con sesión, el armazón con menú: la misma sesión y el mismo menú que daría cualquier pantalla.
    Sin la consulta a la base (si es ella la que falló) queda el menú mínimo, que igual tiene salida."""
    sesion = sesion_de_cookie(request)
    if sesion is None:
        return {}
    try:
        if usuario_activo(sesion.sujeto_externo) is None:
            return {}
        identidad = None
        if sesion.consorcio_id:
            try:
                identidad = resolver_identidad(sesion.sujeto_externo, sesion.consorcio_id)
            except SinMembresia:
                identidad = None
        return rutas.contexto_menu(sesion, identidad)
    except Exception:
        log.exception("no se pudo armar el menú de la página de error")
        return {"sesion": sesion, "csrf": token_csrf(sesion)}


def pagina_error(request: Request, status_code: int, headers: dict | None = None) -> Response:
    """Siempre la misma por código: un 404 de una ruta oculta no se distingue del de una que no existe."""
    mensaje = MENSAJES_ERROR.get(status_code, "No se pudo completar el pedido.")
    if "HX-Request" in request.headers:
        return rutas.plantillas.TemplateResponse(request, "_error.html", {"mensaje": mensaje},
                                                 status_code=status_code, headers=headers)
    return rutas.plantillas.TemplateResponse(request, "error.html", {**_contexto_error(request), "mensaje": mensaje},
                                             status_code=status_code, headers=headers)


def manejar_error(request: Request, exc: HTTPException) -> Response:
    """Sin sesión se va a `/entrar`. El resto es una página, o un fragmento si lo pidió htmx."""
    if exc.status_code == 401:
        if "HX-Request" in request.headers:
            return Response(status_code=401, headers={"HX-Redirect": "/entrar"})
        return RedirectResponse("/entrar", status_code=303)
    return pagina_error(request, exc.status_code, exc.headers)


def manejar_validacion(request: Request, exc: RequestValidationError) -> Response:
    """Un parámetro de la URL con un valor que no corresponde: la página de error y no el JSON de FastAPI."""
    return pagina_error(request, 422)


def manejar_elegir_consorcio(request: Request, exc: ElegirConsorcio) -> Response:
    """Con sesión y sin consorcio elegido, a elegirlo (un marcador o un link profundo después de entrar)."""
    if "HX-Request" in request.headers:
        return Response(status_code=204, headers={"HX-Redirect": "/consorcios"})
    return RedirectResponse("/consorcios", status_code=303)


class ErrorInesperado:
    """ASGI: una excepción que no es HTTP da la página de error 500, sin traceback para el usuario; el detalle va
    al log. Va dentro de `Cabeceras`, así que la página sale con la misma CSP que el resto."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        empezada = False

        async def enviar(mensaje) -> None:
            nonlocal empezada
            if mensaje["type"] == "http.response.start":
                empezada = True
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        except Exception:
            log.exception("error inesperado en %s %s", scope.get("method"), scope.get("path"))
            if empezada:
                raise
            await pagina_error(Request(scope), 500)(scope, receive, send)


def crear_app() -> FastAPI:
    if not config.web_secreto:
        if not config.web_selector_usuarios:
            raise RuntimeError("Falta WEB_SECRETO: sin selector de usuarios no se pueden firmar las sesiones.")
        log.warning("sin WEB_SECRETO: el secreto es aleatorio y las sesiones no sobreviven a este proceso")
    elif secreto_debil(config.web_secreto):  # también con el selector: un secreto débil nunca se usa
        raise RuntimeError(f"WEB_SECRETO demasiado débil: generarlo aleatorio, de al menos {MIN_LARGO_SECRETO} caracteres.")
    app = FastAPI(title="Consorcio", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(ErrorInesperado)
    app.add_middleware(Cabeceras)  # la última que se agrega es la de afuera
    app.add_exception_handler(HTTPException, manejar_error)
    app.add_exception_handler(RequestValidationError, manejar_validacion)
    app.add_exception_handler(ElegirConsorcio, manejar_elegir_consorcio)
    app.include_router(rutas.router)
    app.include_router(crm_rutas.router)
    app.mount("/estaticos", StaticFiles(directory=DIR_ESTATICOS), name="estaticos")
    return app
