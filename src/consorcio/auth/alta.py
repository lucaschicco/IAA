"""Alta de un consorcio nuevo, desde la terminal (`consorcio-alta` o `python -m consorcio.auth.alta`).

Resuelve el primer paso: para invitar vecinos hace falta ser administración,
pero alguien tiene que crear el consorcio y su primer administrador.

Uso:
    python -m consorcio.auth.alta consorcio --id mercedes-274 --nombre "Mercedes 274" \\
        --direccion "Mercedes 274, CABA" --admin-email admin@estudio.com
    python -m consorcio.auth.alta unidades --consorcio-id mercedes-274 --csv datos/unidades/unidades.csv

El primer comando no pide contraseña: crea una invitación para el administrador,
que entra con ese link y elige su contraseña en /registro (web) o /auth/registro (API).
Si el consorcio ya existe (por ejemplo, `mercedes-274`, con sus documentos ingestados), le suma la dirección
y la invitación sin tocar lo demás.
"""
import argparse
import csv
import re
from decimal import Decimal, InvalidOperation

from ..texto import normalizar
from . import config
from .cuentas import crear_invitacion
from .db import conectar


def slug(nombre: str) -> str:
    return re.sub(r"\s+", "-", normalizar(nombre))[:60] or "consorcio"


def crear_consorcio(con, nombre: str, direccion: str, admin_email: str,
                    consorcio_id: str | None = None) -> tuple[str, str]:
    """Crea el consorcio (o completa uno existente) y la invitación para su administrador.
    Devuelve el id del consorcio y el token de la invitación."""
    consorcio_id = consorcio_id or slug(nombre)
    con.execute(
        """INSERT INTO consorcios (id, nombre, direccion, creado_en) VALUES (%s, %s, %s, now())
           ON CONFLICT (id) DO UPDATE SET direccion = EXCLUDED.direccion""",
        (consorcio_id, nombre.strip(), direccion.strip()),
    )
    token, _ = crear_invitacion(con, consorcio_id, admin_email, "administracion", None, None)
    return consorcio_id, token


def _numero(valor, campo: str, unidad: str, tipo):
    if valor in ("", None):
        return None
    try:
        return tipo(str(valor).replace(",", ".").strip())
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{campo} inválido en {unidad}: {valor}") from e


def cargar_unidades(con, consorcio_id: str, unidades: list[dict]) -> int:
    """Carga o actualiza unidades. Si una unidad ya existe (mismo piso y depto),
    actualiza su UF y su porcentual, así el archivo se puede volver a cargar sin duplicar."""
    if con.execute("SELECT 1 FROM consorcios WHERE id = %s", (consorcio_id,)).fetchone() is None:
        raise ValueError(f"No existe el consorcio {consorcio_id}")
    for u in unidades:
        piso, depto = str(u["piso"]).strip(), str(u["depto"]).strip()
        if not piso or not depto:
            raise ValueError(f"Unidad sin piso o depto: {u}")
        porcentual = _numero(u.get("porcentual"), "Porcentual", f"{piso}{depto}", Decimal)
        uf = _numero(u.get("uf"), "UF", f"{piso}{depto}", int)
        con.execute(
            """INSERT INTO unidades_funcionales (consorcio_id, piso, depto, uf, porcentual)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (consorcio_id, piso, depto)
               DO UPDATE SET porcentual = EXCLUDED.porcentual, uf = EXCLUDED.uf""",
            (consorcio_id, piso, depto, uf, porcentual),
        )
    return len(unidades)


def _leer_csv(ruta: str) -> list[dict]:
    """El CSV tiene columnas piso, depto y (opcionales) uf y porcentual."""
    with open(ruta, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main(argv: list[str] | None = None):
    p = argparse.ArgumentParser(description="Alta de consorcios y unidades")
    sub = p.add_subparsers(dest="comando", required=True)

    c = sub.add_parser("consorcio", help="Crea un consorcio e invita a su administrador")
    c.add_argument("--id", help="slug del consorcio (por defecto, sale del nombre)")
    c.add_argument("--nombre", required=True)
    c.add_argument("--direccion", required=True)
    c.add_argument("--admin-email", required=True)

    u = sub.add_parser("unidades", help="Carga las unidades de un consorcio desde un CSV")
    u.add_argument("--consorcio-id", required=True)
    u.add_argument("--csv", required=True)

    args = p.parse_args(argv)
    with conectar() as con:
        if args.comando == "consorcio":
            consorcio_id, token = crear_consorcio(con, args.nombre, args.direccion, args.admin_email, args.id)
            print(f"Consorcio creado con id {consorcio_id}.")
            print("Invitación para el administrador (vence en "
                  f"{config.DIAS_INVITACION} días), usarla en /registro:")
            print(token)
        else:
            n = cargar_unidades(con, args.consorcio_id, _leer_csv(args.csv))
            print(f"{n} unidades cargadas en el consorcio {args.consorcio_id}.")


if __name__ == "__main__":
    main()
