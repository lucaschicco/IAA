"""Contraseñas y tokens."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from auth import config

_hasher = PasswordHasher()
# Hash de una contraseña cualquiera. Se usa cuando el mail no existe, para que
# el login tarde lo mismo y no revele qué mails están registrados.
_HASH_FALSO = _hasher.hash("contrasena-que-no-existe")


def hashear_contrasena(contrasena: str) -> str:
    return _hasher.hash(contrasena)


def verificar_contrasena(hash_guardado: str | None, contrasena: str) -> bool:
    try:
        coincide = _hasher.verify(hash_guardado or _HASH_FALSO, contrasena)
    except (VerificationError, InvalidHashError):
        return False
    return coincide and hash_guardado is not None


def _secreto() -> str:
    if len(config.JWT_SECRETO) < 32:
        raise RuntimeError("JWT_SECRETO no está configurado o es muy corto (mínimo 32 caracteres)")
    return config.JWT_SECRETO


def crear_token_sesion(usuario_id: int, horas: float = config.HORAS_TOKEN_SESION) -> str:
    """El token solo dice quién es el usuario. Los roles se leen de la base en
    cada pedido, así un cambio de rol o una baja aplica al instante."""
    ahora = datetime.now(timezone.utc)
    datos = {"sub": str(usuario_id), "iat": ahora, "exp": ahora + timedelta(hours=horas)}
    return jwt.encode(datos, _secreto(), algorithm="HS256")


def leer_token_sesion(token: str) -> int | None:
    """Devuelve el id del usuario, o None si el token es inválido o venció."""
    try:
        datos = jwt.decode(token, _secreto(), algorithms=["HS256"])
        return int(datos["sub"])
    except (jwt.InvalidTokenError, KeyError, ValueError):
        return None


def nuevo_token_invitacion() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """En la base se guarda el hash del link de invitación, nunca el link."""
    return hashlib.sha256(token.encode()).hexdigest()
