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

    def traza_id(self) -> str | None:
        """Id de la última traza GenAI de la corrida; None sin MLflow."""
        if not self.activo:
            return None
        try:
            import mlflow

            return mlflow.get_last_active_trace_id()
        except Exception as e:
            log.debug("no se pudo leer el id de la traza: %s", e)
            return None

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
    global _mlflow_caido_hasta
    _mlflow_caido_hasta = time.monotonic() + _PAUSA_TRAS_FALLO


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

        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        mlflow.set_experiment(config.mlflow_experiment)
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
    try:
        yield _Traza(True)
    except Exception:
        _etiquetar_traza(mlflow, etiquetas_traza)
        with _sin_frenar_la_corrida():
            mlflow.end_run(status="FAILED")
        raise
    else:
        _etiquetar_traza(mlflow, etiquetas_traza)
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


def _etiquetar_traza(mlflow, etiquetas: dict[str, str]) -> None:
    """Copia a la traza GenAI (autolog) las mismas etiquetas del run: proveedor, modelo, endpoint, origen…"""
    try:
        tid = mlflow.get_last_active_trace_id()
        if not tid:
            return
        for k, v in etiquetas.items():
            mlflow.set_trace_tag(tid, k, v)
    except Exception as e:
        log.debug("no se pudo etiquetar la traza: %s", e)


def registrar_revision(traza_id: str | None, veredicto: str, motivo: str | None) -> bool:
    """Cuelga la revisión de la administración de la traza del turno, como evaluación humana.

    Solo veredicto y motivo: la respuesta correcta que escribe la administración es texto del consorcio y
    no sale de Postgres. Si MLflow no está, la revisión ya quedó guardada igual: devuelve False y sigue."""
    if not traza_id or not config.mlflow_tracking_uri or not _alcanzable(config.mlflow_tracking_uri):
        return False
    try:
        from mlflow.entities import AssessmentSource, AssessmentSourceType

        import mlflow

        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        mlflow.log_feedback(
            trace_id=traza_id, name="revision_administracion", value=veredicto, rationale=motivo,
            source=AssessmentSource(source_type=AssessmentSourceType.HUMAN, source_id="administracion"),
        )
        return True
    except Exception as e:  # p. ej. una traza que ya no existe: no es motivo para pausar MLflow
        log.warning("no se pudo registrar la revisión en MLflow: %s", type(e).__name__)
        return False


def configurar_logging(nivel: int = logging.INFO) -> None:
    logging.basicConfig(level=nivel, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
