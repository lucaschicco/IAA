"""Unidades de un consorcio: la administración las carga, administración y consejo las ven."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from auth.alta import cargar_unidades
from auth.db import obtener_conexion
from auth.permisos import exigir_rol, usuario_actual

router = APIRouter(prefix="/auth/consorcios", tags=["consorcios"])


class Unidad(BaseModel):
    piso: str = Field(min_length=1)
    depto: str = Field(min_length=1)
    porcentual: float | None = None


@router.post("/{consorcio_id}/unidades", status_code=status.HTTP_201_CREATED)
def cargar(consorcio_id: int, unidades: list[Unidad], usuario=Depends(usuario_actual),
           con=Depends(obtener_conexion)):
    exigir_rol(usuario, consorcio_id, {"administracion"})
    try:
        n = cargar_unidades(con, consorcio_id, [u.model_dump() for u in unidades])
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"cargadas": n}


@router.get("/{consorcio_id}/unidades")
def listar(consorcio_id: int, usuario=Depends(usuario_actual), con=Depends(obtener_conexion)):
    exigir_rol(usuario, consorcio_id, {"administracion", "consejo"})
    return con.execute(
        "SELECT id, piso, depto, porcentual FROM unidades WHERE consorcio_id = %s ORDER BY piso, depto",
        (consorcio_id,),
    ).fetchall()
