"""Rutas del CRM: solo el equipo de plataforma. El resto recibe 404, igual que si la sección no existiera."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import crm
from .rutas import pagina
from .sesion import Sesion, exigir_plataforma

router = APIRouter()


@router.get("/crm", response_class=HTMLResponse)
def ver_crm(
    request: Request,
    sesion: Annotated[Sesion, Depends(exigir_plataforma)],
    dias: Literal["7", "30", "90"] = "30",  # como texto: un Literal de enteros rechaza todo lo que llega por query
    consorcio: str | None = None,
):
    consorcios = crm.consorcios()
    if consorcio and consorcio not in consorcios:
        raise HTTPException(status_code=404)
    hasta = datetime.now(UTC)
    datos = crm.panel(hasta - timedelta(days=int(dias)), hasta, consorcio or None)
    # `filtro` y no `consorcio`: ese nombre es el del consorcio de la sesión en el encabezado de `base.html`
    return pagina(request, "crm.html", sesion, panel=datos, dias=dias, filtro=consorcio or "", consorcios=consorcios,
                  hoy=hasta.strftime("%d/%m/%Y"))
