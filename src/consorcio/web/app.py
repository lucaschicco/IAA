"""Aplicación FastAPI. Modo desarrollo = `WEB_SELECTOR_USUARIOS=true`: sin eso, exige `WEB_SECRETO`."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from ..config import config
from . import crm_rutas, rutas
from .sesion import MIN_LARGO_SECRETO, secreto_debil

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
}


class Cabeceras:
    """ASGI puro: también cubre los estáticos y las respuestas de error."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        async def enviar(mensaje) -> None:
            if mensaje["type"] == "http.response.start":
                mensaje["headers"] = [*mensaje.get("headers", []), *CABECERAS]
            await send(mensaje)

        await self.app(scope, receive, enviar if scope["type"] == "http" else send)


def manejar_error(request: Request, exc: HTTPException) -> Response:
    """Con selector, sin sesión se va a `/entrar`. El resto es una página, o un fragmento si lo pidió htmx,
    siempre la misma por código: un 404 de una ruta oculta no se distingue del de una que no existe."""
    htmx = "HX-Request" in request.headers
    if exc.status_code == 401 and config.web_selector_usuarios:
        if htmx:
            return Response(status_code=401, headers={"HX-Redirect": "/entrar"})
        return RedirectResponse("/entrar", status_code=303)
    mensaje = MENSAJES_ERROR.get(exc.status_code, "No se pudo completar el pedido.")
    return rutas.plantillas.TemplateResponse(
        request, "_error.html" if htmx else "error.html", {"mensaje": mensaje},
        status_code=exc.status_code, headers=exc.headers,
    )


def crear_app() -> FastAPI:
    if not config.web_secreto:
        if not config.web_selector_usuarios:
            raise RuntimeError("Falta WEB_SECRETO: sin selector de usuarios no se pueden firmar las sesiones.")
        log.warning("sin WEB_SECRETO: el secreto es aleatorio y las sesiones no sobreviven a este proceso")
    elif secreto_debil(config.web_secreto):  # también con el selector: un secreto débil nunca se usa
        raise RuntimeError(f"WEB_SECRETO demasiado débil: generarlo aleatorio, de al menos {MIN_LARGO_SECRETO} caracteres.")
    app = FastAPI(title="Consorcio", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(Cabeceras)
    app.add_exception_handler(HTTPException, manejar_error)
    app.include_router(rutas.router)
    app.include_router(crm_rutas.router)
    app.mount("/estaticos", StaticFiles(directory=DIR_ESTATICOS), name="estaticos")
    return app
