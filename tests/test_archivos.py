"""Tests del bucket de PDF. El bucket se simula con moto (no hace falta MinIO)."""
import os
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql://postgres:postgres@localhost/auth_test")
os.environ.setdefault("JWT_SECRETO", "secreto-solo-para-tests-0123456789abcdef")

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from almacen.pdfs import guardar_pdf, leer_pdf
from almacen.rutas import obtener_bucket
from auth.alta import cargar_unidades, crear_consorcio
from auth.app import app
from auth.db import conectar

RAIZ = Path(__file__).resolve().parents[1]
CONTRASENA = "una-contrasena-larga"
PDF = b"%PDF-1.4\nun acta de prueba\n%%EOF"
cliente = TestClient(app)


@pytest.fixture(autouse=True)
def entorno():
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        app.dependency_overrides[obtener_bucket] = lambda: s3
        with conectar() as con:
            con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            con.execute((RAIZ / "src/auth/01_tablas.sql").read_text())
            con.execute((RAIZ / "src/almacen/01_archivos.sql").read_text())
        yield s3
        app.dependency_overrides.clear()


def cabecera(token):
    return {"Authorization": f"Bearer {token}"}


def alta(nombre, email):
    with conectar() as con:
        consorcio_id, inv = crear_consorcio(con, nombre, "CABA", email)
        cargar_unidades(con, consorcio_id, [{"piso": "1", "depto": "A"}])
    r = cliente.post("/auth/registro", json={"token": inv, "nombre": "Admin", "contrasena": CONTRASENA})
    return consorcio_id, r.json()["token"]


def subir(token, consorcio_id, contenido=PDF, nombre="acta.pdf"):
    return cliente.post("/archivos", headers=cabecera(token), data={"consorcio_id": consorcio_id},
                        files={"archivo": (nombre, contenido, "application/pdf")})


def test_mismo_pdf_dos_veces_no_se_duplica():
    c, token = alta("Mercedes 274", "admin@test.com")
    r1 = subir(token, c)
    r2 = subir(token, c, nombre="otro-nombre.pdf")   # mismo contenido, otro nombre
    assert r1.status_code == 201 and r1.json()["duplicado"] is False
    assert r2.json() == {"id": r1.json()["id"], "duplicado": True}
    assert len(cliente.get("/archivos", params={"consorcio_id": c}, headers=cabecera(token)).json()) == 1


def test_mismo_pdf_en_otro_consorcio_si_se_guarda():
    c1, t1 = alta("Edificio 1", "a1@test.com")
    c2, t2 = alta("Edificio 2", "a2@test.com")
    assert subir(t1, c1).json()["duplicado"] is False
    assert subir(t2, c2).json()["duplicado"] is False


def test_rechaza_lo_que_no_es_pdf():
    c, token = alta("Mercedes 274", "admin@test.com")
    assert subir(token, c, contenido=b"no soy un pdf").status_code == 422


def test_admin_de_otro_consorcio_no_puede_subir():
    c1, _ = alta("Edificio 1", "a1@test.com")
    _, t2 = alta("Edificio 2", "a2@test.com")
    assert subir(t2, c1).status_code == 403


def test_vecino_no_puede_subir():
    c, token_admin = alta("Mercedes 274", "admin@test.com")
    unidad = cliente.get(f"/auth/consorcios/{c}/unidades", headers=cabecera(token_admin)).json()[0]
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_admin),
                     json={"consorcio_id": c, "email": "v@test.com", "rol": "propietario", "unidad_id": unidad["id"]})
    r = cliente.post("/auth/registro", json={"token": r.json()["token_invitacion"], "nombre": "V", "contrasena": CONTRASENA})
    assert subir(r.json()["token"], c).status_code == 403


def test_leer_pdf_solo_desde_su_consorcio(entorno):
    c1, _ = alta("Edificio 1", "a1@test.com")
    c2, _ = alta("Edificio 2", "a2@test.com")
    with conectar() as con:
        guardado = guardar_pdf(con, entorno, c1, "acta.pdf", PDF)
        assert leer_pdf(con, entorno, guardado.id, c1) == PDF
        with pytest.raises(FileNotFoundError):
            leer_pdf(con, entorno, guardado.id, c2)
