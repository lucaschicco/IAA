"""Funciones HTTP: invitar, registrarse, login y quién soy."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from auth import config
from auth.db import obtener_conexion
from auth.permisos import exigir_rol, usuario_actual
from auth.seguridad import (crear_token_sesion, hash_token, hashear_contrasena,
                            nuevo_token_invitacion, verificar_contrasena)

router = APIRouter(prefix="/auth", tags=["autenticación"])

Rol = Literal["administracion", "consejo", "propietario", "inquilino"]


class PedidoInvitacion(BaseModel):
    consorcio_id: int
    email: EmailStr
    rol: Rol
    unidad_id: int | None = None


class PedidoRegistro(BaseModel):
    token: str
    nombre: str = Field(min_length=1)
    contrasena: str = Field(min_length=config.LARGO_MINIMO_CONTRASENA)


class PedidoLogin(BaseModel):
    email: EmailStr
    contrasena: str


class RespuestaToken(BaseModel):
    token: str
    tipo: str = "bearer"


@router.post("/invitaciones", status_code=status.HTTP_201_CREATED)
def crear_invitacion(pedido: PedidoInvitacion, usuario=Depends(usuario_actual),
                     con=Depends(obtener_conexion)):
    """Solo la administración del consorcio puede invitar."""
    exigir_rol(usuario, pedido.consorcio_id, {"administracion"})

    if pedido.rol in ("propietario", "inquilino") and pedido.unidad_id is None:
        raise HTTPException(422, "Propietarios e inquilinos necesitan una unidad")
    if pedido.rol == "administracion" and pedido.unidad_id is not None:
        raise HTTPException(422, "La administración no tiene unidad")
    if pedido.unidad_id is not None:
        unidad = con.execute("SELECT 1 FROM unidades WHERE id = %s AND consorcio_id = %s",
                             (pedido.unidad_id, pedido.consorcio_id)).fetchone()
        if unidad is None:
            raise HTTPException(422, "La unidad no pertenece a ese consorcio")

    token = nuevo_token_invitacion()
    vence_en = datetime.now(timezone.utc) + timedelta(days=config.DIAS_INVITACION)
    con.execute(
        """INSERT INTO invitaciones (consorcio_id, unidad_id, email, rol, token_hash, creada_por, vence_en)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (pedido.consorcio_id, pedido.unidad_id, pedido.email.lower(), pedido.rol,
         hash_token(token), usuario["id"], vence_en),
    )
    # Pendiente: mandar el link por mail. Por ahora se devuelve para probar.
    return {"token_invitacion": token, "vence_en": vence_en}


@router.post("/registro", response_model=RespuestaToken)
def registrarse(pedido: PedidoRegistro, con=Depends(obtener_conexion)):
    """Acepta una invitación. Si el mail ya tiene cuenta (por ejemplo, alguien con
    departamentos en dos edificios), pide su contraseña actual y suma la membresía."""
    invitacion = con.execute(
        """SELECT * FROM invitaciones WHERE token_hash = %s
           AND usada_en IS NULL AND vence_en > now() FOR UPDATE""",
        (hash_token(pedido.token),),
    ).fetchone()
    if invitacion is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitación inválida, usada o vencida")

    usuario = con.execute("SELECT id, hash_contrasena FROM usuarios WHERE lower(email) = %s",
                          (invitacion["email"],)).fetchone()
    if usuario is None:
        usuario = con.execute(
            "INSERT INTO usuarios (email, nombre, hash_contrasena) VALUES (%s, %s, %s) RETURNING id",
            (invitacion["email"], pedido.nombre, hashear_contrasena(pedido.contrasena)),
        ).fetchone()
    elif not verificar_contrasena(usuario["hash_contrasena"], pedido.contrasena):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "Ese mail ya tiene cuenta: usá tu contraseña actual")

    con.execute(
        """INSERT INTO membresias (usuario_id, consorcio_id, unidad_id, rol)
           VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (usuario["id"], invitacion["consorcio_id"], invitacion["unidad_id"], invitacion["rol"]),
    )
    con.execute("UPDATE invitaciones SET usada_en = now() WHERE id = %s", (invitacion["id"],))
    return RespuestaToken(token=crear_token_sesion(usuario["id"]))


@router.post("/login", response_model=RespuestaToken)
def login(pedido: PedidoLogin, request: Request, con=Depends(obtener_conexion)):
    email = pedido.email.lower()
    fallidos = con.execute(
        """SELECT count(*) AS n FROM accesos WHERE lower(email) = %s AND NOT exitoso
           AND momento > now() - make_interval(mins => %s)""",
        (email, config.MINUTOS_BLOQUEO),
    ).fetchone()["n"]
    if fallidos >= config.MAX_INTENTOS_FALLIDOS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Demasiados intentos. Probá de nuevo en unos minutos.")

    usuario = con.execute(
        "SELECT id, hash_contrasena, activo FROM usuarios WHERE lower(email) = %s", (email,)
    ).fetchone()
    contrasena_ok = verificar_contrasena(usuario["hash_contrasena"] if usuario else None,
                                         pedido.contrasena)
    ok = contrasena_ok and usuario is not None and usuario["activo"]

    ip = request.client.host if request.client else None
    con.execute("INSERT INTO accesos (usuario_id, email, exitoso, ip) VALUES (%s, %s, %s, %s)",
                (usuario["id"] if usuario else None, email, ok, ip))
    if not ok:
        con.commit()  # el intento fallido queda registrado aunque respondamos error
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Mail o contraseña incorrectos")

    con.execute("UPDATE usuarios SET ultimo_login = now() WHERE id = %s", (usuario["id"],))
    return RespuestaToken(token=crear_token_sesion(usuario["id"]))


@router.get("/yo")
def quien_soy(usuario=Depends(usuario_actual)):
    return usuario
