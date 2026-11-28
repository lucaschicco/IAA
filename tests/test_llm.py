"""Salida estructurada que llega mal armada desde el proveedor."""
from langchain_core.messages import AIMessage
from pydantic import BaseModel

from consorcio.llm import _reparar_listas_como_texto


class Esquema(BaseModel):
    titulo: str
    personas: list[dict] = []


def _raw(args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "Esquema", "args": args, "id": "1"}])


def test_una_lista_mandada_como_texto_json_se_repara():
    """Haiku 5.5 mandó `personas` como texto JSON y el acta del 12/12/2023 quedó sin secciones (25/11)."""
    r = _reparar_listas_como_texto(_raw({"titulo": "[Acta]", "personas": '[{"nombre": "X"}]'}), Esquema)
    assert r == Esquema(titulo="[Acta]", personas=[{"nombre": "X"}])  # un texto que no es JSON queda como texto


def test_lo_que_no_se_puede_reparar_sigue_fallando():
    assert _reparar_listas_como_texto(_raw({"titulo": "a", "personas": "[roto"}), Esquema) is None
    assert _reparar_listas_como_texto(_raw({"personas": []}), Esquema) is None
    assert _reparar_listas_como_texto(AIMessage(content="hola"), Esquema) is None
