"""Crudos: el PDF original de cada documento, en un bucket compatible con S3 (SeaweedFS en Compose).

La clave es `<consorcio>/<sha256>.pdf`: el mismo PDF en dos consorcios son dos objetos, igual que
son dos documentos. Sin `S3_ENDPOINT_URL` la ingesta sigue solo en local, con un warning; con endpoint,
cualquier fallo lanza `CrudoNoDisponible` y frena la ingesta, salvo `borrar_crudo`, que es best effort.

El cliente se crea en cada operación: no hay caché que sobreviva a un cambio de configuración, y una
sesión propia evita compartir la sesión global de boto3, que no es segura entre hilos.
"""
from __future__ import annotations

import logging

from ..config import config

log = logging.getLogger("consorcio.crudos")

_REGION = "us-east-1"  # SeaweedFS no usa región, pero la firma de S3 necesita una
_CODIGOS_SIN_BUCKET = {"404", "NoSuchBucket", "NotFound"}
_CODIGOS_BUCKET_YA_CREADO = {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}


class CrudoNoDisponible(RuntimeError):
    """Hay un bucket configurado y no se pudo usar."""


def configurado() -> bool:
    return bool(config.s3_endpoint_url)


def clave_crudo(consorcio_id: str, sha256: str) -> str:
    return f"{consorcio_id}/{sha256}.pdf"


def _cliente():
    import boto3
    from botocore.config import Config

    return boto3.session.Session().client(
        "s3",
        endpoint_url=config.s3_endpoint_url,
        aws_access_key_id=config.s3_access_key or None,
        aws_secret_access_key=config.s3_secret_key or None,
        region_name=_REGION,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=3,
            read_timeout=30,
            retries={"total_max_attempts": 2, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def verificar_bucket() -> None:
    """Comprueba que el bucket responde y lo crea si falta. Sin endpoint no hace nada."""
    if not configurado():
        return
    from botocore.exceptions import BotoCoreError, ClientError

    bucket = config.s3_bucket
    try:
        s3 = _cliente()
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") not in _CODIGOS_SIN_BUCKET:
                raise
            _crear_bucket(s3, bucket)
    except (BotoCoreError, ClientError, OSError) as e:
        raise CrudoNoDisponible(f"bucket {bucket!r} no disponible: {type(e).__name__}: {e}") from e


def _crear_bucket(s3, bucket: str) -> None:
    from botocore.exceptions import ClientError

    try:
        s3.create_bucket(Bucket=bucket)
        log.info("bucket %s creado", bucket)
    except ClientError as e:  # otra ingesta lo creó entre el chequeo y este pedido
        if e.response.get("Error", {}).get("Code") not in _CODIGOS_BUCKET_YA_CREADO:
            raise


def subir_crudo(ruta: str, consorcio_id: str, sha256: str) -> str | None:
    """Sube el PDF y devuelve su clave. Sin endpoint devuelve None. Subirlo de nuevo es idempotente:
    la clave depende del contenido."""
    if not configurado():
        log.warning("sin S3_ENDPOINT_URL: el crudo de %s no se sube", sha256[:16])
        return None
    from botocore.exceptions import BotoCoreError, ClientError

    clave = clave_crudo(consorcio_id, sha256)
    try:
        with open(ruta, "rb") as f:
            _cliente().put_object(Bucket=config.s3_bucket, Key=clave, Body=f, ContentType="application/pdf")
    except (BotoCoreError, ClientError, OSError) as e:
        raise CrudoNoDisponible(f"no se pudo subir {clave}: {type(e).__name__}: {e}") from e
    return clave


def borrar_crudo(clave: str) -> bool:
    """Borra un objeto, best effort: lo que importa ya quedó en Postgres, así que un fallo solo deja un
    objeto huérfano y un warning. Devuelve si se pudo borrar."""
    if not configurado() or not clave:
        return False
    try:
        _cliente().delete_object(Bucket=config.s3_bucket, Key=clave)
        return True
    except Exception as e:
        log.warning("no se pudo borrar el crudo %s: %s: %s", clave, type(e).__name__, e)
        return False
