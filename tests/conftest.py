"""Entorno común de los tests: nunca un modelo real ni MLflow.

Se fija antes de que cualquier test importe `consorcio.config` (pytest carga este archivo primero);
si se dejara en cada test, el orden de importación decidía si una corrida de prueba terminaba
trazada en el MLflow del proyecto (pasó el 26/10)."""
import os

os.environ["LLM_PROVIDER"] = "fake"
os.environ["MLFLOW_TRACKING_URI"] = ""
