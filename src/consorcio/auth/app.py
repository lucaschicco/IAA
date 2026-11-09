"""API JSON del login, aparte de la web. Para correrla: `make api`, y abrir http://localhost:8001/docs
para probar cada función desde el navegador. La web (`make web`) usa la misma lógica desde `cuentas.py`."""
from fastapi import FastAPI

from .rutas import router
from .rutas_unidades import router as router_unidades

app = FastAPI(title="Consorcios: login")
app.include_router(router)
app.include_router(router_unidades)


@app.get("/salud")
def salud():
    return {"ok": True}
