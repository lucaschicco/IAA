"""Punto de entrada. Para correrlo: PYTHONPATH=src uvicorn auth.app:app --reload
y abrir http://localhost:8000/docs para probar cada función desde el navegador."""
from fastapi import FastAPI

from auth.rutas import router
from auth.rutas_unidades import router as router_unidades
from almacen.rutas import router as router_archivos

app = FastAPI(title="Consorcios")
app.include_router(router)
app.include_router(router_unidades)
app.include_router(router_archivos)


@app.get("/salud")
def salud():
    return {"ok": True}
