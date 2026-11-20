"""API JSON del login, aparte de la web. Para correrla: `make api`, y abrir http://localhost:8002/docs
para probar cada función desde el navegador. La web (`make web`) usa la misma lógica desde `cuentas.py`."""
import logging

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .rutas import router
from .rutas_unidades import router as router_unidades

log = logging.getLogger("consorcio.auth")

app = FastAPI(title="Consorcios: login")


@app.exception_handler(psycopg.DataError)
def dato_invalido(request: Request, exc: psycopg.DataError) -> JSONResponse:
    """Un valor que la base no acepta (fuera de rango, NUL) es un pedido mal armado y no un error del servidor.
    Lo normal es que lo corte la validación de antes; esto queda de red."""
    log.warning("dato rechazado por la base en %s: %s", request.url.path, type(exc).__name__)
    return JSONResponse({"detail": "Algún dato está fuera de rango o no es válido."}, status_code=422)
app.include_router(router)
app.include_router(router_unidades)


@app.get("/salud")
def salud():
    return {"ok": True}
