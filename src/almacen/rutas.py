"""Subir y listar PDF de un consorcio. Sube la administración; ve también el consejo."""
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from almacen.pdfs import PdfInvalido, cliente_bucket, guardar_pdf
from auth.db import obtener_conexion
from auth.permisos import exigir_rol, usuario_actual

router = APIRouter(prefix="/archivos", tags=["archivos"])


def obtener_bucket():
    return cliente_bucket()


@router.post("", status_code=status.HTTP_201_CREATED)
async def subir(consorcio_id: int = Form(...), archivo: UploadFile = File(...),
                usuario=Depends(usuario_actual), con=Depends(obtener_conexion),
                s3=Depends(obtener_bucket)):
    exigir_rol(usuario, consorcio_id, {"administracion"})
    try:
        r = guardar_pdf(con, s3, consorcio_id, archivo.filename or "sin-nombre.pdf",
                        await archivo.read(), usuario["id"])
    except PdfInvalido as e:
        raise HTTPException(422, str(e))
    return {"id": r.id, "duplicado": r.duplicado}


@router.get("")
def listar(consorcio_id: int, usuario=Depends(usuario_actual), con=Depends(obtener_conexion)):
    exigir_rol(usuario, consorcio_id, {"administracion", "consejo"})
    return con.execute(
        """SELECT id, nombre, tamano_bytes, subido_en FROM archivos
           WHERE consorcio_id = %s ORDER BY subido_en DESC""",
        (consorcio_id,),
    ).fetchall()
