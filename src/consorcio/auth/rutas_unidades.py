"""Unidades de un consorcio: la administración las carga, administración y consejo las ven."""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from .alta import MAX_PISO_DEPTO, MAX_UF, cargar_unidades
from .db import obtener_conexion
from .permisos import exigir_rol, usuario_actual

router = APIRouter(prefix="/auth/consorcios", tags=["consorcios"])


class Unidad(BaseModel):
    piso: str = Field(min_length=1, max_length=MAX_PISO_DEPTO)
    depto: str = Field(min_length=1, max_length=MAX_PISO_DEPTO)
    uf: int | None = Field(default=None, gt=0, le=MAX_UF)  # número de UF en la liquidación: con eso el agente busca sus expensas
    porcentual: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)


@router.post("/{consorcio_id}/unidades", status_code=status.HTTP_201_CREATED)
def cargar(consorcio_id: str, unidades: list[Unidad], usuario=Depends(usuario_actual),
           con=Depends(obtener_conexion)):
    exigir_rol(usuario, consorcio_id, {"administracion"})
    try:
        n = cargar_unidades(con, consorcio_id, [u.model_dump() for u in unidades])
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"cargadas": n}


@router.get("/{consorcio_id}/unidades")
def listar(consorcio_id: str, usuario=Depends(usuario_actual), con=Depends(obtener_conexion)):
    exigir_rol(usuario, consorcio_id, {"administracion", "consejo"})
    return con.execute(
        """SELECT id, piso, depto, uf, porcentual FROM unidades_funcionales
           WHERE consorcio_id = %s ORDER BY piso, depto""",
        (consorcio_id,),
    ).fetchall()
