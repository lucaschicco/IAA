"""Tests del almacén de crudos. Los que hablan con S3 necesitan S3_ENDPOINT_URL (SeaweedFS o similar) y
se saltean si no está; nunca tocan objetos fuera del prefijo `prueba-`."""
from __future__ import annotations

import logging
import re
import uuid

import pytest

from consorcio.almacen import crudos
from consorcio.config import RAIZ_REPO, config

CERRADO = "http://127.0.0.1:1"  # nadie escucha en ese puerto
CONSORCIO = "prueba-crudos"
SHA = "a" * 64


def _hay_s3() -> bool:
    if not crudos.configurado():
        return False
    try:
        crudos.verificar_bucket()
        return True
    except crudos.CrudoNoDisponible:
        return False


requiere_s3 = pytest.mark.skipif(not _hay_s3(), reason="sin S3 configurado y accesible")


@pytest.fixture
def pdf(tmp_path):
    ruta = tmp_path / "crudo.pdf"
    ruta.write_bytes(b"%PDF-1.7 contenido inventado \x00\xff" * 50)
    return ruta


@pytest.fixture
def s3():
    cliente = crudos._cliente()
    yield cliente
    res = cliente.list_objects_v2(Bucket=config.s3_bucket, Prefix=f"{CONSORCIO}/")
    for o in res.get("Contents", []):
        cliente.delete_object(Bucket=config.s3_bucket, Key=o["Key"])


def test_la_clave_lleva_consorcio_y_sha():
    assert crudos.clave_crudo("mercedes-274", SHA) == f"mercedes-274/{SHA}.pdf"


def test_sin_endpoint_no_sube_y_avisa(monkeypatch, pdf, caplog):
    monkeypatch.setattr(config, "s3_endpoint_url", "")
    assert not crudos.configurado()
    crudos.verificar_bucket()  # no hace nada
    with caplog.at_level(logging.WARNING, logger="consorcio.crudos"):
        assert crudos.subir_crudo(str(pdf), CONSORCIO, SHA) is None
    assert "S3_ENDPOINT_URL" in caplog.text
    assert crudos.borrar_crudo("x/y.pdf") is False


def test_endpoint_inalcanzable_lanza_crudo_no_disponible(monkeypatch, pdf):
    monkeypatch.setattr(config, "s3_endpoint_url", CERRADO)
    with pytest.raises(crudos.CrudoNoDisponible):
        crudos.verificar_bucket()
    with pytest.raises(crudos.CrudoNoDisponible):
        crudos.subir_crudo(str(pdf), CONSORCIO, SHA)


def test_borrar_es_best_effort(monkeypatch, caplog):
    monkeypatch.setattr(config, "s3_endpoint_url", CERRADO)
    with caplog.at_level(logging.WARNING, logger="consorcio.crudos"):
        assert crudos.borrar_crudo("x/y.pdf") is False
    assert "x/y.pdf" in caplog.text


@requiere_s3
def test_archivo_inexistente_lanza_crudo_no_disponible(tmp_path):
    with pytest.raises(crudos.CrudoNoDisponible):
        crudos.subir_crudo(str(tmp_path / "no-existe.pdf"), CONSORCIO, SHA)


@requiere_s3
def test_verificar_bucket_lo_crea_si_falta(monkeypatch):
    nombre = f"prueba-{uuid.uuid4().hex[:12]}"
    monkeypatch.setattr(config, "s3_bucket", nombre)
    cliente = crudos._cliente()
    with pytest.raises(cliente.exceptions.ClientError):
        cliente.head_bucket(Bucket=nombre)
    crudos.verificar_bucket()
    try:
        cliente.head_bucket(Bucket=nombre)
        crudos.verificar_bucket()  # idempotente
    finally:
        cliente.delete_bucket(Bucket=nombre)


@requiere_s3
def test_subir_y_borrar_conserva_los_bytes(pdf, s3):
    clave = crudos.subir_crudo(str(pdf), CONSORCIO, SHA)
    assert clave == f"{CONSORCIO}/{SHA}.pdf"
    obj = s3.get_object(Bucket=config.s3_bucket, Key=clave)
    assert obj["Body"].read() == pdf.read_bytes()
    assert obj["ContentType"] == "application/pdf"
    assert crudos.subir_crudo(str(pdf), CONSORCIO, SHA) == clave  # subir de nuevo es idempotente
    assert crudos.borrar_crudo(clave) is True
    res = s3.list_objects_v2(Bucket=config.s3_bucket, Prefix=f"{CONSORCIO}/")
    assert not res.get("Contents")


@requiere_s3
def test_el_cliente_sigue_a_la_configuracion(monkeypatch, pdf, s3):
    assert crudos.subir_crudo(str(pdf), CONSORCIO, SHA)  # primero funciona
    monkeypatch.setattr(config, "s3_endpoint_url", CERRADO)
    with pytest.raises(crudos.CrudoNoDisponible):  # sin caché: el cambio de endpoint se nota enseguida
        crudos.subir_crudo(str(pdf), CONSORCIO, SHA)


def test_compose_y_env_ejemplo_declaran_seaweedfs():
    compose = (RAIZ_REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"image: chrislusf/seaweedfs:\d+\.\d+\s", compose)
    assert "seaweeddata:/data" in compose
    assert "S3_ENDPOINT_URL=http://seaweedfs:8333" in (RAIZ_REPO / ".env.example").read_text(encoding="utf-8")
