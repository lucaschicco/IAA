"""Configuración del login. Los valores de entorno salen de `consorcio.config` (el `.env` del proyecto)."""
from ..config import config

HORAS_TOKEN_SESION = 8          # cuánto dura un token de la API
DIAS_INVITACION = 7             # cuánto dura un link de invitación
MAX_INTENTOS_FALLIDOS = 5       # intentos de login fallidos permitidos...
MINUTOS_BLOQUEO = 15            # ...dentro de esta ventana
LARGO_MINIMO_CONTRASENA = 10


def database_url() -> str:
    """La misma base que el resto del sistema, en el formato de psycopg (sin el `+psycopg` de SQLAlchemy)."""
    return config.postgres_url.replace("postgresql+psycopg://", "postgresql://", 1)


def jwt_secreto() -> str:
    return config.jwt_secreto
