"""Tests de persistencia: necesitan Postgres (docker compose up)."""
from __future__ import annotations

import json
import os
import uuid

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


def test_migrar_consorcios_exige_el_consorcio_por_defecto():
    from consorcio.almacen import postgres

    with pytest.raises(TypeError):
        postgres.migrar_consorcios()


def test_cli_migrar_resuelve_el_por_defecto_y_solo_informa_postgres(monkeypatch, capsys):
    from consorcio import cli
    from consorcio.almacen import postgres
    from consorcio.config import config

    pedidos = []
    cambios = [{"viejo": "abc", "nuevo": "x:abc", "consorcio": "x"}]
    monkeypatch.setattr(postgres, "migrar_consorcios", lambda por_defecto: pedidos.append(por_defecto) or cambios)
    assert cli.main(["migrar"]) == 0
    assert pedidos == [config.consorcio_por_defecto]
    assert json.loads(capsys.readouterr().out) == {"documentos_migrados": 1, "cambios": cambios}


def test_cli_ya_no_tiene_el_subcomando_grafo(capsys):
    from consorcio import cli

    with pytest.raises(SystemExit) as e:
        cli.main(["grafo"])
    assert e.value.code == 2
    assert "grafo" in capsys.readouterr().err


def test_cli_dibujar_grafo_de_langgraph_sigue(capsys):
    from consorcio import cli

    assert cli.main(["dibujar-grafo", "ingesta"]) == 0
    assert "persistir" in capsys.readouterr().out


def _pdf_inventado() -> bytes:
    """PDF mínimo con capa de texto y contenido propio, para no depender de datos/."""
    texto = f"Reglamento inventado de prueba {uuid.uuid4().hex}. Texto suficiente para la capa de texto. " * 2
    flujo = f"BT /F1 9 Tf 20 700 Td ({texto}) Tj ET".encode("ascii")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(flujo), flujo),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    posiciones = []
    for i, obj in enumerate(objetos, 1):
        posiciones.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    inicio = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objetos) + 1, inicio)
    return pdf


@pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")
def test_ingesta_persiste_el_documento_en_postgres(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from consorcio.almacen import postgres
    from consorcio.config import config
    from consorcio.ingesta.grafo import ingestar_pdf

    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")
    consorcio = f"prueba-{uuid.uuid4().hex[:8]}"
    pdf = tmp_path / "inventado.pdf"
    pdf.write_bytes(_pdf_inventado())

    final = ingestar_pdf(str(pdf), consorcio_id=consorcio)
    try:
        assert final["persistido"]["postgres"]["filas"]
        with Session(postgres.engine()) as s:
            assert s.get(postgres.Documento, final["documento_id"]).consorcio_id == consorcio
    finally:
        with Session(postgres.engine()) as s:
            s.delete(s.get(postgres.Documento, final["documento_id"]))
            s.delete(s.get(postgres.Consorcio, consorcio))
            s.commit()
