"""Configuración del bucket de PDF. En local es MinIO (compatible con S3)."""
import os

S3_ENDPOINT = os.environ.get("S3_ENDPOINT", "http://localhost:9000")
S3_USUARIO = os.environ.get("S3_USUARIO", "minio")
S3_CLAVE = os.environ.get("S3_CLAVE", "minio-clave-local")
S3_BUCKET = os.environ.get("S3_BUCKET", "pdfs")
MAX_MB = 25
