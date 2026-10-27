"""Tests del canal de Telegram: las funciones puras (autorización, parseo de /expensas y formateo
de respuestas) corren sin red, sin bot y sin bases de datos."""
from __future__ import annotations

import os

os.environ["LLM_PROVIDER"] = "fake"
os.environ["MLFLOW_TRACKING_URI"] = ""

from consorcio.canales.telegram import (
    LIMITE_MENSAJE,
    chat_autorizado,
    chats_permitidos,
    formatear_expensas,
    formatear_ingesta,
    formatear_respuesta,
    parsear_expensas,
)
from consorcio.texto import consorcio_de_chat_id, hash_de, id_documento


def test_cada_chat_tiene_su_base_y_el_id_lleva_el_consorcio():
    assert consorcio_de_chat_id(1976380707) == "chat-1976380707"
    assert consorcio_de_chat_id(-1001234) == "grupo-1001234"
    assert id_documento("chat-1976380707", "2009af49436d1da9") == "chat-1976380707:2009af49436d1da9"
    assert hash_de("mercedes-274:2009af49436d1da9") == "2009af49436d1da9"
    assert hash_de("2009af49436d1da9") == "2009af49436d1da9"  # id anterior a la migración


def test_lista_blanca_vacia_permite_todo_y_con_valores_restringe():
    assert chats_permitidos("") == set()
    assert chats_permitidos("123, -4567 89") == {123, -4567, 89}
    assert chat_autorizado(5, set())
    assert chat_autorizado(123, {123})
    assert not chat_autorizado(5, {123})


def test_parsear_expensas_en_cualquier_orden():
    assert parsear_expensas([]) == {"que": "resumen", "periodo": None, "unidad": None, "texto": None}
    assert parsear_expensas(["rubros", "2026-07"]) == {"que": "rubros", "periodo": "2026-07", "unidad": None, "texto": None}
    assert parsear_expensas(["2026-07", "5-d"]) == {"que": "unidades", "periodo": "2026-07", "unidad": "5-D", "texto": None}
    assert parsear_expensas(["limpieza", "general"]) == {"que": "gastos", "periodo": None, "unidad": None, "texto": "limpieza general"}


def test_formatear_respuesta_con_cita_y_derivada():
    final = {
        "mensaje": "Sí, se puede.\n\nFuente: ... Cita: ...", "derivada": False, "verificada": True,
        "respuesta": {"respuesta": "Sí, se puede.", "cita": "queda permitido", "pagina": 2},
        "fuente": {"nombre_archivo": "REGLAMENTO.pdf", "titulo": "Artículo 3"},
    }
    texto = formatear_respuesta(final)
    assert texto.startswith("Sí, se puede.\n\n«queda permitido»")
    assert "Fuente: REGLAMENTO.pdf · Artículo 3 · pág. 2" in texto and "verificada" in texto and "Cita: ..." not in texto
    derivada = formatear_respuesta({"mensaje": "Derivo la consulta a la administración.", "derivada": True})
    assert derivada == "Derivo la consulta a la administración."


def test_formatear_ingesta_resume_y_recorta():
    final = {
        "nombre_archivo": "acta.pdf", "clasificacion": {"metodo": "texto", "motivo": "capa de texto"},
        "extraccion": {"metadatos": {"titulo": "Acta", "tipo_documento": "acta", "fecha_documento": "2024-07-25"},
                       "secciones": [1, 2], "personas": [1], "eventos": [], "montos": []},
        "costo": {"llamadas": 2, "tokens_entrada": 100, "tokens_salida": 50, "costo_usd": None},
        "segundos_total": 3.2, "persistido": True, "errores": ["x" * 5000],
    }
    texto = formatear_ingesta(final)
    assert texto.startswith("Listo: acta.pdf") and "Secciones: 2" in texto and "sin precio" in texto
    assert len(texto) <= LIMITE_MENSAJE and texto.endswith("[… recortado]")


def test_formatear_expensas():
    assert "No hay datos" in formatear_expensas("rubros", [])
    filas = [{"periodo": "2026-07", "numero": 1, "rubro": "Mantenimiento", "total": 1234567.5, "porcentaje": 42.7}]
    assert "Mantenimiento: $ 1.234.567,50 (42,7 %)".replace("42,7", "42.7") in formatear_expensas("rubros", filas)
