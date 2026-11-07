"""Guardar y leer PDF en el bucket.

Cualquier parte del sistema que reciba un PDF (la ingesta, la carga de facturas)
lo guarda con guardar_pdf(). Si el mismo contenido ya estaba cargado en ese
consorcio, no lo vuelve a guardar: devuelve el registro existente y avisa que
es un duplicado, así la ingesta no procesa dos veces el mismo documento.
"""
import hashlib
from dataclasses import dataclass

import boto3
from botocore.exceptions import ClientError

from almacen import config


class PdfInvalido(ValueError):
    pass


@dataclass
class ArchivoGuardado:
    id: int
    duplicado: bool       # True si ese contenido ya estaba cargado
    clave_bucket: str
    sha256: str


def cliente_bucket():
    return boto3.client("s3", endpoint_url=config.S3_ENDPOINT,
                        aws_access_key_id=config.S3_USUARIO,
                        aws_secret_access_key=config.S3_CLAVE,
                        region_name="us-east-1")


def asegurar_bucket(s3) -> None:
    try:
        s3.head_bucket(Bucket=config.S3_BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=config.S3_BUCKET)


def guardar_pdf(con, s3, consorcio_id: int, nombre: str, contenido: bytes,
                usuario_id: int | None = None) -> ArchivoGuardado:
    if not contenido.startswith(b"%PDF-"):
        raise PdfInvalido("El archivo no es un PDF")
    if len(contenido) > config.MAX_MB * 1024 * 1024:
        raise PdfInvalido(f"El PDF supera los {config.MAX_MB} MB")

    sha = hashlib.sha256(contenido).hexdigest()
    existente = con.execute(
        "SELECT id, clave_bucket FROM archivos WHERE consorcio_id = %s AND sha256 = %s",
        (consorcio_id, sha),
    ).fetchone()
    if existente:
        return ArchivoGuardado(existente["id"], True, existente["clave_bucket"], sha)

    clave = f"consorcio-{consorcio_id}/{sha}.pdf"
    asegurar_bucket(s3)
    s3.put_object(Bucket=config.S3_BUCKET, Key=clave, Body=contenido,
                  ContentType="application/pdf")
    fila = con.execute(
        """INSERT INTO archivos (consorcio_id, nombre, sha256, tamano_bytes, clave_bucket, subido_por)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (consorcio_id, nombre, sha, len(contenido), clave, usuario_id),
    ).fetchone()
    return ArchivoGuardado(fila["id"], False, clave, sha)


def leer_pdf(con, s3, archivo_id: int, consorcio_id: int) -> bytes:
    """Pide el consorcio además del id, para que nadie lea un PDF de otro edificio."""
    fila = con.execute("SELECT clave_bucket FROM archivos WHERE id = %s AND consorcio_id = %s",
                       (archivo_id, consorcio_id)).fetchone()
    if fila is None:
        raise FileNotFoundError(archivo_id)
    return s3.get_object(Bucket=config.S3_BUCKET, Key=fila["clave_bucket"])["Body"].read()
