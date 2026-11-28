"""Tests del login y los permisos de la API JSON (de Lucas). Corren en la base descartable de los tests
(`make test`), con el esquema de Alembic. Cada test arranca con la base vacía."""
import pytest
from apoyo_auth import CONTRASENA, base_vacia_con_esquema
from fastapi.testclient import TestClient

from consorcio.auth.app import app
from consorcio.auth.db import conectar
from consorcio.auth.permisos import unidades_propias
from consorcio.auth.seguridad import crear_token_sesion, hashear_contrasena

A, B = "mercedes-274", "otro-edificio"


@pytest.fixture(autouse=True)
def base_limpia():
    base_vacia_con_esquema()
    with conectar() as con:
        con.execute("""INSERT INTO consorcios (id, nombre, direccion, creado_en) VALUES
                       (%s, 'Mercedes 274', 'Mercedes 274', now()), (%s, 'Otro edificio', 'Calle Falsa 123', now())""",
                    (A, B))
        con.execute("""INSERT INTO unidades_funcionales (consorcio_id, piso, depto) VALUES
                       (%s, '5', 'D'), (%s, '3', 'A'), (%s, '1', 'A')""", (A, A, B))
        h = hashear_contrasena(CONTRASENA)
        con.execute("""INSERT INTO usuarios (sujeto_externo, email, nombre, hash_contrasena) VALUES
                       ('login:1', 'admin1@test.com', 'Admin 1', %s), ('login:2', 'admin2@test.com', 'Admin 2', %s)""",
                    (h, h))
        con.execute("""INSERT INTO membresias (usuario_id, consorcio_id, unidad_id, rol) VALUES
                       (1, %s, NULL, 'administracion'), (2, %s, NULL, 'administracion')""", (A, B))


cliente = TestClient(app)


def login(email, contrasena=CONTRASENA):
    return cliente.post("/auth/login", json={"email": email, "contrasena": contrasena})


def cabecera(token):
    return {"Authorization": f"Bearer {token}"}


def token_de(email):
    r = login(email)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def invitar_y_registrar(email, rol, unidad_id, admin="admin1@test.com", consorcio_id=A):
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de(admin)),
                     json={"consorcio_id": consorcio_id, "email": email, "rol": rol, "unidad_id": unidad_id})
    assert r.status_code == 201, r.text
    invitacion = r.json()["token_invitacion"]
    r = cliente.post("/auth/registro", json={"token": invitacion, "nombre": email, "contrasena": CONTRASENA})
    assert r.status_code == 200, r.text
    return invitacion, r.json()["token"]


# --- Login -------------------------------------------------------------

def test_login_correcto_y_quien_soy():
    r = cliente.get("/auth/yo", headers=cabecera(token_de("admin1@test.com")))
    assert r.status_code == 200
    assert r.json()["membresias"][0]["rol"] == "administracion"
    assert "hash_contrasena" not in r.json()


def test_login_con_mayusculas_en_el_mail():
    assert login("ADMIN1@Test.com").status_code == 200


def test_contrasena_incorrecta():
    assert login("admin1@test.com", "otra-contrasena-larga").status_code == 401


def test_mail_inexistente_da_el_mismo_error():
    assert login("nadie@test.com").status_code == 401


def test_bloqueo_tras_cinco_intentos_fallidos():
    for _ in range(5):
        assert login("admin1@test.com", "incorrecta-incorrecta").status_code == 401
    # Bloqueado incluso con la contraseña correcta
    assert login("admin1@test.com").status_code == 429


def test_usuario_dado_de_baja_no_entra():
    with conectar() as con:
        con.execute("UPDATE usuarios SET activo = false WHERE id = 1")
    assert login("admin1@test.com").status_code == 401


# --- Tokens ------------------------------------------------------------

def test_sin_token():
    assert cliente.get("/auth/yo").status_code == 401


def test_token_falso():
    assert cliente.get("/auth/yo", headers=cabecera("cualquier-cosa")).status_code == 401


def test_token_vencido():
    vencido = crear_token_sesion(1, horas=-1)
    assert cliente.get("/auth/yo", headers=cabecera(vencido)).status_code == 401


def test_baja_aplica_aunque_el_token_siga_vigente():
    token = token_de("admin1@test.com")
    with conectar() as con:
        con.execute("UPDATE usuarios SET activo = false WHERE id = 1")
    assert cliente.get("/auth/yo", headers=cabecera(token)).status_code == 401


# --- Invitaciones y registro --------------------------------------------

def test_invitacion_y_registro_de_inquilino():
    _, token = invitar_y_registrar("inquilino@test.com", "inquilino", 2)
    membresias = cliente.get("/auth/yo", headers=cabecera(token)).json()["membresias"]
    assert [(m["rol"], m["piso"], m["depto"]) for m in membresias] == [("inquilino", "3", "A")]


def test_invitacion_no_se_puede_usar_dos_veces():
    invitacion, _ = invitar_y_registrar("vecino@test.com", "propietario", 1)
    r = cliente.post("/auth/registro", json={"token": invitacion, "nombre": "x", "contrasena": CONTRASENA})
    assert r.status_code == 400


def test_invitacion_vencida():
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de("admin1@test.com")),
                     json={"consorcio_id": A, "email": "tarde@test.com", "rol": "propietario", "unidad_id": 1})
    with conectar() as con:
        con.execute("UPDATE invitaciones SET vence_en = now() - interval '1 day'")
    r = cliente.post("/auth/registro", json={"token": r.json()["token_invitacion"],
                                             "nombre": "x", "contrasena": CONTRASENA})
    assert r.status_code == 400


def test_contrasena_corta_rechazada():
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de("admin1@test.com")),
                     json={"consorcio_id": A, "email": "corta@test.com", "rol": "propietario", "unidad_id": 1})
    r = cliente.post("/auth/registro", json={"token": r.json()["token_invitacion"],
                                             "nombre": "x", "contrasena": "corta"})
    assert r.status_code == 422


def test_mismo_mail_en_dos_edificios_suma_membresia():
    invitar_y_registrar("dueno@test.com", "propietario", 1)
    _, token = invitar_y_registrar("dueno@test.com", "propietario", 3, admin="admin2@test.com", consorcio_id=B)
    consorcios = {m["consorcio_id"] for m in cliente.get("/auth/yo", headers=cabecera(token)).json()["membresias"]}
    assert consorcios == {A, B}


# --- Permisos ----------------------------------------------------------

def test_inquilino_no_puede_invitar():
    _, token = invitar_y_registrar("inquilino@test.com", "inquilino", 2)
    r = cliente.post("/auth/invitaciones", headers=cabecera(token),
                     json={"consorcio_id": A, "email": "x@test.com", "rol": "propietario", "unidad_id": 1})
    assert r.status_code == 403


def test_admin_de_otro_edificio_no_puede_invitar():
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de("admin2@test.com")),
                     json={"consorcio_id": A, "email": "x@test.com", "rol": "propietario", "unidad_id": 1})
    assert r.status_code == 403


def test_no_se_puede_invitar_a_una_unidad_de_otro_edificio():
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de("admin1@test.com")),
                     json={"consorcio_id": A, "email": "x@test.com", "rol": "propietario", "unidad_id": 3})
    assert r.status_code == 422


def test_inquilino_sin_unidad_rechazado():
    r = cliente.post("/auth/invitaciones", headers=cabecera(token_de("admin1@test.com")),
                     json={"consorcio_id": A, "email": "x@test.com", "rol": "inquilino", "unidad_id": None})
    assert r.status_code == 422


def test_vecino_solo_ve_su_unidad():
    _, token = invitar_y_registrar("inquilino@test.com", "inquilino", 2)
    usuario = cliente.get("/auth/yo", headers=cabecera(token)).json()
    assert unidades_propias(usuario, A) == [2]   # solo el 3A
    assert unidades_propias(usuario, B) == []    # nada en otro edificio
