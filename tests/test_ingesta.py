"""Tests del pipeline de ingesta. Corren sin claves (LLM_PROVIDER=fake) y sin bases
de datos (sin_persistir=True). Los que necesitan Postgres/Neo4j se saltean si no
hay conexión."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ["LLM_PROVIDER"] = "fake"  # los tests nunca llaman a un modelo real
os.environ["MLFLOW_TRACKING_URI"] = ""

from consorcio.config import RAIZ_REPO
from consorcio.ingesta import esquemas
from consorcio.ingesta.clasificador import clasificar_pdf
from consorcio.ingesta.estructurar import fusionar
from consorcio.ingesta.grafo import construir_grafo, ingestar_pdf
from consorcio.ingesta.skill import cargar_skill

DOCS = RAIZ_REPO / "datos" / "documentos"
PDF_TEXTO = DOCS / "REGLAMENTO COCHERAS MERCEDES_12-05-2026_18_59_13 (1).pdf"
PDF_ESCANEADO = DOCS / "ACTA_MERCEDES_25-7-2024.pdf"

requiere_datos = pytest.mark.skipif(not PDF_TEXTO.exists(), reason="faltan los PDF de datos/")


def test_skill_tiene_las_tres_secciones():
    for s in ("transcripcion", "metadatos", "estructurar"):
        assert len(cargar_skill(s)) > 100


def test_grafo_se_compila_y_tiene_los_nodos():
    nodos = set(construir_grafo().get_graph().nodes)
    assert {"clasificar", "extraer_texto", "extraer_tesseract", "extraer_vision", "estructurar", "persistir"} <= nodos


@requiere_datos
def test_clasificador_detecta_capa_de_texto():
    c = clasificar_pdf(str(PDF_TEXTO))
    assert c.metodo == "texto"
    assert c.confianza == 1.0
    assert all(p.metodo == "texto" for p in c.paginas)


@requiere_datos
def test_clasificador_detecta_escaneado():
    c = clasificar_pdf(str(PDF_ESCANEADO))
    assert c.metodo in ("tesseract", "vision")
    assert any(p.chars_texto == 0 for p in c.paginas)


@requiere_datos
def test_metodo_forzado_se_respeta():
    c = clasificar_pdf(str(PDF_TEXTO), metodo_forzado="vision")
    assert c.metodo == "vision"


def test_fusionar_deduplica_y_completa():
    meta = esquemas.MetadatosDocumento(titulo="t", resumen="r")
    a = esquemas.ExtraccionParcial(personas=[esquemas.Persona(nombre="Norah Korovsky", rol="fiduciario")])
    b = esquemas.ExtraccionParcial(personas=[
        esquemas.Persona(nombre="NORAH KOROVSKY", nacionalidad="argentina",
                         datos_sensibles=[esquemas.DatoSensible(tipo="dni", valor="XX.XXX.XXX")])
    ])
    doc = fusionar(meta, [a, b])
    assert len(doc.personas) == 1
    p = doc.personas[0]
    assert p.rol == "fiduciario" and p.nacionalidad == "argentina"
    assert p.datos_sensibles[0].tipo == "dni"


@requiere_datos
def test_ingesta_completa_con_modelo_fake(tmp_path, monkeypatch):
    from consorcio import config as cfg

    monkeypatch.setattr(cfg.config, "dir_procesados", tmp_path)
    final = ingestar_pdf(str(PDF_TEXTO), sin_persistir=True)
    assert final["clasificacion"]["metodo"] == "texto"
    assert len(final["paginas"]) == final["clasificacion"]["total_paginas"]
    assert final["costo"]["llamadas"] >= 1
    carpeta = Path(final["persistido"]["carpeta"])
    assert (carpeta / "texto.md").exists() and (carpeta / "extraccion.json").exists()


def test_enums_toleran_valores_inventados():
    p = esquemas.Persona(nombre="x", rol="Comprador", datos_sensibles=[esquemas.DatoSensible(tipo="pasaporte", valor="1")])
    assert p.rol == "otro" and p.datos_sensibles[0].tipo == "otro"
    assert esquemas.Persona(nombre="x", rol="Propietario").rol == "propietario"
    assert esquemas.Evento(fecha="2024-01-01", tipo="firma", descripcion="d").tipo == "otro"
    assert esquemas.MetadatosDocumento(tipo_documento="escritura", titulo="t", resumen="r").tipo_documento == "otro"
    parcial = esquemas.ExtraccionParcial.model_validate(
        {"eventos": [{"fecha": None, "descripcion": "sin fecha"}, {"fecha": "2024", "descripcion": "ok"}]}
    )
    assert [e.descripcion for e in parcial.eventos] == ["ok"]


def test_texto_degenerado_detecta_bucles():
    from consorcio.ingesta.extractores import texto_degenerado

    assert texto_degenerado("ARTICULO 1. " + "l l " * 300)
    assert texto_degenerado("[ilegible] " * 2000)
    assert texto_degenerado("x" * 20_000)
    normal = "ARTÍCULO QUINTO: Los copropietarios deberán abonar las expensas comunes dentro de los diez días. " * 20
    assert not texto_degenerado(normal)
    assert not texto_degenerado("")
