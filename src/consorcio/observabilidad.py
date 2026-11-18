"""Trazabilidad con MLflow (práctica, diseño 8). Si MLFLOW_TRACKING_URI está
vacío, todo es no-op y el sistema corre igual.

Por corrida se abre un run con etiquetas (nodo raíz, archivo, versión del skill,
decisión del clasificador) y se registran métricas: llamadas, tokens, costo,
segundos, y el conteo de entidades extraídas.

MLflow es opcional y nunca debe demorar a quien espera una respuesta: con sus valores
por defecto (120 s y 7 reintentos por pedido), un servidor caído frenó un turno del chat
más de 9 minutos. Por eso se sondea el puerto antes y, si no contesta, no se lo vuelve a
intentar durante `_PAUSA_TRAS_FALLO`.
"""
from __future__ import annotations

import contextlib
import logging
import os
import socket
import time
from typing import Any
from urllib.parse import urlsplit

from .config import config

log = logging.getLogger("consorcio")

_ESPERA_SONDEO = 1.0  # segundos
_PAUSA_TRAS_FALLO = 60.0  # segundos
_mlflow_caido_hasta = 0.0
# Servidor y experimento con los que ya se configuró MLflow en este proceso (ver `_configurar`)
_configurado_para: tuple[str, str] | None = None

# Si MLflow se cae con la corrida abierta, cada pedido pendiente espera esto y no los valores por defecto
os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "10")
os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")


@contextlib.contextmanager
def _sin_frenar_la_corrida():
    """MLflow que cae con la corrida abierta: si la excepción llegara al agente, un turno correcto se
    informaría como error y el vecino repetiría un reclamo ya creado."""
    try:
        yield
    except Exception as e:
        _marcar_caido()
        log.warning("MLflow dejó de responder (%s); la corrida sigue sin trazas", e)


class _Traza:
    def __init__(self, activo: bool):
        self.activo = activo
        self._id: str | None = None  # la traza de la corrida, cuando ya se la vio abierta

    def traza_id(self) -> str | None:
        """Id de la traza GenAI de la corrida; None sin MLflow o si nunca se la vio abierta.

        Se llama desde adentro de la traza (el nodo `registrar` del agente), así que se pide la traza
        abierta. `get_last_active_trace_id` no sirve: MLflow la actualiza recién cuando termina el span
        raíz (3.18, `BaseMlflowSpanProcessor._on_end_impl`). Con la traza del turno abierta devolvía la
        del turno anterior, y como es global al proceso, con dos turnos simultáneos podía ser la de otra
        persona (18/11)."""
        if not self.activo:
            return None
        try:
            import mlflow

            self._id = mlflow.get_active_trace_id() or self._id
        except Exception as e:
            log.debug("no se pudo leer el id de la traza: %s", e)
        return self._id

    def registrar_metricas(self, etiquetas: dict[str, str], metricas: dict[str, float]) -> None:
        """Para corridas que no son ingestas (consultas, evaluaciones)."""
        if not self.activo:
            return
        import mlflow

        with _sin_frenar_la_corrida():
            if etiquetas:
                mlflow.set_tags({k: str(v) for k, v in etiquetas.items()})
            if metricas:
                mlflow.log_metrics({k: float(v) for k, v in metricas.items() if v is not None})

    def registrar(self, estado_final: dict) -> None:
        if not self.activo:
            return
        import mlflow

        clasif = estado_final.get("clasificacion", {})
        costo = estado_final.get("costo", {})
        extraccion = estado_final.get("extraccion", {})
        with _sin_frenar_la_corrida():
            mlflow.set_tags(
                {
                    "metodo": clasif.get("metodo", ""),
                    "tipo_documento": extraccion.get("metadatos", {}).get("tipo_documento", ""),
                    "documento_id": estado_final.get("documento_id", ""),
                }
            )
        metricas: dict[str, float] = {
            "confianza_clasificador": float(clasif.get("confianza", 0)),
            "paginas": float(clasif.get("total_paginas", 0)),
            "segundos_total": float(estado_final.get("segundos_total", 0)),
            "errores": float(len(estado_final.get("errores", []))),
        }
        for k in ("llamadas", "tokens_entrada", "tokens_salida", "segundos_modelo", "costo_usd"):
            if costo.get(k) is not None:
                metricas[k] = float(costo[k])
        for coleccion in ("personas", "organizaciones", "direcciones", "eventos", "unidades", "montos", "secciones"):
            metricas[f"n_{coleccion}"] = float(len(extraccion.get(coleccion, [])))
        with _sin_frenar_la_corrida():
            mlflow.log_metrics(metricas)
        carpeta = estado_final.get("persistido", {}).get("carpeta")
        if carpeta:
            with contextlib.suppress(Exception):
                mlflow.log_artifacts(carpeta, artifact_path="procesados")


_autolog_prendido = False


def _prender_autolog(mlflow) -> None:
    """Trazas GenAI de MLflow: cada llamada de LangChain/LangGraph queda como span dentro del run.
    Necesita `pandas` (lo importa `mlflow.langchain`); si falta, se avisa en vez de callar: hasta el
    26/10 el error se tragaba y las trazas nunca se generaron."""
    global _autolog_prendido
    if _autolog_prendido:
        return
    try:
        mlflow.langchain.autolog()
        _autolog_prendido = True
    except Exception as e:
        log.warning("autolog de LangChain en MLflow no disponible (%s); quedan los runs, sin trazas", e)


def _marcar_caido() -> None:
    """Además de la pausa, olvida la configuración: si el servidor volvió con otra base, el experimento
    ya no existe y hay que fijarlo de nuevo al reconectar."""
    global _mlflow_caido_hasta, _configurado_para
    _mlflow_caido_hasta = time.monotonic() + _PAUSA_TRAS_FALLO
    _configurado_para = None


def _configurar(mlflow) -> None:
    """Fija servidor y experimento una sola vez por proceso; de nuevo solo si cambian o tras una caída.

    `mlflow.set_experiment` reinicia el proveedor de trazas aunque el experimento sea el mismo (MLflow
    3.18, `_sync_trace_destination_and_provider`), y los spans abiertos en ese momento ya no se exportan.
    La consulta que corre dentro del turno del agente lo llamaba con la traza del turno abierta: la traza
    quedaba "In progress", sin el span raíz ni el nodo `reglamento` (16/11). Lo mismo podía pasar entre
    dos turnos simultáneos de la web."""
    global _configurado_para
    destino = (config.mlflow_tracking_uri, config.mlflow_experiment)
    if _configurado_para == destino:
        return
    mlflow.set_tracking_uri(destino[0])
    mlflow.set_experiment(destino[1])
    _configurado_para = destino


def _alcanzable(uri: str) -> bool:
    """Un URI local (`sqlite:`, una carpeta) no tiene puerto que sondear."""
    if time.monotonic() < _mlflow_caido_hasta:
        return False
    try:
        destino = urlsplit(uri)
        if destino.hostname:
            puerto = destino.port or (443 if destino.scheme == "https" else 80)
            socket.create_connection((destino.hostname, puerto), timeout=_ESPERA_SONDEO).close()
        return True
    except (OSError, ValueError) as e:
        _marcar_caido()
        log.warning("MLflow no disponible (%s); se sigue sin trazas por %.0f s", type(e).__name__, _PAUSA_TRAS_FALLO)
        return False


@contextlib.contextmanager
def trazar_corrida(nombre: str, etiquetas: dict[str, Any]):
    """Abre un run de MLflow si está configurado; si MLflow no responde, sigue sin trazas.
    Las excepciones de la corrida se propagan tal cual (no se tragan)."""
    if not config.mlflow_tracking_uri or not _alcanzable(config.mlflow_tracking_uri):
        yield _Traza(False)
        return
    try:
        import mlflow

        _configurar(mlflow)  # nunca en cada corrida: con una traza abierta la rompe
        _prender_autolog(mlflow)
        from .ingesta.skill import version_skill

        # Anidado: la consulta corre dentro del turno del agente, y un segundo run suelto lanza excepción
        mlflow.start_run(run_name=nombre, nested=mlflow.active_run() is not None)
        mlflow.set_tags(
            {
                **{k: str(v) for k, v in etiquetas.items()},
                "skill_version": version_skill(_skill_de(nombre)),
                "proveedor": config.llm_provider,
                "modelo": config.modelo,
            }
        )
    except Exception as e:  # MLflow caído no debe frenar la ingesta
        _marcar_caido()  # el puerto contestó pero el servidor no: no repetir la espera en cada turno
        log.warning("MLflow no disponible (%s); se sigue sin trazas", e)
        yield _Traza(False)
        return

    etiquetas_traza = {
        **{k: str(v) for k, v in etiquetas.items()},
        "proveedor": config.llm_provider, "modelo": config.modelo,
        "endpoint": _endpoint_proveedor(), "skill_version": version_skill(_skill_de(nombre)),
    }
    traza = _Traza(True)
    try:
        yield traza
    except Exception:
        _etiquetar_traza(mlflow, etiquetas_traza, traza)
        with _sin_frenar_la_corrida():
            mlflow.end_run(status="FAILED")
        raise
    else:
        _etiquetar_traza(mlflow, etiquetas_traza, traza)
        with _sin_frenar_la_corrida():
            mlflow.end_run()


def _skill_de(nombre_corrida: str) -> str:
    """Skill cuya versión se registra: `agente` para los turnos, `consulta` para preguntas y su evaluación,
    `ingesta` para el resto."""
    if nombre_corrida.startswith("agente"):
        return "agente"
    return "consulta" if nombre_corrida.startswith(("consulta", "evaluacion-consultas")) else "ingesta"


def _endpoint_proveedor() -> str:
    """A dónde van las llamadas. Ollama y OpenRouter usan la API compatible con OpenAI, por eso el span
    del autolog se llama `ChatOpenAI` aunque el proveedor no sea OpenAI."""
    return {
        "ollama": config.ollama_base_url, "openrouter": config.openrouter_base_url,
        "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com",
        "google_genai": "https://generativelanguage.googleapis.com", "fake": "sin llamadas",
    }.get(config.llm_provider, config.llm_provider)


def _etiquetar_traza(mlflow, etiquetas: dict[str, str], traza: _Traza) -> None:
    """Copia a la traza GenAI (autolog) las mismas etiquetas del run: proveedor, modelo, endpoint, origen…

    Qué traza: la que la corrida ya vio abierta (`traza_id`, el turno del agente); si no, la abierta
    ahora (la consulta anidada termina con la del turno abierta, y antes del 18/11 etiquetaba la del
    turno anterior); si no, la última que terminó en este hilo (una ingesta o una consulta suelta, que
    terminan antes de etiquetar)."""
    try:
        tid = traza._id or mlflow.get_active_trace_id() or mlflow.get_last_active_trace_id(thread_local=True)
        if not tid:
            return
        for k, v in etiquetas.items():
            mlflow.set_trace_tag(tid, k, v)
    except Exception as e:
        log.debug("no se pudo etiquetar la traza: %s", e)


def configurar_logging(nivel: int = logging.INFO) -> None:
    logging.basicConfig(level=nivel, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
