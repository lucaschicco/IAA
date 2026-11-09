"""Identidad dentro de un consorcio: inmutable, agnóstica al login y sin pistas cuando falla."""
from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from consorcio.almacen import postgres
from consorcio.almacen.postgres import Membresia, UnidadFuncional, Usuario
from consorcio.config import config
from consorcio.identidad import Identidad, SinMembresia, resolver_identidad

BASES_DESCARTABLES = {"consorcio_test"}


def _hay_postgres() -> bool:
    try:
        with postgres.engine().connect():
            return True
    except Exception:
        return False


requiere_postgres = pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")


def _vaciar_base() -> None:
    postgres.engine().dispose()
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def base():
    """Dos consorcios inventados; Ana es propietaria en uno solo y Beto es administrador en el otro."""
    if make_url(config.postgres_url).database not in BASES_DESCARTABLES:
        pytest.skip("la base configurada no es descartable: este test borra el schema")
    _vaciar_base()
    postgres.crear_tablas()
    postgres.asegurar_consorcio("edificio-a")
    postgres.asegurar_consorcio("edificio-b")
    with Session(postgres.engine()) as s:
        ana = Usuario(sujeto_externo="login|ana", nombre="Ana Inventada")
        beto = Usuario(sujeto_externo="login|beto", nombre="Beto Inventado")
        s.add_all([ana, beto])
        s.flush()
        u12 = UnidadFuncional(consorcio_id="edificio-a", piso="6", depto="B", uf=12)
        u3 = UnidadFuncional(consorcio_id="edificio-a", piso="1", depto="C", uf=3)
        s.add_all([u12, u3])
        s.flush()
        s.add_all([Membresia(usuario_id=ana.id, consorcio_id="edificio-a", rol="propietario", unidad_id=u.id)
                   for u in (u12, u3)])
        s.add(Membresia(usuario_id=beto.id, consorcio_id="edificio-b", rol="administracion"))
        s.commit()
    yield
    _vaciar_base()


def test_identidad_es_inmutable():
    i = Identidad(usuario_id=1, consorcio_id="edificio-a", rol="propietario", unidades=(3, 12))
    with pytest.raises(FrozenInstanceError):
        i.rol = "administracion"
    assert isinstance(i.unidades, tuple)


@requiere_postgres
def test_resolver_identidad_devuelve_rol_y_unidades(base):
    i = resolver_identidad("login|ana", "edificio-a")
    assert (i.consorcio_id, i.rol, i.unidades) == ("edificio-a", "propietario", (3, 12))
    assert isinstance(i.usuario_id, int)
    beto = resolver_identidad("login|beto", "edificio-b")
    assert (beto.rol, beto.unidades) == ("administracion", ())


@requiere_postgres
def test_sin_membresia_falla_siempre_con_el_mismo_mensaje(base):
    mensajes = set()
    for sujeto, consorcio in [
        ("login|nadie", "edificio-a"),  # usuario inexistente
        ("login|ana", "no-existe"),  # consorcio inexistente
        ("login|ana", "edificio-b"),  # usuario de otro consorcio
    ]:
        with pytest.raises(SinMembresia) as error:
            resolver_identidad(sujeto, consorcio)
        mensajes.add(str(error.value))
    assert len(mensajes) == 1


@requiere_postgres
@pytest.mark.parametrize("fila", [
    lambda ids: Usuario(sujeto_externo="login|ana"),
    lambda ids: Membresia(usuario_id=ids["ana"], consorcio_id="edificio-a", rol="propietario", unidad_id=ids["u3"]),
    lambda ids: Membresia(usuario_id=ids["ana"], consorcio_id="edificio-b", rol="vecino"),
    lambda ids: Membresia(usuario_id=ids["ana"], consorcio_id="edificio-b", rol="propietario"),
    lambda ids: Membresia(usuario_id=ids["ana"], consorcio_id="edificio-a", rol="administracion", unidad_id=ids["u3"]),
    lambda ids: UnidadFuncional(consorcio_id="edificio-a", piso="9", depto="Z", uf=3),
], ids=["sujeto-duplicado", "membresia-duplicada", "rol-invalido", "propietario-sin-unidad",
        "administracion-con-unidad", "uf-duplicada"])
def test_la_base_exige_unicidad_y_rol(base, fila):
    with Session(postgres.engine()) as s:
        ana = s.query(Usuario).filter_by(sujeto_externo="login|ana").one()
        u3 = s.query(UnidadFuncional).filter_by(consorcio_id="edificio-a", uf=3).one()
        s.add(fila({"ana": ana.id, "u3": u3.id}))
        with pytest.raises(IntegrityError):
            s.commit()


@requiere_postgres
def test_varios_roles_se_juntan_y_manda_el_principal(base):
    with Session(postgres.engine()) as s:
        ana = s.query(Usuario).filter_by(sujeto_externo="login|ana").one()
        s.add(Membresia(usuario_id=ana.id, consorcio_id="edificio-a", rol="consejo"))
        s.commit()
    i = resolver_identidad("login|ana", "edificio-a")
    assert (i.rol, i.unidades, i.roles) == ("consejo", (3, 12), frozenset({"consejo", "propietario"}))
    assert not i.es_administracion


@requiere_postgres
def test_membresia_o_usuario_inactivo_no_entra(base):
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE usuarios SET activo = false WHERE sujeto_externo = 'login|beto'"))
        con.execute(text("UPDATE membresias SET activo = false WHERE rol = 'propietario' AND unidad_id = "
                         "(SELECT id FROM unidades_funcionales WHERE uf = 12)"))
    with pytest.raises(SinMembresia):
        resolver_identidad("login|beto", "edificio-b")
    assert resolver_identidad("login|ana", "edificio-a").unidades == (3,)
