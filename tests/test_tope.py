"""Tope de gasto y libro `gastos_modelo`, con precios simulados: nunca contra una factura real.

La base se destruye al empezar el módulo y se vacía en cada test. Los PDF son inventados."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_duplicados import crear_pdf

from consorcio import cli, tope
from consorcio.agente import grafo as grafo_agente
from consorcio.agente.esquemas import SalidaRouter
from consorcio.agente.grafo import atender
from consorcio.agente.semilla import sembrar_demo
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Carga, GastoModelo, Solicitud, Turno
from consorcio.canales.formato import formatear_ingesta
from consorcio.config import config
from consorcio.consulta.grafo import preguntar
from consorcio.identidad import resolver_identidad
from consorcio.ingesta import grafo as grafo_ingesta
from consorcio.ingesta.grafo import ingestar_pdf
from consorcio.llm import Contador, invocar_estructurado

BASES_DESCARTABLES = {"consorcio_test"}
X = "prueba-tope"
PRECIO = "claude-haiku-5-5"  # 0,10 y 0,50 USD por millón de tokens


@pytest.fixture(scope="module")
def esquema():
    if postgres.engine().url.database not in BASES_DESCARTABLES:
        raise RuntimeError("la base configurada no es descartable: este módulo la destruye")
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))
    postgres.crear_tablas()


@pytest.fixture
def base(esquema, monkeypatch, tmp_path):
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE consorcios, usuarios, gastos_modelo, cargas, consultas CASCADE"))
    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")
    monkeypatch.setattr(config, "s3_endpoint_url", "")
    monkeypatch.setattr(config, "tope_usd_mensual", 1.0)


def gastar(costo: float | None, creado_en: datetime | None = None, modelo: str = PRECIO, tokens: int = 100) -> None:
    with Session(postgres.engine()) as s:
        s.add(GastoModelo(consorcio_id=X, nodo="n", modelo=modelo, tokens_entrada=tokens, tokens_salida=tokens,
                          costo_usd=costo, **({"creado_en": creado_en} if creado_en else {})))
        s.commit()


def filas_gasto() -> list[GastoModelo]:
    with Session(postgres.engine()) as s:
        return list(s.scalars(select(GastoModelo).order_by(GastoModelo.id)).all())


def contar(modelo) -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(func.count()).select_from(modelo))


# ------------------------------------------------------------------ libro


def test_contador_asienta_con_consorcio_y_costo(base):
    Contador(consorcio_id=X).registrar("router", PRECIO, {"input_tokens": 1_000_000, "output_tokens": 1_000_000}, 1.0)
    f = filas_gasto()[0]
    assert (f.consorcio_id, f.nodo, f.modelo, f.tokens_entrada, f.tokens_salida) == (X, "router", PRECIO, 1_000_000, 1_000_000)
    assert f.costo_usd == pytest.approx(0.60)


def test_el_fake_no_asienta(base):
    Contador(consorcio_id=X).registrar("router", "fake", {"input_tokens": 10, "output_tokens": 5}, 0.0)
    assert filas_gasto() == []


@pytest.mark.parametrize("uso", [None, {}, {"input_tokens": 0, "output_tokens": 0}], ids=["none", "vacio", "ceros"])
def test_sin_uso_informado_el_costo_es_null_y_no_cero(base, uso):
    Contador().registrar("router", PRECIO, uso, 0.0)
    f = filas_gasto()[0]
    assert f.costo_usd is None and f.consorcio_id is None


def test_fallo_del_libro_no_frena_la_llamada(monkeypatch, caplog):
    monkeypatch.setattr(config, "postgres_url", "postgresql+psycopg://qa:qa@127.0.0.1:1/nada")
    monkeypatch.setattr(postgres, "_engine", None)
    contador = Contador(consorcio_id=X)
    llamada = contador.registrar("router", PRECIO, {"input_tokens": 1, "output_tokens": 1}, 0.0)
    assert llamada.nodo == "router" and len(contador.llamadas) == 1
    assert "no se pudo asentar el gasto" in caplog.text


def test_invocar_estructurado_pasa_por_el_libro(base, monkeypatch):
    monkeypatch.setattr(config, "llm_model", PRECIO)  # el proveedor sigue siendo fake: tokens y precio simulados
    monkeypatch.setattr(config, "fake_guionado", True)
    invocar_estructurado(Contador(consorcio_id=X), "router", "s", "<mensaje>hola</mensaje>", SalidaRouter)
    f = filas_gasto()[0]
    assert (f.consorcio_id, f.nodo, f.tokens_entrada, f.tokens_salida) == (X, "router", 10, 5)
    assert f.costo_usd == pytest.approx(0.0000035)


# ------------------------------------------------------------------ mes


def test_gasto_del_mes_suma_solo_el_mes_calendario_utc(base):
    inicio, siguiente = tope._limites_del_mes()
    gastar(0.25, inicio)
    gastar(0.25)
    gastar(5.0, inicio - timedelta(seconds=1))
    gastar(7.0, siguiente)
    assert tope.gasto_del_mes() == pytest.approx(0.5)


def test_limites_del_mes_cruzan_el_anio():
    inicio, siguiente = tope._limites_del_mes(datetime(2026, 12, 31, 23, 59, tzinfo=UTC))
    assert inicio == datetime(2026, 12, 1, tzinfo=UTC) and siguiente == datetime(2027, 1, 1, tzinfo=UTC)


def test_sin_gastos_es_cero(base):
    assert tope.gasto_del_mes() == 0.0 and tope.tope_excedido() is False


def test_estado_tope(base):
    gastar(0.25)
    gastar(None, modelo="sin-precio", tokens=100)
    gastar(None, modelo="sin-precio", tokens=50)
    e = tope.estado_tope()
    assert (e["tope_usd"], e["gastado_usd"], e["disponible_usd"], e["excedido"]) == (1.0, 0.25, 0.75, False)
    assert e["mes"] == datetime.now(UTC).strftime("%Y-%m")
    assert e["modelos_sin_precio"] == [{"modelo": "sin-precio", "llamadas": 2, "tokens": 300}]


def test_modelo_sin_precio_no_suma(base):
    Contador(consorcio_id=X).registrar("router", "modelo-nuevo", {"input_tokens": 9_000_000, "output_tokens": 9_000_000}, 1.0)
    assert tope.gasto_del_mes() == 0.0
    assert [m["modelo"] for m in tope.estado_tope()["modelos_sin_precio"]] == ["modelo-nuevo"]


def test_exacto_en_el_tope_ya_bloquea(base):
    gastar(1.0)
    assert tope.tope_excedido() and tope.estado_tope()["disponible_usd"] == 0.0


def test_tope_cero_no_bloquea(base, monkeypatch):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    gastar(1_000.0)
    assert tope.tope_excedido() is False
    e = tope.estado_tope()
    assert e["excedido"] is False and e["disponible_usd"] is None


def test_cli_gasto_imprime_el_estado(base, capsys):
    gastar(0.25)
    assert cli.main(["gasto"]) == 0
    assert json.loads(capsys.readouterr().out)["gastado_usd"] == 0.25


# ------------------------------------------------------------------ atender


@pytest.fixture
def demo(base):
    sembrar_demo()


def test_tope_alcanzado_no_llama_al_modelo(demo, monkeypatch):
    def prohibida(*a, **k):
        raise AssertionError("no debía llamar al modelo")

    monkeypatch.setattr(grafo_agente, "invocar_estructurado", prohibida)
    gastar(1.0)
    r = atender("hola", resolver_identidad("demo-prop-palmeras", "demo-palmeras"), None, "web")
    assert r["resultado"] == "tope_excedido" and r["solicitud_id"] is None
    assert contar(Solicitud) == 0
    with Session(postgres.engine()) as s:
        t = s.get(Turno, r["turno_id"])
    assert (t.resultado, t.tokens_entrada, t.intencion) == ("tope_excedido", 0, None)


def test_tope_cero_deja_pasar_a_atender(demo, monkeypatch):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    gastar(1_000.0)
    monkeypatch.setattr(grafo_agente, "invocar_estructurado", lambda *a, **k: SalidaRouter(intencion="fuera_de_alcance"))
    r = atender("hola", resolver_identidad("demo-prop-palmeras", "demo-palmeras"), None, "web")
    assert r["resultado"] == "fuera_de_alcance"


def test_preguntar_cuenta_y_no_bloquea(demo, monkeypatch):
    gastar(5.0)
    monkeypatch.setattr(config, "fake_guionado", True)
    final = preguntar("¿Se permiten mascotas?", origen="telegram", consorcio_id="demo-palmeras")
    assert final["verificada"]


# ------------------------------------------------------------------ ingesta


def test_ingesta_de_pdf_nuevo_corta_sin_extraer(base, monkeypatch, tmp_path):
    gastar(1.0)

    def prohibido(*a, **k):
        raise AssertionError("no debía clasificar")

    monkeypatch.setattr(grafo_ingesta, "clasificar_pdf", prohibido)
    final = ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), consorcio_id=X, usuario_id=None)
    assert final["estado"] == "tope_excedido" and contar(postgres.Documento) == 0
    assert "tope mensual" in formatear_ingesta(final) and "Listo" not in formatear_ingesta(final)
    with Session(postgres.engine()) as s:
        assert s.scalars(select(Carga.estado)).all() == ["tope_excedido"]


def test_duplicado_sigue_contestando_con_el_tope_alcanzado(base, tmp_path):
    pdf = crear_pdf(tmp_path / "a.pdf", "a")
    assert ingestar_pdf(pdf, consorcio_id=X)["estado"] == "procesado"
    gastar(1.0)
    assert ingestar_pdf(pdf, consorcio_id=X)["estado"] == "duplicado"


def test_forzar_tambien_respeta_el_tope(base, tmp_path):
    pdf = crear_pdf(tmp_path / "a.pdf", "a")
    ingestar_pdf(pdf, consorcio_id=X)
    gastar(1.0)
    assert ingestar_pdf(pdf, consorcio_id=X, forzar=True)["estado"] == "tope_excedido"


def test_solo_disco_respeta_el_tope_si_puede_leerlo(base, tmp_path):
    gastar(1.0)
    final = ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), sin_persistir=True, consorcio_id=X)
    assert final["estado"] == "tope_excedido" and contar(Carga) == 0


def test_tope_cero_deja_ingestar(base, monkeypatch, tmp_path):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    gastar(1_000.0)
    assert ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), consorcio_id=X)["estado"] == "procesado"


def test_forzar_no_reduce_el_gasto(base, monkeypatch, tmp_path):
    """El libro no cuelga de `documentos`: reprocesar borra `llamadas_modelo`, no lo pagado."""
    monkeypatch.setattr(config, "llm_model", PRECIO)  # fake con precio: 10 + 5 tokens por llamada
    pdf = crear_pdf(tmp_path / "a.pdf", "a")
    ingestar_pdf(pdf, consorcio_id=X)
    primero = tope.gasto_del_mes()
    assert primero > 0

    ingestar_pdf(pdf, consorcio_id=X, forzar=True)

    assert tope.gasto_del_mes() == pytest.approx(primero * 2)
    assert all(f.consorcio_id == X and f.modelo == PRECIO for f in filas_gasto())


def test_cargas_registra_usuario_y_estado(base, tmp_path):
    pdf = crear_pdf(tmp_path / "a.pdf", "a")
    ingestar_pdf(pdf, consorcio_id=X, usuario_id=7)
    ingestar_pdf(pdf, consorcio_id=X, usuario_id=7)
    with Session(postgres.engine()) as s:
        filas = s.execute(select(Carga.consorcio_id, Carga.usuario_id, Carga.estado).order_by(Carga.id)).all()
    assert [tuple(f) for f in filas] == [(X, 7, "procesado"), (X, 7, "duplicado")]


def test_solo_disco_no_escribe_cargas(base, tmp_path):
    ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), sin_persistir=True, consorcio_id=X)
    assert contar(Carga) == 0


def test_fallo_de_cargas_no_afecta_la_ingesta(base, monkeypatch, tmp_path, caplog):
    def explota(*a, **k):
        raise RuntimeError("sin base")

    monkeypatch.setattr(postgres, "registrar_carga", explota)
    assert ingestar_pdf(crear_pdf(tmp_path / "a.pdf", "a"), consorcio_id=X)["estado"] == "procesado"
    assert "no se pudo registrar la carga" in caplog.text


def test_cli_ingestar_devuelve_1_con_el_tope_alcanzado(base, tmp_path, capsys):
    gastar(1.0)
    assert cli.main(["ingestar", crear_pdf(tmp_path / "a.pdf", "a"), "--consorcio", X]) == 1
    assert '"estado": "tope_excedido"' in capsys.readouterr().out
