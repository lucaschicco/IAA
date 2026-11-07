"""Alta de un consorcio nuevo, desde la terminal.

Resuelve el primer paso: para invitar vecinos hace falta ser administración,
pero alguien tiene que crear el consorcio y su primer administrador.

Uso:
    PYTHONPATH=src python -m auth.alta consorcio --nombre "Mercedes 274" \\
        --direccion "Mercedes 274, CABA" --admin-email admin@estudio.com
    PYTHONPATH=src python -m auth.alta unidades --consorcio-id 1 --csv datos/unidades/unidades.csv

El primer comando no pide contraseña: crea una invitación para el administrador,
que entra con ese link y elige su contraseña en /auth/registro.
"""
import argparse
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from auth import config
from auth.db import conectar
from auth.seguridad import hash_token, nuevo_token_invitacion


def crear_consorcio(con, nombre: str, direccion: str, admin_email: str) -> tuple[int, str]:
    """Crea el consorcio y la invitación para su administrador.
    Devuelve el id del consorcio y el token de la invitación."""
    consorcio_id = con.execute(
        "INSERT INTO consorcios (nombre, direccion) VALUES (%s, %s) RETURNING id",
        (nombre.strip(), direccion.strip()),
    ).fetchone()["id"]
    token = nuevo_token_invitacion()
    vence_en = datetime.now(timezone.utc) + timedelta(days=config.DIAS_INVITACION)
    con.execute(
        """INSERT INTO invitaciones (consorcio_id, email, rol, token_hash, vence_en)
           VALUES (%s, %s, 'administracion', %s, %s)""",
        (consorcio_id, admin_email.strip().lower(), hash_token(token), vence_en),
    )
    return consorcio_id, token


def cargar_unidades(con, consorcio_id: int, unidades: list[dict]) -> int:
    """Carga o actualiza unidades. Si una unidad ya existe (mismo piso y depto),
    actualiza su porcentual, así el archivo se puede volver a cargar sin duplicar."""
    if con.execute("SELECT 1 FROM consorcios WHERE id = %s", (consorcio_id,)).fetchone() is None:
        raise ValueError(f"No existe el consorcio {consorcio_id}")
    for u in unidades:
        piso, depto = str(u["piso"]).strip(), str(u["depto"]).strip()
        if not piso or not depto:
            raise ValueError(f"Unidad sin piso o depto: {u}")
        porcentual = u.get("porcentual")
        if porcentual in ("", None):
            porcentual = None
        else:
            try:
                porcentual = Decimal(str(porcentual).replace(",", "."))
            except InvalidOperation:
                raise ValueError(f"Porcentual inválido en {piso}{depto}: {u['porcentual']}")
        con.execute(
            """INSERT INTO unidades (consorcio_id, piso, depto, porcentual)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (consorcio_id, piso, depto)
               DO UPDATE SET porcentual = EXCLUDED.porcentual""",
            (consorcio_id, piso, depto, porcentual),
        )
    return len(unidades)


def _leer_csv(ruta: str) -> list[dict]:
    """El CSV tiene columnas piso, depto y (opcional) porcentual."""
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    p = argparse.ArgumentParser(description="Alta de consorcios y unidades")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("consorcio", help="Crea un consorcio e invita a su administrador")
    c.add_argument("--nombre", required=True)
    c.add_argument("--direccion", required=True)
    c.add_argument("--admin-email", required=True)

    u = sub.add_parser("unidades", help="Carga las unidades de un consorcio desde un CSV")
    u.add_argument("--consorcio-id", type=int, required=True)
    u.add_argument("--csv", required=True)

    args = p.parse_args()
    with conectar() as con:
        if args.comando == "consorcio":
            consorcio_id, token = crear_consorcio(con, args.nombre, args.direccion, args.admin_email)
            print(f"Consorcio creado con id {consorcio_id}.")
            print("Invitación para el administrador (vence en "
                  f"{config.DIAS_INVITACION} días), usarla en /auth/registro:")
            print(token)
        else:
            n = cargar_unidades(con, args.consorcio_id, _leer_csv(args.csv))
            print(f"{n} unidades cargadas en el consorcio {args.consorcio_id}.")


if __name__ == "__main__":
    main()
