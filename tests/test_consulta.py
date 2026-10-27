"""Tests de la tool 2 (consultas con cita) y de la liquidación de expensas.
Corren sin modelo (fake) y sin bases de datos salvo donde se indica."""
from __future__ import annotations

import os

import pytest

os.environ["LLM_PROVIDER"] = "fake"
os.environ["MLFLOW_TRACKING_URI"] = ""

from consorcio.consulta.buscador import verificar_cita
from consorcio.consulta.grafo import construir_grafo
from consorcio.ingesta.liquidacion import (
    GastoLiquidacion,
    LiquidacionExpensas,
    RubroLiquidacion,
    es_liquidacion,
    parsear_prorrateo,
    secciones_de_rubros,
)
from consorcio.ingesta.skill import cargar_skill, version_skill
from consorcio.texto import normalizar, palabras_clave

FILA_PRORRATEO = (
    "48 2-B CORSICO JOSE $ 276,285.00 $ 284,573.54 $ -8,288.54 $ 8,288.54 1.00% $ 197,470.16 "
    "1.00% $ 40,368.00 1.00% $ 38,472.51 $ 276,310.67 $ 0.33 $ 276,311.00 48"
)
FILA_SIN_REDONDEO = (
    "83 6-G RODRIGUEZ DARIO H $ 594,140.12 $ 594,140.12 $ 17,323.10 0.74% $ 145,500.21 0.74% $ 29,744.00 "
    "0.74% $ 28,347.36 $ 203,591.57 $ 815,054.79 83"
)


def test_skill_consulta_existe_y_tiene_version():
    assert "cita" in cargar_skill("responder", "consulta").lower()
    assert version_skill("consulta") == "0.1.0"
    assert len(cargar_skill("liquidacion")) > 100


def test_grafo_consulta_tiene_los_nodos():
    nodos = set(construir_grafo().get_graph().nodes)
    assert {"buscar", "responder", "verificar", "derivar"} <= nodos


def test_normalizar_y_palabras_clave():
    assert normalizar("¿Se pueden tener MASCOTAS en el edificio?") == "se pueden tener mascotas en el edificio"
    assert palabras_clave("¿Se pueden tener mascotas en el edificio?") == ["tener", "mascotas", "edificio"]


def test_verificar_cita_exige_texto_literal():
    paginas = [{"numero": 1, "texto": "Artículo 5°: Queda prohibida la tenencia de animales que molesten a los vecinos."}]
    ok, _ = verificar_cita("prohibida la tenencia de animales", paginas)
    assert ok
    ok, motivo = verificar_cita("se permiten animales", paginas)
    assert not ok and "no aparece" in motivo
    ok, motivo = verificar_cita("animales", paginas)
    assert not ok and "corta" in motivo
    assert verificar_cita(None, paginas) == (False, "sin cita")


def test_parsear_prorrateo_fila_completa_y_sin_redondeo():
    paginas = [{"numero": 12, "texto": "ESTADO DE CUENTAS Y PRORRATEO\n" + FILA_PRORRATEO + "\n" + FILA_SIN_REDONDEO + "\nTotal $ 1.00 x"}]
    r = parsear_prorrateo(paginas)
    assert r["filas_prorrateo"] == 2 and r["filas_no_parseadas"] == 0
    u = r["unidades"][0]
    assert (u["uf"], u["piso_depto"], u["propietario"]) == (48, "2-B", "CORSICO JOSE")
    assert u["porcentual"] == 1.0 and u["expensas_ordinarias"] == 197470.16 and u["a_pagar"] == 276311.0
    assert u["saldo_anterior"] == 276285.0 and u["importes_previos"][2] == -8288.54
    u2 = r["unidades"][1]
    assert u2["a_pagar"] == 815054.79 and u2["total_expensa"] == 203591.57


FILA_ENERO = (  # sin columna de previsión
    "47 2-A COLLA MARIA EUGENIA $ 243,213.00 $ 243,213.00 1.05% $ 188,524.59 1.05% $ 42,068.00 $ 230,592.59 $ 0.41 $ 230,593.00 47"
)
FILA_AGOSTO = (  # un concepto extra con otro porcentual y gasto particular
    "107 9-G ROMUALDO CARLOS $ 235,379.00 $ 235,379.00 0.85% $ 180,880.88 1.47% $ 3,043.50 0.85% $ 34,388.00 "
    "0.85% $ 36,107.40 $ 254,419.78 $ 38,247.00 $ 0.22 $ 292,667.00 107"
)


def test_parsear_prorrateo_formatos_de_otros_meses():
    r = parsear_prorrateo([{"numero": 12, "texto": "PRORRATEO\n" + FILA_ENERO + "\n" + FILA_AGOSTO}])
    assert r["filas_no_parseadas"] == 0
    ene, ago = r["unidades"]
    assert ene["expensa_extraordinaria"] == 42068.0 and ene["prevision_gastos"] is None and ene["redondeo"] == 0.41
    assert ago["expensas_ordinarias"] == 180880.88 and ago["expensa_extraordinaria"] == 34388.0
    assert ago["prevision_gastos"] == 36107.4 and ago["gasto_particular"] == 38247.0 and ago["redondeo"] == 0.22
    assert ago["otros_conceptos"] == [{"porcentaje": 1.47, "importe": 3043.5}] and ago["a_pagar"] == 292667.0


def test_es_liquidacion_necesita_expensas_vencimiento_y_prorrateo():
    assert es_liquidacion([{"numero": 1, "texto": "MIS EXPENSAS 1° Venc: 22-08-2026"}, {"numero": 2, "texto": "ESTADO DE CUENTAS Y PRORRATEO"}])
    assert not es_liquidacion([{"numero": 1, "texto": "REGLAMENTO DE COPROPIEDAD"}])


def test_secciones_de_rubros_quedan_citables():
    liq = LiquidacionExpensas(
        periodo="2026-07",
        rubros=[RubroLiquidacion(numero=3, nombre="ABONOS Y SERVICIOS", total=2943017.04, porcentaje=14.51)],
        gastos=[GastoLiquidacion(rubro="ABONOS Y SERVICIOS", proveedor="ASECLIM S.R.L.", concepto="limpieza", importe=1631351.04, pagina=3)],
    )
    secs = secciones_de_rubros(liq)
    assert len(secs) == 1 and secs[0].tema == "expensas" and "ASECLIM" in secs[0].resumen
    assert secs[0].pagina_inicio == 3


def _hay_postgres() -> bool:
    try:
        from consorcio.almacen.postgres import engine

        with engine().connect():
            return True
    except Exception:
        return False


@pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")
def test_buscador_palabras_y_tsvector_devuelven_la_misma_forma():
    from consorcio.consulta.buscador import buscar_secciones

    for metodo in ("tsvector", "palabras"):
        res = buscar_secciones("cocheras", 2, metodo)
        for r in res:
            assert {"seccion_id", "documento_id", "nombre_archivo", "titulo", "resumen", "puntaje", "metodo"} <= set(r)
            assert r["metodo"] == metodo
