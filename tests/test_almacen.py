"""Tests de persistencia: necesitan Postgres y Neo4j (docker compose up)."""
from __future__ import annotations

import os

import pytest

os.environ["LLM_PROVIDER"] = "fake"  # los tests nunca llaman a un modelo real

from consorcio.ingesta import esquemas


def _hay_postgres() -> bool:
    try:
        from consorcio.almacen.postgres import engine

        with engine().connect():
            return True
    except Exception:
        return False


def _hay_neo4j() -> bool:
    try:
        from consorcio.almacen.grafo_neo4j import driver

        driver().verify_connectivity()
        return True
    except Exception:
        return False


def _doc() -> esquemas.DocumentoExtraido:
    return esquemas.DocumentoExtraido(
        metadatos=esquemas.MetadatosDocumento(
            tipo_documento="contrato", titulo="Doc de prueba", fecha_documento="2014-01-14",
            consorcio="Cabrera 5736", resumen="Prueba",
        ),
        personas=[esquemas.Persona(nombre="Persona Prueba", rol="escribano", domicilio="Callao 441",
                                   datos_sensibles=[esquemas.DatoSensible(tipo="dni", valor="12.345.678")], pagina=1)],
        direcciones=[esquemas.Direccion(texto="Callao 441", tipo="domicilio_persona", pagina=1)],
        eventos=[esquemas.Evento(fecha="2014-01-14", tipo="escritura", descripcion="Escritura", pagina=1)],
        secciones=[esquemas.Seccion(titulo="Art. 1", tema="Objeto", resumen="Prueba", pagina_inicio=1, pagina_fin=1)],
    )


ID_PRUEBA = "prueba:test0000000000ab"  # <consorcio>:<hash>, como arma la ingesta


@pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")
def test_postgres_guarda_y_marca_sensibles():
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from consorcio.almacen import postgres

    res = postgres.guardar_documento(
        documento_id=ID_PRUEBA, nombre_archivo="prueba.pdf", ruta="x", clasificacion={"metodo": "texto", "confianza": 1, "total_paginas": 1},
        paginas=[{"numero": 1, "texto": "hola", "metodo": "texto", "confianza": 100}], doc=_doc(), costo={}, llamadas=[], segundos_total=0.1,
    )
    assert res["filas"]["personas"] == 1 and res["filas"]["datos_sensibles"] == 1
    with Session(postgres.engine()) as s:
        ds = s.scalars(select(postgres.DatoSensible).where(postgres.DatoSensible.documento_id == ID_PRUEBA)).one()
        assert ds.sensible is True and ds.tipo == "dni"
    # idempotente
    res2 = postgres.guardar_documento(
        documento_id=ID_PRUEBA, nombre_archivo="prueba.pdf", ruta="x", clasificacion={"metodo": "texto", "confianza": 1, "total_paginas": 1},
        paginas=[], doc=_doc(), costo={}, llamadas=[], segundos_total=0.1,
    )
    assert res2["filas"]["personas"] == 1
    # limpieza: que el documento de prueba no quede en la base que usa `preguntar`
    with Session(postgres.engine()) as s:
        s.delete(s.get(postgres.Documento, ID_PRUEBA))
        s.delete(s.get(postgres.Consorcio, "prueba"))
        s.commit()


@pytest.mark.skipif(not _hay_neo4j(), reason="sin Neo4j")
def test_neo4j_no_guarda_sensibles():
    from consorcio.almacen import grafo_neo4j

    res = grafo_neo4j.guardar_documento(ID_PRUEBA, _doc())
    assert res["aristas"].get("MENCIONA") == 1
    with grafo_neo4j.driver().session() as s:
        n = s.run("MATCH (n) WHERE any(k IN keys(n) WHERE toString(n[k]) CONTAINS '12.345.678') RETURN count(n) AS n").single()["n"]
        assert n == 0
        s.run("MATCH (d:Documento {id:'prueba:test0000000000ab'}) OPTIONAL MATCH (d)-[:TIENE_SECCION]->(x) DETACH DELETE x, d")
