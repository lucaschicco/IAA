"""Rutas del CRM: solo el equipo de plataforma. El resto recibe 404, igual que si la sección no existiera."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.datastructures import URL

from .. import crm
from .rutas import pagina
from .sesion import Sesion, exigir_plataforma

router = APIRouter()


def _administracion(valor: str | None) -> int | str | None:
    """`""` o ausente: todas; `sin`: los consorcios sin Administración; un id: esa. Otra cosa da 422 (la página de
    error, con menú) y un id que no existe, 404, como un consorcio inexistente."""
    if not valor:
        return None
    if valor == crm.SIN_ADMINISTRACION:
        return valor
    if not (valor.isascii() and valor.isdigit()) or len(valor) > 9:
        raise HTTPException(status_code=422)
    if int(valor) not in {a["id"] for a in crm.administraciones()}:
        raise HTTPException(status_code=404)
    return int(valor)


@router.get("/crm", response_class=HTMLResponse)
def ver_crm(
    request: Request,
    sesion: Annotated[Sesion, Depends(exigir_plataforma)],
    dias: Literal["7", "30", "90"] = "30",  # como texto: un Literal de enteros rechaza todo lo que llega por query
    consorcio: str | None = None,
    vista: Literal["resumen", "calidad", "uso"] = "resumen",
    administracion: Annotated[str | None, Query(max_length=20)] = None,
):
    elegida = _administracion(administracion)
    if consorcio and consorcio not in crm.consorcios():
        raise HTTPException(status_code=404)
    consorcios = crm.consorcios(elegida)
    if consorcio and consorcio not in consorcios:
        # Consorcio de otra Administración: pasa al cambiar la Administración en el formulario sin tocar el consorcio.
        # Se lo saca y la URL dice lo que se mira: un cruce vacío no se muestra como si fuera un dato.
        destino = URL("/crm").include_query_params(vista=vista, dias=dias, consorcio="", administracion=administracion)
        return RedirectResponse(str(destino), status_code=303)
    hasta = datetime.now(UTC)
    # Posicional a propósito: los tests reemplazan `crm.panel` por una función que recibe `*args`
    datos = crm.panel(hasta - timedelta(days=int(dias)), hasta, consorcio or None, elegida)
    # `filtro` y no `consorcio`: ese nombre es el del consorcio de la sesión en el encabezado de `base.html`
    return pagina(request, "crm.html", sesion, panel=datos, dias=dias, filtro=consorcio or "", consorcios=consorcios,
                  administraciones=crm.administraciones(), filtro_administracion=administracion or "",
                  sin_administracion=crm.SIN_ADMINISTRACION, hoy=hasta.strftime("%d/%m/%Y"), vista=vista)
