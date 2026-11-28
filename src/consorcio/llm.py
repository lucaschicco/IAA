"""Capa de acceso al modelo, independiente del proveedor.

- `obtener_llm()` devuelve un chat model de LangChain según LLM_PROVIDER.
- `Contador` acumula llamadas, tokens, costo y segundos por corrida: es la
  "medición común" que pide la práctica y alimenta la sección de costos.
- El proveedor `fake` no llama a ninguna API y permite correr el pipeline
  completo (y los tests) sin clave.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from PIL import Image
from pydantic import BaseModel

from .config import PRECIOS_USD_POR_MILLON, config

log = logging.getLogger("consorcio.llm")


class PresupuestoExcedido(RuntimeError):
    """Se superó el tope de llamadas al modelo para esta corrida."""


@dataclass
class Llamada:
    nodo: str
    modelo: str
    tokens_entrada: int
    tokens_salida: int
    segundos: float
    costo_usd: float | None


@dataclass
class Contador:
    """Acumula el costo de una corrida. Viaja en el estado del grafo. `consorcio_id` solo etiqueta el
    gasto; None en lo que no pertenece a ningún consorcio (evaluar-ocr)."""

    llamadas: list[Llamada] = field(default_factory=list)
    consorcio_id: str | None = None

    def verificar_presupuesto(self) -> None:
        if len(self.llamadas) >= config.presupuesto_llamadas:
            raise PresupuestoExcedido(
                f"Se alcanzó el tope de {config.presupuesto_llamadas} llamadas al modelo"
            )

    def registrar(self, nodo: str, modelo: str, uso: dict | None, segundos: float) -> Llamada:
        uso = uso or {}
        ent = int(uso.get("input_tokens", 0) or 0)
        sal = int(uso.get("output_tokens", 0) or 0)
        llamada = Llamada(nodo, modelo, ent, sal, round(segundos, 3), costo(modelo, ent, sal))
        self.llamadas.append(llamada)
        self._asentar_gasto(llamada)
        return llamada

    def _asentar_gasto(self, llamada: Llamada) -> None:
        """Libro de gastos del tope mensual (ver tope.py). Sin tokens informados no se sabe cuánto costó:
        va NULL, no 0. Un fallo acá avisa pero no frena la respuesta: lo que ya se pagó, se pagó."""
        if llamada.modelo == "fake":
            return
        informado = llamada.tokens_entrada or llamada.tokens_salida
        try:
            from .almacen.postgres import registrar_gasto

            registrar_gasto(self.consorcio_id, llamada.nodo, llamada.modelo, llamada.tokens_entrada,
                            llamada.tokens_salida, llamada.costo_usd if informado else None)
        except Exception as e:
            log.warning("no se pudo asentar el gasto de %s (%s)", llamada.nodo, type(e).__name__)

    def costo_total(self) -> float | None:
        """Sin redondear. None si ninguna llamada tiene precio."""
        costos = [c.costo_usd for c in self.llamadas if c.costo_usd is not None]
        return sum(costos) if costos else None

    def resumen(self) -> dict:
        total = self.costo_total()
        return {
            "llamadas": len(self.llamadas),
            "tokens_entrada": sum(c.tokens_entrada for c in self.llamadas),
            "tokens_salida": sum(c.tokens_salida for c in self.llamadas),
            "segundos_modelo": round(sum(c.segundos for c in self.llamadas), 2),
            "costo_usd": None if total is None else round(total, 4),
        }

    def detalle(self) -> list[dict]:
        return [asdict(c) for c in self.llamadas]


def costo(modelo: str, tokens_entrada: int, tokens_salida: int) -> float | None:
    precio = PRECIOS_USD_POR_MILLON.get(modelo)
    if not precio or precio[0] is None or precio[1] is None:
        return None
    return (tokens_entrada * precio[0] + tokens_salida * precio[1]) / 1_000_000


# --------------------------------------------------------------------------- fake


class _FakeChatModel(BaseChatModel):
    """Modelo de mentira: devuelve respuestas fijas. Para tests y para probar el
    pipeline sin gastar. `with_structured_output` devuelve una instancia "vacía"
    del esquema pedido."""

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        from langchain_core.outputs import ChatGeneration, ChatResult

        ultimo = messages[-1].content if messages else ""
        if _tiene_imagen(ultimo):
            texto = "[fake] transcripción simulada de la página"
        else:
            texto = "[fake] respuesta"
        msg = AIMessage(
            content=texto,
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def with_structured_output(self, schema, **kwargs):  # type: ignore[override]
        return _FakeEstructurado(schema)


class _FakeEstructurado:
    def __init__(self, schema: type[BaseModel]):
        self.schema = schema

    def invoke(self, entrada, **_kwargs):
        return {
            "raw": AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
            "parsed": self._guionado(entrada) or _instancia_vacia(self.schema),
            "parsing_error": None,
        }

    def _guionado(self, entrada) -> BaseModel | None:
        """Con FAKE_GUIONADO, los esquemas que tienen guion contestan según el último mensaje."""
        if not config.fake_guionado:
            return None
        from .agente.guion_fake import GUIONES

        guion = GUIONES.get(self.schema.__name__)
        return guion(_texto_de(entrada[-1].content)) if guion else None


def _instancia_vacia(schema: type[BaseModel]) -> BaseModel:
    datos: dict[str, Any] = {}
    for nombre, campo in schema.model_fields.items():
        if not campo.is_required():
            continue
        anot = campo.annotation
        origen = getattr(anot, "__origin__", None)
        if origen is list or anot is list:
            datos[nombre] = []
        elif anot is str:
            datos[nombre] = "[fake]"
        elif anot in (int, float):
            datos[nombre] = 0
        elif anot is bool:
            datos[nombre] = False
        elif isinstance(anot, type) and issubclass(anot, BaseModel):
            datos[nombre] = _instancia_vacia(anot)
        else:
            datos[nombre] = None
    return schema(**datos)


def _tiene_imagen(contenido: Any) -> bool:
    return isinstance(contenido, list) and any(
        isinstance(b, dict) and b.get("type") in ("image", "image_url") for b in contenido
    )


# --------------------------------------------------------------------------- real


def obtener_llm(modelo: str | None = None, max_tokens: int | None = None) -> BaseChatModel:
    """Devuelve el chat model configurado. `modelo` permite pedir otro (p. ej. visión);
    `max_tokens` acota la salida (por defecto `max_tokens_salida` de la config): es el freno
    a los bucles de repetición, que sin tope cuestan cientos de miles de tokens."""
    proveedor = config.llm_provider
    modelo = modelo or config.modelo
    if proveedor == "fake":
        return _FakeChatModel()

    from langchain.chat_models import init_chat_model

    extra: dict[str, Any] = {"max_tokens": max_tokens or config.max_tokens_salida}
    if proveedor == "google_genai":
        if config.google_api_key:
            extra["google_api_key"] = config.google_api_key
        extra["max_output_tokens"] = extra.pop("max_tokens")
        # Con las claves de AI Studio que empiezan con `AQ.` la biblioteca llama a `v1`, que rechaza las
        # instrucciones de sistema ("Developer instruction is not enabled for api version v1")
        extra["api_version"] = "v1beta"
    elif proveedor == "anthropic":
        if config.anthropic_api_key:
            extra["api_key"] = config.anthropic_api_key
    elif proveedor == "openai":
        if config.openai_api_key:
            extra["api_key"] = config.openai_api_key
    elif proveedor in ("openrouter", "ollama"):
        # Ambos exponen una API compatible con OpenAI; cambia la URL base y la clave
        extra["base_url"] = config.openrouter_base_url if proveedor == "openrouter" else config.ollama_base_url
        extra["api_key"] = config.openrouter_api_key if proveedor == "openrouter" else config.ollama_api_key
        proveedor = "openai"
    else:
        raise ValueError(f"LLM_PROVIDER desconocido: {proveedor!r}")
    return init_chat_model(modelo, model_provider=proveedor, **extra)


def imagen_a_bloque(imagen: Image.Image) -> dict:
    """Convierte una imagen PIL en un bloque multimodal (data URL) que LangChain
    traduce al formato de cada proveedor."""
    buf = io.BytesIO()
    imagen.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}


def invocar_texto(
    contador: Contador,
    nodo: str,
    sistema: str,
    usuario: str | list,
    modelo: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """Una llamada de texto (o multimodal si `usuario` es una lista de bloques)."""
    contador.verificar_presupuesto()
    modelo = modelo or config.modelo
    llm = obtener_llm(modelo, max_tokens)
    t0 = time.perf_counter()
    resp = llm.invoke([SystemMessage(content=sistema), HumanMessage(content=usuario)])
    contador.registrar(nodo, modelo, getattr(resp, "usage_metadata", None), time.perf_counter() - t0)
    return _texto_de(resp.content)


def invocar_estructurado[T: BaseModel](
    contador: Contador,
    nodo: str,
    sistema: str,
    usuario: str,
    esquema: type[T],
    modelo: str | None = None,
) -> T:
    """Una llamada con salida estructurada (pydantic), independiente del proveedor.

    Los proveedores con API compatible con OpenAI pero sin tool calling confiable
    (Ollama cloud, modelos abiertos en OpenRouter) devuelven prosa cuando se les pide
    una herramienta: para ellos se pide el JSON en el prompt y se valida acá
    (decisión 26/10/2026, al agotarse las cuotas gratuitas de Gemini).
    """
    contador.verificar_presupuesto()
    modelo = modelo or config.modelo
    if config.llm_provider in ESTRUCTURADO_POR_PROMPT:
        return _estructurado_por_prompt(contador, nodo, sistema, usuario, esquema, modelo)
    llm = obtener_llm(modelo).with_structured_output(esquema, include_raw=True)
    t0 = time.perf_counter()
    resp = llm.invoke([SystemMessage(content=sistema), HumanMessage(content=usuario)])
    segundos = time.perf_counter() - t0
    raw = resp.get("raw") if isinstance(resp, dict) else None
    contador.registrar(nodo, modelo, getattr(raw, "usage_metadata", None), segundos)
    parsed = resp.get("parsed") if isinstance(resp, dict) else resp
    if parsed is None:
        parsed = _reparar_listas_como_texto(raw, esquema)
    if parsed is None:
        raise ValueError(
            f"El modelo no devolvió una salida válida para {esquema.__name__}: "
            f"{resp.get('parsing_error') if isinstance(resp, dict) else 'sin detalle'}"
        )
    return parsed


ESTRUCTURADO_POR_PROMPT = {"ollama", "openrouter"}


def _estructurado_por_prompt[T: BaseModel](
    contador: Contador, nodo: str, sistema: str, usuario: str, esquema: type[T], modelo: str
) -> T:
    import json

    instrucciones = (
        "\n\nFORMATO DE SALIDA: respondé únicamente con un objeto JSON válido que cumpla este "
        "JSON Schema. Sin texto antes ni después, sin bloque de código markdown, sin comentarios. "
        "Los campos opcionales que no figuren van en null; las listas vacías, como [].\n"
        + json.dumps(esquema.model_json_schema(), ensure_ascii=False)
    )
    crudo = invocar_texto(contador, nodo, sistema + instrucciones, usuario, modelo)
    try:
        # strict=False tolera saltos de línea y tabs crudos dentro de las cadenas (gpt-oss los manda)
        return esquema.model_validate(json.loads(_extraer_json(crudo), strict=False))
    except Exception as e:
        raise ValueError(
            f"El modelo no devolvió un JSON válido para {esquema.__name__}: {e}. Inicio de la respuesta: {crudo[:200]!r}"
        ) from e


def _extraer_json(texto: str) -> str:
    """Tolera un bloque ```json ... ``` o texto alrededor: se queda con el primer objeto {...}."""
    t = texto.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        t = t.rsplit("```", 1)[0]
    ini, fin = t.find("{"), t.rfind("}")
    return t[ini : fin + 1] if ini != -1 and fin != -1 else t


def _texto_de(contenido: Any) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for b in contenido:
            if isinstance(b, str):
                partes.append(b)
            elif isinstance(b, dict) and b.get("type") == "text":
                partes.append(b.get("text", ""))
        return "\n".join(partes)
    return str(contenido)


def _reparar_listas_como_texto[T: BaseModel](raw: Any, esquema: type[T]) -> T | None:
    """Haiku 5.5 a veces manda un campo lista como texto JSON (`"personas": "[{...}]"`, Manzanares 25/11):
    se decodifica ese texto y se valida de nuevo. Si igual no valida, sigue el error original."""
    llamadas = getattr(raw, "tool_calls", None) or []
    if not llamadas:
        return None
    args = dict(llamadas[0].get("args") or {})
    for k, v in args.items():
        campo = esquema.model_fields.get(k)
        if campo and campo.annotation is not str and isinstance(v, str) and v.lstrip()[:1] in ("[", "{"):
            try:
                args[k] = json.loads(v)
            except ValueError:
                return None
    try:
        return esquema.model_validate(args)
    except ValueError:  # ValidationError
        return None
