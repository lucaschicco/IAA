"""Punto de entrada. Para correrlo: PYTHONPATH=src uvicorn auth.app:app --reload
y abrir http://localhost:8000/docs para probar cada función desde el navegador."""
from fastapi import FastAPI

from auth.rutas import router

app = FastAPI(title="Consorcios: autenticación y roles")
app.include_router(router)


@app.get("/salud")
def salud():
    return {"ok": True}
