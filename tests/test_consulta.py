"""Tests de la tool 2 (consultas con cita) y de la liquidación de expensas.
Corren sin modelo (fake) y sin bases de datos salvo donde se indica."""
from __future__ import annotations

import os

import pytest

os.environ["LLM_PROVIDER"] = "fake"
os.environ["MLFLOW_TRACKING_URI"] = ""

from consorcio.consulta import buscador
from consorcio.consulta import grafo as grafo_consulta
from consorcio.consulta.buscador import pagina_de_cita, verificar_cita, verificar_respuesta
from consorcio.consulta.evaluar import evaluar_generacion
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
    assert "cita" in cargar_skill("extraer", "consulta").lower() and "citas" in cargar_skill("redactar", "consulta").lower()
    assert version_skill("consulta") == "0.3.0"
    assert len(cargar_skill("liquidacion")) > 100


def test_grafo_consulta_tiene_los_nodos():
    nodos = set(construir_grafo().get_graph().nodes)
    assert {"buscar", "extraer", "verificar", "redactar", "derivar"} <= nodos


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


FUENTE = "Artículo 4. La multa es de $ 1.500,00 y el pago vence a los 10 días de la asamblea de propietarios."


@pytest.mark.parametrize("respuesta", [
    "La multa es de $ 1.500,00.", "La multa es de $ 1.500.", "Es de 1500 pesos.", "Vence a los 10 días.",
    "Según el artículo 4, la multa vence a los 10 días de la asamblea.",
])
def test_respuesta_con_respaldo_pasa(respuesta):
    assert verificar_respuesta(respuesta, "La multa es de $ 1.500,00", FUENTE)[0]


@pytest.mark.parametrize("respuesta", [
    "La multa es de $ 2.500,00.", "La multa es de $ 1.000.", "La multa es de 15.", "Vence a los 11 días.",
    "Podés tener un tigre: el reglamento lo autoriza.",
])
def test_respuesta_sin_respaldo_no_pasa(respuesta):
    ok, motivo = verificar_respuesta(respuesta, "La multa es de $ 1.500,00", FUENTE)
    assert not ok and motivo.startswith("respuesta sin respaldo")


def test_la_evaluacion_mide_lo_mismo_que_ve_el_vecino(monkeypatch):
    pedidos: list[dict] = []

    def preguntar(_pregunta, **opciones):
        pedidos.append(opciones)
        return {"derivada": True}

    monkeypatch.setattr(grafo_consulta, "preguntar", preguntar)
    evaluar_generacion([{"id": "1", "pregunta": "¿Se puede?", "documento": "x", "seccion_clave": ""}], consorcio_id="c")
    assert pedidos[0]["validar_respuesta"] is True


def test_un_miles_no_se_confunde_con_centavos():
    assert not verificar_respuesta("Son $ 2.000.", "Son $ 2,00 por unidad", "Son $ 2,00 por unidad")[0]


CITA_MULTA = "La multa es de $ 1.500,00"


@pytest.mark.parametrize("respuesta", [
    "Son $ 150.050.", "Son $ 15,05.", "Son 15 M.", "Son 15k.", "Son 15 mil pesos.", "Son USD 1.500.",
    "La multa es de dos millones de pesos.", "La multa es de 10 pesos.",
])
def test_monto_que_no_esta_en_la_cita_no_pasa(respuesta):
    assert not verificar_respuesta(respuesta, CITA_MULTA, FUENTE + " Hay 15 pesos de sello y 15,5 de otra cosa.")[0]


def test_monto_en_letras_dentro_de_una_respuesta_larga_no_pasa():
    fuente = "Se permite la tenencia de mascotas pequeñas en las unidades, siempre que no molesten a los vecinos."
    respuesta = f"{fuente} La multa es de dos millones de pesos."
    assert not verificar_respuesta(respuesta, fuente, fuente)[0]


def test_escala_y_letras_respaldadas_pasan():
    cita = "La cuota es de $ 15.000 o quince mil pesos, por dos cocheras."
    for respuesta in ("La cuota es de 15 mil pesos.", "La cuota es de quince mil pesos.", "Son 2 cocheras.", "Son dos cocheras."):
        assert verificar_respuesta(respuesta, cita, cita)[0], respuesta


@pytest.mark.parametrize("a, b", [("150.050", "1.500,50"), ("12,5", "125"), ("15,05", "1505"), ("15,0", "150"), ("1,500", "1.500")])
def test_numeros_distintos_no_colisionan(a, b):
    assert buscador._numeros(a) != buscador._numeros(b)


def test_pagina_de_cita_sale_del_texto():
    paginas = [{"numero": 7, "texto": "Primer texto de la página."}, {"numero": 8, "texto": "La cita buscada está acá."}]
    assert pagina_de_cita("la cita buscada", paginas, 777) == 8
    assert pagina_de_cita("texto de la pagina. La cita", paginas, 7) == 7  # cruza páginas: vale la declarada
    assert pagina_de_cita("texto de la pagina. La cita", paginas, 777) is None
    assert pagina_de_cita("texto de la pagina. La cita", paginas, None) is None


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
        res = buscar_secciones("cocheras", 2, metodo, consorcio_id="prueba")
        for r in res:
            assert {"seccion_id", "documento_id", "nombre_archivo", "titulo", "resumen", "puntaje", "metodo"} <= set(r)
            assert r["metodo"] == metodo


def test_citas_extraidas_sin_puede_responder_se_acepta():
    """Haiku 5.5 omitía `puede_responder` al traer citas y la consulta fallaba con ValueError (25/11)."""
    from consorcio.consulta.esquemas import CitasExtraidas

    e = CitasExtraidas.model_validate({"citas": [{"fuente": 1, "pagina": 2, "cita": "una cita textual de prueba"}]})
    assert e.puede_responder is True and len(e.citas) == 1
    assert CitasExtraidas.model_validate({"puede_responder": False}).citas == []


def test_la_respuesta_puede_retomar_la_pregunta_y_hablar_de_las_citas():
    """Manzanares, 25/11: respuestas correctas derivaban por palabras de la pregunta y por "las citas no muestran"."""
    fuente = "La asamblea se convoca para las 19:30 hs en el hall de entrada."
    respuesta = "Las citas no muestran a qué hora empezó la asamblea: estaba convocada a las 19:30 en el hall."
    assert not verificar_respuesta(respuesta, fuente, fuente)[0]
    assert verificar_respuesta(respuesta, fuente, fuente, "¿A qué hora empezó la asamblea? ¿estaba convocada?")[0]
    # los números siguen teniendo que estar en las fuentes, aunque los diga la pregunta
    assert not verificar_respuesta("Empezó a las 20:15 en el hall.", fuente, fuente, "¿Empezó a las 20:15?")[0]
