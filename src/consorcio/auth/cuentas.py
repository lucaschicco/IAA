"""Login y registro, sin HTTP: lo usan la API JSON (`rutas.py`) y la web (`web/autenticacion.py`).

Cada función recibe una conexión de psycopg abierta y no confirma la transacción, salvo `autenticar` cuando el
intento falla: ese intento tiene que quedar registrado aunque después se responda con un error.
"""
from datetime import UTC, datetime, timedelta

from . import config
from .seguridad import hash_token, hashear_contrasena, nuevo_token_invitacion, verificar_contrasena


class Bloqueado(Exception):
    """Demasiados intentos fallidos con ese mail en la ventana de bloqueo."""


class CredencialesInvalidas(Exception):
    """Mail o contraseña incorrectos, o usuario dado de baja: siempre el mismo error."""


class InvitacionInvalida(Exception):
    """Invitación inexistente, usada o vencida."""


class CuentaExistente(Exception):
    """El mail de la invitación ya tiene cuenta y la contraseña no es la suya."""


class PedidoInvalido(ValueError):
    """La invitación no es coherente: rol sin unidad, unidad de otro consorcio, contraseña corta."""


def autenticar(con, email: str, contrasena: str, ip: str | None = None) -> dict:
    """Devuelve `{id, sujeto_externo}` del usuario. Registra el intento en `accesos`."""
    email = email.strip().lower()
    fallidos = con.execute(
        """SELECT count(*) AS n FROM accesos WHERE lower(email) = %s AND NOT exitoso
           AND momento > now() - make_interval(mins => %s)""",
        (email, config.MINUTOS_BLOQUEO),
    ).fetchone()["n"]
    if fallidos >= config.MAX_INTENTOS_FALLIDOS:
        raise Bloqueado(email)

    usuario = con.execute(
        "SELECT id, sujeto_externo, hash_contrasena, activo FROM usuarios WHERE lower(email) = %s", (email,)
    ).fetchone()
    contrasena_ok = verificar_contrasena(usuario["hash_contrasena"] if usuario else None, contrasena)
    ok = contrasena_ok and usuario is not None and usuario["activo"]

    con.execute("INSERT INTO accesos (usuario_id, email, exitoso, ip) VALUES (%s, %s, %s, %s)",
                (usuario["id"] if usuario else None, email, ok, ip))
    if not ok:
        con.commit()  # el intento fallido queda registrado aunque respondamos error
        raise CredencialesInvalidas(email)

    con.execute("UPDATE usuarios SET ultimo_login = now() WHERE id = %s", (usuario["id"],))
    return {"id": usuario["id"], "sujeto_externo": usuario["sujeto_externo"]}


def crear_invitacion(con, consorcio_id: str, email: str, rol: str, unidad_id: int | None,
                     creada_por: int | None) -> tuple[str, datetime]:
    """Devuelve el token (que va en el link, nunca a la base) y su vencimiento."""
    if rol in ("propietario", "inquilino") and unidad_id is None:
        raise PedidoInvalido("Propietarios e inquilinos necesitan una unidad")
    if rol == "administracion" and unidad_id is not None:
        raise PedidoInvalido("La administración no tiene unidad")
    if unidad_id is not None:
        unidad = con.execute("SELECT 1 FROM unidades_funcionales WHERE id = %s AND consorcio_id = %s",
                             (unidad_id, consorcio_id)).fetchone()
        if unidad is None:
            raise PedidoInvalido("La unidad no pertenece a ese consorcio")

    token = nuevo_token_invitacion()
    vence_en = datetime.now(UTC) + timedelta(days=config.DIAS_INVITACION)
    con.execute(
        """INSERT INTO invitaciones (consorcio_id, unidad_id, email, rol, token_hash, creada_por, vence_en)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (consorcio_id, unidad_id, email.strip().lower(), rol, hash_token(token), creada_por, vence_en),
    )
    return token, vence_en


def invitacion_vigente(con, token: str) -> dict | None:
    """La invitación de ese token si se puede usar, sin tomarla: para mostrar el formulario de registro."""
    return con.execute(
        """SELECT i.email, i.rol, c.nombre AS consorcio
           FROM invitaciones i JOIN consorcios c ON c.id = i.consorcio_id
           WHERE i.token_hash = %s AND i.usada_en IS NULL AND i.vence_en > now()""",
        (hash_token(token),),
    ).fetchone()


def aceptar_invitacion(con, token: str, nombre: str, contrasena: str) -> dict:
    """Acepta una invitación. Si el mail ya tiene cuenta (por ejemplo, alguien con departamentos en dos
    edificios), pide su contraseña actual y suma la membresía. Devuelve `{id, sujeto_externo}`."""
    invitacion = con.execute(
        """SELECT * FROM invitaciones WHERE token_hash = %s
           AND usada_en IS NULL AND vence_en > now() FOR UPDATE""",
        (hash_token(token),),
    ).fetchone()
    if invitacion is None:
        raise InvitacionInvalida()

    usuario = con.execute("SELECT id, sujeto_externo, hash_contrasena FROM usuarios WHERE lower(email) = %s",
                          (invitacion["email"],)).fetchone()
    if usuario is None:
        if len(contrasena) < config.LARGO_MINIMO_CONTRASENA:
            raise PedidoInvalido(
                f"La contraseña tiene que tener al menos {config.LARGO_MINIMO_CONTRASENA} caracteres")
        # `sujeto_externo` es lo único que ve el resto del sistema: opaco, no cambia si cambia el mail
        usuario = con.execute(
            """INSERT INTO usuarios (sujeto_externo, email, nombre, hash_contrasena)
               VALUES ('login:' || gen_random_uuid(), %s, %s, %s) RETURNING id, sujeto_externo""",
            (invitacion["email"], nombre.strip(), hashear_contrasena(contrasena)),
        ).fetchone()
    elif not verificar_contrasena(usuario["hash_contrasena"], contrasena):
        raise CuentaExistente()

    con.execute(
        """INSERT INTO membresias (usuario_id, consorcio_id, unidad_id, rol)
           VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
        (usuario["id"], invitacion["consorcio_id"], invitacion["unidad_id"], invitacion["rol"]),
    )
    con.execute("UPDATE invitaciones SET usada_en = now() WHERE id = %s", (invitacion["id"],))
    return {"id": usuario["id"], "sujeto_externo": usuario["sujeto_externo"]}
