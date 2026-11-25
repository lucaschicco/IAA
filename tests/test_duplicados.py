"""Tests de duplicados, candado, corridas parciales y período único, contra Postgres y S3 reales.

Usan PDF inventados, creados con pypdfium2, y el proveedor fake. La base se destruye en cada test: la
fixture exige una base descartable por nombre y, si no lo es, saltea. Los objetos de S3 se limitan
al prefijo `prueba-`."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import threading
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from consorcio import cli
from consorcio.almacen import crudos, postgres
from consorcio.canales.telegram import formatear_ingesta, ruta_entrada
from consorcio.config import config
from consorcio.ingesta import esquemas, evaluar, extractores, grafo
from consorcio.ingesta.grafo import carpeta_procesados, hash_archivo, ingestar_pdf, sha256_archivo
from consorcio.ingesta.liquidacion import GastoLiquidacion, LiquidacionExpensas, RubroLiquidacion

BASES_DESCARTABLES = {"consorcio_test"}
X = "prueba-equis"
Y = "prueba-ye"
CERRADO = "http://127.0.0.1:1"  # nadie escucha en ese puerto
ESPERA = 60  # segundos: tope de las sincronizaciones entre hilos


def crear_pdf(ruta: Path, texto: str, paginas: int = 1) -> str:
    """PDF con capa de texto suficiente para que el clasificador no use OCR."""
    pdf = pdfium.PdfDocument.new()
    fuente = pdfium_c.FPDFText_LoadStandardFont(pdf.raw, b"Helvetica")
    for n in range(paginas):
        pagina = pdf.new_page(595, 842)
        for i in range(10):
            obj = pdfium_c.FPDFPageObj_CreateTextObj(pdf.raw, fuente, 10.0)
            linea = f"{texto} pagina {n + 1} linea {i} con texto de relleno para superar el umbral\0"
            buffer = linea.encode("utf-16-le")
            pdfium_c.FPDFText_SetText(obj, ctypes.cast(buffer, pdfium_c.FPDF_WIDESTRING))
            pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 40, 800 - 20 * i)
            pdfium_c.FPDFPage_InsertObject(pagina.raw, obj)
        pagina.gen_content()
    pdf.save(str(ruta))
    pdf.close()
    return str(ruta)


def _hay_postgres() -> bool:
    try:
        with postgres.engine().connect():
            return True
    except Exception:
        return False


def _hay_s3() -> bool:
    try:
        crudos.verificar_bucket()
        return crudos.configurado()
    except crudos.CrudoNoDisponible:
        return False


def _limpiar_s3() -> None:
    cliente = crudos._cliente()
    res = cliente.list_objects_v2(Bucket=config.s3_bucket, Prefix="prueba-")
    for o in res.get("Contents", []):
        cliente.delete_object(Bucket=config.s3_bucket, Key=o["Key"])


def objetos() -> list[str]:
    res = crudos._cliente().list_objects_v2(Bucket=config.s3_bucket, Prefix="prueba-")
    return sorted(o["Key"] for o in res.get("Contents", []))


@pytest.fixture
def base(monkeypatch, tmp_path):
    if not _hay_postgres():
        pytest.skip("sin Postgres")
    nombre = postgres.engine().url.database
    if nombre not in BASES_DESCARTABLES:
        pytest.skip(f"la base {nombre!r} no es descartable: este módulo la destruiría")
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))
    postgres.crear_tablas()
    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")


@pytest.fixture
def s3(base):
    if not _hay_s3():
        pytest.skip("sin S3 configurado y accesible")
    _limpiar_s3()
    yield
    _limpiar_s3()


@pytest.fixture
def sin_s3(monkeypatch):
    monkeypatch.setattr(config, "s3_endpoint_url", "")


@pytest.fixture
def espias(monkeypatch):
    """Cuenta las llamadas a clasificador, modelo y extractores (siguen funcionando)."""
    llamadas: dict[str, int] = {}

    def espiar(modulo, nombre):
        original = getattr(modulo, nombre)

        def espia(*a, **k):
            llamadas[nombre] = llamadas.get(nombre, 0) + 1
            return original(*a, **k)

        monkeypatch.setattr(modulo, nombre, espia)

    espiar(grafo, "clasificar_pdf")
    espiar(grafo, "extraer_metadatos")
    for nombre in ("texto_capa", "renderizar_pagina", "tesseract_pagina", "vision_pagina"):
        espiar(extractores, nombre)
    return llamadas


def con_periodo(monkeypatch, periodo: str) -> None:
    """El proveedor fake no sabe de períodos: se simula la extracción de una liquidación."""
    liq = LiquidacionExpensas(
        periodo=periodo, total_gastos=100.0,
        rubros=[RubroLiquidacion(numero=1, nombre="Limpieza", total=100.0, porcentaje=100.0)],
        gastos=[GastoLiquidacion(rubro="Limpieza", concepto="Artículos", importe=100.0)],
    )
    monkeypatch.setattr(grafo, "es_liquidacion", lambda paginas: True)
    monkeypatch.setattr(grafo, "extraer_liquidacion", lambda paginas, contador: liq)


def documento(documento_id: str) -> dict | None:
    with Session(postgres.engine()) as s:
        d = s.get(postgres.Documento, documento_id)
        return None if d is None else {
            "sha256": d.sha256, "crudo_clave": d.crudo_clave, "total_paginas": d.total_paginas, "costo": d.costo,
            "paginas": len(d.paginas), "consorcio_id": d.consorcio_id, "estado": d.estado,
        }


def contar(modelo, *condiciones) -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(func.count()).select_from(modelo).where(*condiciones))


def locks_advisory() -> int:
    with postgres.engine().connect() as con:
        return con.scalar(text(
            "SELECT count(*) FROM pg_locks l JOIN pg_database d ON l.database = d.oid "
            "WHERE l.locktype = 'advisory' AND d.datname = current_database()"
        ))


def en_hilo(resultados: dict, clave: str, *args, **kwargs) -> threading.Thread:
    def correr():
        try:
            resultados[clave] = ingestar_pdf(*args, **kwargs)
        except Exception as e:  # un fallo en el hilo tiene que hacer fallar el test
            resultados[clave] = e

    hilo = threading.Thread(target=correr)
    hilo.start()
    return hilo


def seguro_para_json(final: dict) -> dict:
    return {k: v for k, v in final.items() if not k.startswith("_")}


# ------------------------------------------------------------------ duplicados


def test_segunda_carga_es_duplicado_sin_ocr_ni_modelo(s3, espias, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta de asamblea")
    primero = ingestar_pdf(pdf, consorcio_id=X)
    assert primero["estado"] == "procesado" and espias["clasificar_pdf"] == 1 and espias["extraer_metadatos"] == 1
    espias.clear()

    final = ingestar_pdf(pdf, consorcio_id=X)
    assert final["estado"] == "duplicado" and espias == {}
    existente = final["documento_existente"]
    assert existente["id"] == primero["documento_id"] and existente["nombre_archivo"] == "acta.pdf"
    assert set(existente) == {"id", "nombre_archivo", "titulo", "creado_en", "estado"}
    assert final["errores"] == [] and final["costo"] == {} and final["consorcio_id"] == X
    assert final["documento_id"] == primero["documento_id"] and final["nombre_archivo"] == "acta.pdf"
    json.dumps(final)
    assert locks_advisory() == 0


def test_forzar_reprocesa_y_queda_un_documento(s3, espias, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    primero = ingestar_pdf(pdf, consorcio_id=X)
    espias.clear()
    final = ingestar_pdf(pdf, consorcio_id=X, forzar=True)
    assert final["estado"] == "procesado" and espias["extraer_metadatos"] == 1
    assert final["documento_id"] == primero["documento_id"]
    assert contar(postgres.Documento) == 1


def test_reusar_texto_no_es_duplicado_ni_vuelve_a_transcribir(s3, espias, tmp_path):
    pdf = crear_pdf(tmp_path / "escaneado.pdf", "escaneado", paginas=2)
    ingestar_pdf(pdf, consorcio_id=X, metodo="vision")
    assert espias["vision_pagina"] == 2
    espias.clear()
    final = ingestar_pdf(pdf, consorcio_id=X, metodo="vision", reusar_texto=True)
    assert final["estado"] == "procesado"
    assert "vision_pagina" not in espias and espias["extraer_metadatos"] == 1
    assert [p["metodo"] for p in final["paginas"]] == ["vision", "vision"]
    assert contar(postgres.Documento) == 1


def test_mismo_pdf_en_dos_consorcios_son_independientes(s3, tmp_path):
    pdf = crear_pdf(tmp_path / "reglamento.pdf", "reglamento")
    sha = sha256_archivo(pdf)
    en_x = ingestar_pdf(pdf, consorcio_id=X)
    en_y = ingestar_pdf(pdf, consorcio_id=Y)
    assert en_y["estado"] == "procesado"
    assert en_x["documento_id"] == f"{X}:{sha[:16]}" and en_y["documento_id"] == f"{Y}:{sha[:16]}"
    assert contar(postgres.Documento) == 2
    assert objetos() == sorted([f"{X}/{sha}.pdf", f"{Y}/{sha}.pdf"])
    assert en_x["persistido"]["carpeta"] != en_y["persistido"]["carpeta"]
    assert Path(en_x["persistido"]["carpeta"]).is_dir() and Path(en_y["persistido"]["carpeta"]).is_dir()

    assert X not in json.dumps(seguro_para_json(en_y), ensure_ascii=False)
    assert X not in formatear_ingesta(en_y)
    # el aviso de duplicado en Y habla solo de Y
    otra_vez = ingestar_pdf(pdf, consorcio_id=Y)
    assert otra_vez["estado"] == "duplicado" and otra_vez["documento_existente"]["id"] == en_y["documento_id"]
    assert X not in json.dumps(otra_vez) and X not in formatear_ingesta(otra_vez)


def test_duplicado_sobre_fila_historica_sin_sha(s3, espias, tmp_path):
    pdf = crear_pdf(tmp_path / "viejo.pdf", "documento historico")
    primero = ingestar_pdf(pdf, consorcio_id=X)
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE documentos SET sha256 = NULL, crudo_clave = NULL"))
    espias.clear()
    final = ingestar_pdf(pdf, consorcio_id=X)
    assert final["estado"] == "duplicado" and espias == {}
    assert final["documento_existente"]["id"] == primero["documento_id"]


def test_sha_y_crudo_coinciden_con_el_archivo(s3, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    bytes_pdf = Path(pdf).read_bytes()
    sha = hashlib.sha256(bytes_pdf).hexdigest()
    final = ingestar_pdf(pdf, consorcio_id=X)
    fila = documento(final["documento_id"])
    assert fila["sha256"] == sha and len(fila["sha256"]) == 64
    assert fila["crudo_clave"] == crudos.clave_crudo(X, sha)
    obj = crudos._cliente().get_object(Bucket=config.s3_bucket, Key=fila["crudo_clave"])
    assert obj["Body"].read() == bytes_pdf


def test_hash_corto_es_el_prefijo_del_sha(tmp_path):
    pdf = crear_pdf(tmp_path / "a.pdf", "a")
    assert hash_archivo(pdf) == sha256_archivo(pdf)[:16]


def test_sin_endpoint_termina_con_warning_y_sin_crudo(base, sin_s3, tmp_path, caplog):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    with caplog.at_level(logging.WARNING):
        final = ingestar_pdf(pdf, consorcio_id=X)
    assert final["estado"] == "procesado" and "S3_ENDPOINT_URL" in caplog.text
    assert documento(final["documento_id"])["crudo_clave"] is None


def test_forzar_sin_bucket_conserva_la_clave_del_crudo(s3, monkeypatch, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    primero = ingestar_pdf(pdf, consorcio_id=X)
    clave = documento(primero["documento_id"])["crudo_clave"]
    with monkeypatch.context() as m:
        m.setattr(config, "s3_endpoint_url", "")
        ingestar_pdf(pdf, consorcio_id=X, forzar=True)
    assert documento(primero["documento_id"])["crudo_clave"] == clave


def test_bucket_caido_frena_antes_de_gastar_y_suelta_el_candado(s3, espias, monkeypatch, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    bueno = config.s3_endpoint_url
    monkeypatch.setattr(config, "s3_endpoint_url", CERRADO)
    with pytest.raises(crudos.CrudoNoDisponible):
        ingestar_pdf(pdf, consorcio_id=X)
    assert espias == {}
    assert contar(postgres.Documento) == 0
    assert locks_advisory() == 0

    monkeypatch.setattr(config, "s3_endpoint_url", bueno)
    assert ingestar_pdf(pdf, consorcio_id=X)["estado"] == "procesado"  # el reintento obtiene el candado


def test_fallo_del_bucket_al_subir_no_deja_fila(s3, monkeypatch, tmp_path):
    def falla(*a, **k):
        raise crudos.CrudoNoDisponible("simulado")

    monkeypatch.setattr(crudos, "subir_crudo", falla)
    with pytest.raises(crudos.CrudoNoDisponible):
        ingestar_pdf(crear_pdf(tmp_path / "acta.pdf", "acta"), consorcio_id=X)
    assert contar(postgres.Documento) == 0 and locks_advisory() == 0


def test_bucket_que_cae_tras_el_preflight_deja_lo_pagado_en_disco(s3, espias, monkeypatch, tmp_path):
    original = crudos.verificar_bucket

    with monkeypatch.context() as m:  # se deshace antes de que la fixture limpie el bucket
        def verificar_y_caer():
            original()
            m.setattr(config, "s3_endpoint_url", CERRADO)

        m.setattr(crudos, "verificar_bucket", verificar_y_caer)
        with pytest.raises(crudos.CrudoNoDisponible):
            ingestar_pdf(crear_pdf(tmp_path / "acta.pdf", "acta de asamblea"), consorcio_id=X)

    assert espias["extraer_metadatos"] == 1
    assert contar(postgres.Documento) == 0 and locks_advisory() == 0
    carpeta = next((tmp_path / "procesados").iterdir())
    assert "acta de asamblea" in (carpeta / "texto.md").read_text(encoding="utf-8")
    assert json.loads((carpeta / "extraccion.json").read_text(encoding="utf-8"))["consorcio_id"] == X


def test_fallo_de_postgres_al_guardar_deja_lo_pagado_en_disco(s3, monkeypatch, tmp_path):
    def falla(**kwargs):
        raise RuntimeError("Postgres caído")

    monkeypatch.setattr(postgres, "guardar_documento", falla)
    with pytest.raises(RuntimeError, match="Postgres caído"):
        ingestar_pdf(crear_pdf(tmp_path / "acta.pdf", "acta"), consorcio_id=X)
    assert (next((tmp_path / "procesados").iterdir()) / "extraccion.json").is_file()


# ------------------------------------------------------------------ candado


def test_dos_cargas_simultaneas_del_mismo_pdf(s3, monkeypatch, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    original = grafo.clasificar_pdf
    adentro, seguir = threading.Event(), threading.Event()

    def clasificar_lento(*a, **k):
        adentro.set()
        assert seguir.wait(ESPERA)
        return original(*a, **k)

    monkeypatch.setattr(grafo, "clasificar_pdf", clasificar_lento)
    resultados: dict = {}
    primero = en_hilo(resultados, "primero", pdf, consorcio_id=X)
    assert adentro.wait(ESPERA)
    segundo = en_hilo(resultados, "segundo", pdf, consorcio_id=X)
    segundo.join(ESPERA)
    assert resultados["segundo"]["estado"] == "en_proceso"
    assert "documento_existente" not in resultados["segundo"]
    seguir.set()
    primero.join(ESPERA)
    assert resultados["primero"]["estado"] == "procesado"

    assert contar(postgres.Documento) == 1 and locks_advisory() == 0
    assert ingestar_pdf(pdf, consorcio_id=X)["estado"] == "duplicado"


def test_mismo_pdf_en_dos_consorcios_a_la_vez(s3, monkeypatch, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    original = grafo.clasificar_pdf
    encuentro = threading.Barrier(2)  # solo pasa si los dos están adentro de su candado a la vez

    def clasificar_junto(*a, **k):
        encuentro.wait(ESPERA)
        return original(*a, **k)

    monkeypatch.setattr(grafo, "clasificar_pdf", clasificar_junto)
    resultados: dict = {}
    hilos = [en_hilo(resultados, c, pdf, consorcio_id=c) for c in (X, Y)]
    for h in hilos:
        h.join(ESPERA)
    assert resultados[X]["estado"] == "procesado" and resultados[Y]["estado"] == "procesado"
    assert contar(postgres.Documento) == 2


def test_no_quedan_candados_tras_cortes_ni_excepciones(s3, monkeypatch, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    ingestar_pdf(pdf, consorcio_id=X)
    assert locks_advisory() == 0
    ingestar_pdf(pdf, consorcio_id=X)  # corte por duplicado
    assert locks_advisory() == 0

    def explota(*a, **k):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(grafo, "clasificar_pdf", explota)
    with pytest.raises(RuntimeError):
        ingestar_pdf(pdf, consorcio_id=X, forzar=True)
    assert locks_advisory() == 0


def test_candado_ocupado_y_liberado(base):
    with postgres.candado_ingesta(X, "abc") as primero, postgres.candado_ingesta(X, "abc") as segundo:
        assert primero is True and segundo is False
        with postgres.candado_ingesta(Y, "abc") as otro_consorcio:
            assert otro_consorcio is True
    assert locks_advisory() == 0
    with postgres.candado_ingesta(X, "abc") as de_nuevo:
        assert de_nuevo is True


# ------------------------------------------------------------------ corridas parciales


def test_corrida_parcial_no_toca_un_documento_completo(s3, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta", paginas=3)
    completo = ingestar_pdf(pdf, consorcio_id=X)
    antes, objetos_antes = documento(completo["documento_id"]), objetos()
    assert antes["paginas"] == 3 and antes["total_paginas"] == 3

    final = ingestar_pdf(pdf, consorcio_id=X, paginas="1")
    assert final["estado"] == "solo_disco"
    assert documento(completo["documento_id"]) == antes and objetos() == objetos_antes
    assert "-pags-1-" in final["persistido"]["carpeta"] and Path(final["persistido"]["carpeta"]).is_dir()
    assert "postgres" not in final["persistido"]


def test_corrida_parcial_de_un_pdf_nuevo_no_crea_fila(s3, tmp_path):
    final = ingestar_pdf(crear_pdf(tmp_path / "nuevo.pdf", "nuevo", paginas=2), consorcio_id=X, paginas="1")
    assert final["estado"] == "solo_disco" and contar(postgres.Documento) == 0 and objetos() == []


def _postgres_inalcanzable(monkeypatch) -> None:
    monkeypatch.setattr(config, "postgres_url", "postgresql+psycopg://qa:qa@127.0.0.1:1/nada")
    monkeypatch.setattr(postgres, "_engine", None)


def test_sin_persistir_funciona_sin_postgres(monkeypatch, tmp_path):
    _postgres_inalcanzable(monkeypatch)
    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")
    final = ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), sin_persistir=True, consorcio_id=X)
    assert final["estado"] == "solo_disco" and Path(final["persistido"]["carpeta"]).is_dir()
    assert final["documento_id"].startswith(f"{X}:")


def test_evaluar_ocr_no_depende_de_postgres_ni_de_la_ingesta(monkeypatch, tmp_path):
    _postgres_inalcanzable(monkeypatch)
    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")

    def prohibida(*a, **k):
        raise AssertionError("evaluar-ocr no debe llamar a ingestar_pdf")

    monkeypatch.setattr(grafo, "ingestar_pdf", prohibida)
    pdf = crear_pdf(tmp_path / "a.pdf", "contenido")
    referencia = tmp_path / "ref.md"
    referencia.write_text("## Página 1\n\ncontenido pagina 1\n", encoding="utf-8")
    res = evaluar.evaluar_transcripcion(pdf, str(referencia))
    assert res["metodo"] == "texto" and res["paginas"] == [1]


def test_carpeta_sin_consorcio_para_evaluar_ocr():
    estado = {"nombre_archivo": "a.pdf", "documento_id": "abcdef0123456789"}
    assert carpeta_procesados(estado).name.startswith("a-abcdef01-")
    assert X in carpeta_procesados({**estado, "consorcio_id": X}).name


# ------------------------------------------------------------------ una liquidación por período


def test_otro_pdf_del_mismo_periodo_no_se_guarda(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    primero = ingestar_pdf(crear_pdf(tmp_path / "julio.pdf", "julio"), consorcio_id=X)
    objetos_antes = objetos()

    final = ingestar_pdf(crear_pdf(tmp_path / "otro.pdf", "julio bis"), consorcio_id=X)
    assert final["estado"] == "periodo_duplicado"
    assert final["documento_existente"]["id"] == primero["documento_id"]
    assert final["documento_existente"]["periodo"] == "2026-07"
    assert contar(postgres.Liquidacion) == 1 and contar(postgres.Documento) == 1
    assert objetos() == objetos_antes
    assert "postgres" not in final["persistido"]
    json.dumps(seguro_para_json(final), default=str)
    assert not formatear_ingesta(final).startswith("Listo:")
    assert "2026-07" in formatear_ingesta(final)


def test_forzar_reemplaza_al_otro_pdf_del_periodo(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    viejo_pdf = crear_pdf(tmp_path / "julio.pdf", "julio")
    viejo = ingestar_pdf(viejo_pdf, consorcio_id=X)
    nuevo_pdf = crear_pdf(tmp_path / "corregida.pdf", "julio corregida")
    nuevo = ingestar_pdf(nuevo_pdf, consorcio_id=X, forzar=True)
    assert nuevo["estado"] == "procesado"
    assert nuevo["persistido"]["postgres"]["reemplazado"]["id"] == viejo["documento_id"]

    assert contar(postgres.Documento) == 1 and contar(postgres.Liquidacion) == 1
    for modelo in (postgres.Pagina, postgres.Seccion, postgres.LlamadaModelo):
        assert contar(modelo, modelo.documento_id == viejo["documento_id"]) == 0
    assert contar(postgres.LiquidacionRubro, postgres.LiquidacionRubro.documento_id == viejo["documento_id"]) == 0
    assert objetos() == [crudos.clave_crudo(X, sha256_archivo(nuevo_pdf))]


def test_reemplazo_con_crudo_que_no_se_puede_borrar_avisa(s3, monkeypatch, tmp_path, caplog):
    con_periodo(monkeypatch, "2026-07")
    ingestar_pdf(crear_pdf(tmp_path / "julio.pdf", "julio"), consorcio_id=X)

    def falla(*a, **k):
        raise RuntimeError("bucket caído")

    real = crudos._cliente

    def cliente_sin_delete():
        cliente = real()
        cliente.delete_object = falla
        return cliente

    with monkeypatch.context() as m, caplog.at_level(logging.WARNING, logger="consorcio.crudos"):
        m.setattr(crudos, "_cliente", cliente_sin_delete)
        final = ingestar_pdf(crear_pdf(tmp_path / "nueva.pdf", "nueva"), consorcio_id=X, forzar=True)
    assert final["estado"] == "procesado" and "no se pudo borrar" in caplog.text
    assert contar(postgres.Documento) == 1


def test_reusar_texto_no_autoriza_a_reemplazar_otro_pdf(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    ingestar_pdf(crear_pdf(tmp_path / "julio.pdf", "julio"), consorcio_id=X)
    final = ingestar_pdf(crear_pdf(tmp_path / "otro.pdf", "otro"), consorcio_id=X, reusar_texto=True)
    assert final["estado"] == "periodo_duplicado" and contar(postgres.Documento) == 1


def test_mismo_periodo_en_otro_consorcio_se_guarda(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    ingestar_pdf(crear_pdf(tmp_path / "x.pdf", "de equis"), consorcio_id=X)
    final = ingestar_pdf(crear_pdf(tmp_path / "y.pdf", "de ye"), consorcio_id=Y)
    assert final["estado"] == "procesado" and contar(postgres.Liquidacion) == 2


@pytest.mark.parametrize("periodo", [
    "", "[fake]", "2026-7", "07-2026", "Julio 2026", "julio/2026", "2026-07-01", "periodo no informado",
])
def test_periodo_invalido_no_dispara_el_control(s3, monkeypatch, tmp_path, periodo):
    con_periodo(monkeypatch, periodo)
    ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), consorcio_id=X)
    final = ingestar_pdf(crear_pdf(tmp_path / "b.pdf", "b"), consorcio_id=X)
    assert final["estado"] == "procesado" and contar(postgres.Liquidacion) == 2
    assert {f["periodo"] for f in postgres.consultar_expensas(consorcio_id=X)} == {periodo}  # tal como vino


def test_reforzar_la_misma_liquidacion_no_es_periodo_duplicado(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    pdf = crear_pdf(tmp_path / "julio.pdf", "julio")
    primero = ingestar_pdf(pdf, consorcio_id=X)
    final = ingestar_pdf(pdf, consorcio_id=X, forzar=True)
    assert final["estado"] == "procesado" and final["documento_id"] == primero["documento_id"]
    assert final["persistido"]["postgres"]["reemplazado"] is None
    assert objetos() == [crudos.clave_crudo(X, sha256_archivo(pdf))]


def test_dos_pdf_del_mismo_periodo_en_paralelo(s3, monkeypatch, tmp_path):
    con_periodo(monkeypatch, "2026-07")
    original = crudos.subir_crudo
    encuentro = threading.Barrier(2)  # los dos pasaron el control previo: decide guardar_documento

    def subir_y_esperar(*a, **k):
        clave = original(*a, **k)
        encuentro.wait(ESPERA)
        return clave

    monkeypatch.setattr(crudos, "subir_crudo", subir_y_esperar)
    pdfs = {c: crear_pdf(tmp_path / f"{c}.pdf", f"julio {c}") for c in ("a", "b")}
    resultados: dict = {}
    hilos = [en_hilo(resultados, c, pdf, consorcio_id=X) for c, pdf in pdfs.items()]
    for h in hilos:
        h.join(ESPERA)

    assert sorted(r["estado"] for r in resultados.values()) == ["periodo_duplicado", "procesado"]
    ganador = next(c for c, r in resultados.items() if r["estado"] == "procesado")
    perdedor = next(c for c in pdfs if c != ganador)
    assert resultados[perdedor]["documento_existente"]["id"] == resultados[ganador]["documento_id"]
    assert contar(postgres.Liquidacion) == 1 and contar(postgres.Documento) == 1
    assert objetos() == [crudos.clave_crudo(X, sha256_archivo(pdfs[ganador]))]  # sin crudo huérfano
    assert locks_advisory() == 0


def test_rechazo_no_borra_el_crudo_de_un_documento_existente(s3, tmp_path):
    pdf = crear_pdf(tmp_path / "acta.pdf", "acta")
    ingestar_pdf(pdf, consorcio_id=X)
    sha = sha256_archivo(pdf)
    entrada = {"consorcio_id": X, "sha256": sha, "documento_id": f"{X}:{sha[:16]}"}
    grafo._borrar_crudo_descartado(entrada)  # `--reusar-texto` que perdió una carrera
    assert objetos() == [crudos.clave_crudo(X, sha)]


def _guardar(documento_id: str, periodo: str | None, **kwargs) -> dict:
    return postgres.guardar_documento(
        documento_id=documento_id, nombre_archivo="a.pdf", ruta="a.pdf",
        clasificacion={"metodo": "texto", "confianza": 1, "total_paginas": 1},
        paginas=[{"numero": 1, "texto": "hola", "metodo": "texto", "confianza": 100}],
        doc=esquemas.DocumentoExtraido(metadatos=esquemas.MetadatosDocumento(titulo="T", resumen="R")),
        costo={}, llamadas=[], segundos_total=0.1,
        liquidacion={"periodo": periodo} if periodo else None, **kwargs,
    )


def test_guardar_documento_es_la_verificacion_autoritativa(base):
    _guardar(f"{X}:a", "2026-07", crudo_clave="clave-a")
    with pytest.raises(postgres.PeriodoDuplicado) as e:
        _guardar(f"{X}:b", "2026-07")
    assert e.value.existente["id"] == f"{X}:a" and contar(postgres.Documento) == 1
    assert locks_advisory() == 0  # el candado de la transacción se fue con el rollback

    res = _guardar(f"{X}:b", "2026-07", reemplazar_periodo=True)
    assert res["reemplazado"] == {"id": f"{X}:a", "crudo_clave": "clave-a", "otros": []}
    assert contar(postgres.Documento) == 1


def test_guardar_documento_reemplaza_duplicados_historicos(base):
    _guardar(f"{X}:a", "2026-07", crudo_clave="clave-a")
    _guardar(f"{X}:b", "2026-08", crudo_clave="clave-b")
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE liquidaciones SET periodo = '2026-07'"))
    res = _guardar(f"{X}:c", "2026-07", reemplazar_periodo=True)
    claves = {res["reemplazado"]["crudo_clave"], *(o["crudo_clave"] for o in res["reemplazado"]["otros"])}
    assert claves == {"clave-a", "clave-b"} and contar(postgres.Liquidacion) == 1


def test_reingestar_conserva_activo(base):
    _guardar(f"{X}:a", None)
    assert postgres.activar_documento(f"{X}:a", activo=False, consorcio_id=X)
    _guardar(f"{X}:a", None)
    with Session(postgres.engine()) as s:
        assert s.get(postgres.Documento, f"{X}:a").activo is False


# ------------------------------------------------------------------ consorcio obligatorio y canales


@pytest.mark.parametrize("consorcio", [None, ""])
def test_ingestar_exige_consorcio_antes_de_leer_el_archivo(consorcio):
    with pytest.raises(ValueError, match="consorcio_id"):
        ingestar_pdf("no-existe.pdf", consorcio_id=consorcio)


def test_ruta_entrada_separa_archivos_homonimos(tmp_path):
    a = ruta_entrada(tmp_path, "AgADuno", "factura.pdf")
    b = ruta_entrada(tmp_path, "AgADdos", "factura.pdf")
    assert a != b and a.name == b.name == "factura.pdf"
    assert a.parent.is_dir() and b.parent.is_dir()


@pytest.mark.parametrize("nombre", ["../../evil.pdf", "..\\..\\evil.pdf", "..", ""])
def test_ruta_entrada_no_sale_de_su_carpeta(tmp_path, nombre):
    ruta = ruta_entrada(tmp_path, "../id/raro", nombre)
    assert ruta.parent.parent == tmp_path and ruta.parent.name == "___id_raro"
    assert ruta.suffix == ".pdf"


def test_formatear_duplicado_nombra_al_documento_existente():
    final = {"estado": "duplicado", "nombre_archivo": "copia.pdf", "errores": [], "costo": {},
             "documento_existente": {"id": f"{X}:a", "nombre_archivo": "acta.pdf", "titulo": "Acta 2024"}}
    texto = formatear_ingesta(final)
    assert not texto.startswith("Listo:") and "acta.pdf" in texto and "Acta 2024" in texto


def test_formatear_en_proceso():
    texto = formatear_ingesta({"estado": "en_proceso", "nombre_archivo": "acta.pdf"})
    assert not texto.startswith("Listo:") and "acta.pdf" in texto


def test_formatear_periodo_duplicado_sin_periodo_conocido():
    texto = formatear_ingesta({"estado": "periodo_duplicado", "nombre_archivo": "b.pdf", "documento_existente": {}})
    assert not texto.startswith("Listo:") and "b.pdf" in texto


@pytest.mark.parametrize("final,salida", [
    ({"estado": "procesado"}, 0), ({"estado": "solo_disco"}, 0), ({"estado": "duplicado"}, 0),
    ({"estado": "en_proceso"}, 1), ({"estado": "periodo_duplicado"}, 1),
    ({"estado": "procesado", "errores": ["x"]}, 1),
])
def test_cli_salida_por_estado_y_forzar(monkeypatch, capsys, final, salida):
    recibido = {}

    def falso(ruta, **kwargs):
        recibido.update(kwargs)
        return final

    monkeypatch.setattr(grafo, "ingestar_pdf", falso)
    assert cli.main(["ingestar", "a.pdf", "--consorcio", X, "--forzar"]) == salida
    assert recibido["forzar"] is True and recibido["consorcio_id"] == X
    assert json.loads(capsys.readouterr().out)["estado"] == final["estado"]


def test_un_documento_que_quedo_sin_secciones_se_reprocesa_al_subirlo_de_nuevo(s3, monkeypatch, tmp_path):
    """Manzanares, 24/11: sin cupo del modelo, `estructurar` falló, el documento se guardó con 0 secciones y
    volver a subirlo daba "duplicado". Ahora queda `incompleto` y la nueva carga lo reprocesa."""
    pdf = crear_pdf(tmp_path / "reglamento.pdf", "reglamento de copropiedad")

    def sin_cupo(*_a, **_k):
        raise RuntimeError("429 RESOURCE_EXHAUSTED")

    original = grafo.estructurar_lotes
    monkeypatch.setattr(grafo, "estructurar_lotes", sin_cupo)
    primero = ingestar_pdf(pdf, consorcio_id=X)
    assert primero["estado"] == "procesado" and primero["errores"]
    assert documento(primero["documento_id"])["estado"] == "incompleto"

    monkeypatch.setattr(grafo, "estructurar_lotes", original)
    otra = ingestar_pdf(pdf, consorcio_id=X)
    assert otra["estado"] == "procesado" and otra["errores"] == []
    assert documento(primero["documento_id"])["estado"] == "extraido"
    assert ingestar_pdf(pdf, consorcio_id=X)["estado"] == "duplicado"  # completo: ya no se reprocesa
