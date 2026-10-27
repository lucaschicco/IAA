"""Trazabilidad con MLflow (práctica, diseño 8). Si MLFLOW_TRACKING_URI está
vacío, todo es no-op y el sistema corre igual.

Por corrida se abre un run con etiquetas (nodo raíz, archivo, versión del skill,
decisión del clasificador) y se registran métricas: llamadas, tokens, costo,
segundos, y el conteo de entidades extraídas.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any

from .config import config

log = logging.getLogger("consorcio")


class _Traza:
    def __init__(self, activo: bool):
        self.activo = activo

    def registrar_metricas(self, etiquetas: dict[str, str], metricas: dict[str, float]) -> None:
        """Para corridas que no son ingestas (consultas, evaluaciones)."""
        if not self.activo:
            return
        import mlflow

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


@contextlib.contextmanager
def trazar_corrida(nombre: str, etiquetas: dict[str, Any]):
    """Abre un run de MLflow si está configurado; si MLflow no responde, sigue sin trazas.
    Las excepciones de la corrida se propagan tal cual (no se tragan)."""
    if not config.mlflow_tracking_uri:
        yield _Traza(False)
        return
    try:
        import mlflow

        mlflow.set_tracking_uri(config.mlflow_tracking_uri)
        mlflow.set_experiment(config.mlflow_experiment)
        _prender_autolog(mlflow)
        from .ingesta.skill import version_skill

        mlflow.start_run(run_name=nombre)
        mlflow.set_tags(
            {
                **{k: str(v) for k, v in etiquetas.items()},
                "skill_version": version_skill(_skill_de(nombre)),
                "proveedor": config.llm_provider,
                "modelo": config.modelo,
            }
        )
    except Exception as e:  # MLflow caído no debe frenar la ingesta
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
        mlflow.end_run(status="FAILED")
        raise
    else:
        _etiquetar_traza(mlflow, etiquetas_traza)
        mlflow.end_run()


def _skill_de(nombre_corrida: str) -> str:
    """Skill cuya versión se registra: `consulta` para preguntas y su evaluación, `ingesta` para el resto."""
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


def configurar_logging(nivel: int = logging.INFO) -> None:
    logging.basicConfig(level=nivel, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
