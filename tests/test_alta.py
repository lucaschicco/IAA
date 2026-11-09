"""Tests del alta de consorcios y la carga de unidades."""
import os
import subprocess
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost/auth_test")
os.environ.setdefault("JWT_SECRETO", "secreto-solo-para-tests-0123456789abcdef")

import pytest
from fastapi.testclient import TestClient

from auth.alta import cargar_unidades, crear_consorcio
from auth.app import app
from auth.db import conectar

RAIZ = Path(__file__).resolve().parents[1]
TABLAS = RAIZ / "src" / "auth" / "01_tablas.sql"
CONTRASENA = "una-contrasena-larga"
cliente = TestClient(app)


@pytest.fixture(autouse=True)
def base_vacia():
    with conectar() as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        con.execute(TABLAS.read_text())


def cabecera(token):
    return {"Authorization": f"Bearer {token}"}


def alta_con_admin(nombre="Mercedes 274", email="admin@estudio.com"):
    """Crea un consorcio y registra a su administrador. Devuelve (id, token de sesión)."""
    with conectar() as con:
        consorcio_id, invitacion = crear_consorcio(con, nombre, "CABA", email)
    r = cliente.post("/auth/registro", json={"token": invitacion, "nombre": "Admin", "contrasena": CONTRASENA})
    assert r.status_code == 200, r.text
    return consorcio_id, r.json()["token"]


def test_flujo_completo_de_alta():
    # 1. Alta del consorcio y su administrador
    consorcio_id, token_admin = alta_con_admin()
    # 2. La administración carga las unidades
    r = cliente.post(f"/auth/consorcios/{consorcio_id}/unidades", headers=cabecera(token_admin),
                     json=[{"piso": "5", "depto": "D", "porcentual": 1.25},
                           {"piso": "3", "depto": "A", "porcentual": 1.10}])
    assert r.status_code == 201 and r.json()["cargadas"] == 2
    unidades = cliente.get(f"/auth/consorcios/{consorcio_id}/unidades", headers=cabecera(token_admin)).json()
    id_5d = next(u["id"] for u in unidades if u["piso"] == "5" and u["depto"] == "D")
    # 3. Invita a un vecino a su unidad
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_admin),
                     json={"consorcio_id": consorcio_id, "email": "vecino@test.com",
                           "rol": "propietario", "unidad_id": id_5d})
    assert r.status_code == 201
    # 4. El vecino se registra y queda en su unidad
    r = cliente.post("/auth/registro", json={"token": r.json()["token_invitacion"],
                                             "nombre": "Vecino", "contrasena": CONTRASENA})
    yo = cliente.get("/auth/yo", headers=cabecera(r.json()["token"])).json()
    assert [(m["rol"], m["piso"], m["depto"]) for m in yo["membresias"]] == [("propietario", "5", "D")]


def test_recargar_unidades_no_duplica_y_actualiza_porcentual():
    consorcio_id, _ = alta_con_admin()
    with conectar() as con:
        cargar_unidades(con, consorcio_id, [{"piso": "1", "depto": "A", "porcentual": "1,5"}])
        cargar_unidades(con, consorcio_id, [{"piso": "1", "depto": "A", "porcentual": "2"}])
        filas = con.execute("SELECT porcentual FROM unidades WHERE consorcio_id = %s", (consorcio_id,)).fetchall()
    assert len(filas) == 1 and float(filas[0]["porcentual"]) == 2.0


def test_porcentual_invalido_rechazado():
    consorcio_id, token = alta_con_admin()
    with conectar() as con, pytest.raises(ValueError):
        cargar_unidades(con, consorcio_id, [{"piso": "1", "depto": "A", "porcentual": "mucho"}])


def test_admin_de_otro_consorcio_no_carga_unidades():
    consorcio_1, _ = alta_con_admin("Edificio 1", "admin1@test.com")
    _, token_2 = alta_con_admin("Edificio 2", "admin2@test.com")
    r = cliente.post(f"/auth/consorcios/{consorcio_1}/unidades", headers=cabecera(token_2),
                     json=[{"piso": "1", "depto": "A"}])
    assert r.status_code == 403


def test_vecino_no_ve_el_listado_de_unidades():
    consorcio_id, token_admin = alta_con_admin()
    cliente.post(f"/auth/consorcios/{consorcio_id}/unidades", headers=cabecera(token_admin),
                 json=[{"piso": "1", "depto": "A"}])
    unidad = cliente.get(f"/auth/consorcios/{consorcio_id}/unidades", headers=cabecera(token_admin)).json()[0]
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_admin),
                     json={"consorcio_id": consorcio_id, "email": "v@test.com",
                           "rol": "inquilino", "unidad_id": unidad["id"]})
    r = cliente.post("/auth/registro", json={"token": r.json()["token_invitacion"],
                                             "nombre": "V", "contrasena": CONTRASENA})
    r = cliente.get(f"/auth/consorcios/{consorcio_id}/unidades", headers=cabecera(r.json()["token"]))
    assert r.status_code == 403


def test_comando_de_terminal(tmp_path):
    entorno = {**os.environ, "PYTHONPATH": str(RAIZ / "src")}
    r = subprocess.run([sys.executable, "-m", "auth.alta", "consorcio", "--nombre", "Mercedes 274",
                        "--direccion", "CABA", "--admin-email", "admin@estudio.com"],
                       capture_output=True, text=True, env=entorno)
    assert r.returncode == 0, r.stderr
    assert "Consorcio creado con id 1" in r.stdout

    archivo = tmp_path / "unidades.csv"
    archivo.write_text("piso,depto,porcentual\n1,A,1.5\n1,B,1.5\n2,A,2\n", encoding="utf-8")
    r = subprocess.run([sys.executable, "-m", "auth.alta", "unidades", "--consorcio-id", "1",
                        "--csv", str(archivo)], capture_output=True, text=True, env=entorno)
    assert r.returncode == 0, r.stderr
    assert "3 unidades cargadas" in r.stdout
