"""`make test` no puede depender del `.env` de quien lo corre: el README pide prender FAKE_GUIONADO y
WEB_SELECTOR_USUARIOS para la web, y con esas claves fallaban tests que no tienen nada que ver."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from conftest import ENTORNO_FIJO

RAIZ = Path(__file__).resolve().parents[1]

# Cómo llegar a la infraestructura y credenciales: cambian de máquina en máquina, no el comportamiento
SIN_FIJAR = re.compile(r"POSTGRES_.*|S3_(ENDPOINT_URL|ACCESS_KEY|SECRET_KEY)|.*_API_KEY|TELEGRAM_BOT_TOKEN|MLFLOW_EXPERIMENT|DIR_.*")

SALIDA_CONFIG = """
import json, runpy, sys
runpy.run_path(sys.argv[1])
from consorcio.config import config
print(json.dumps({k: getattr(config, k) for k in (
    "llm_provider", "llm_model", "llm_model_vision", "fake_guionado", "web_selector_usuarios", "tope_usd_mensual")}))
"""


def test_toda_clave_del_ejemplo_esta_fijada_o_es_infraestructura():
    """Una clave nueva en `.env.example` no entra sin decidir de qué lado queda."""
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    claves = re.findall(r"^([A-Z][A-Z0-9_]*)=", ejemplo, re.MULTILINE)
    assert claves and [c for c in claves if c not in ENTORNO_FIJO and not SIN_FIJAR.fullmatch(c)] == []


def test_el_entorno_de_quien_corre_los_tests_no_entra():
    """En un proceso aparte: en este, `consorcio.config` ya se importó."""
    hostil = {**os.environ, "LLM_PROVIDER": "anthropic", "LLM_MODEL": "claude-haiku-5-5", "LLM_MODEL_VISION": "otro",
              "FAKE_GUIONADO": "true", "WEB_SELECTOR_USUARIOS": "true", "TOPE_USD_MENSUAL": "0"}
    salida = subprocess.run([sys.executable, "-c", SALIDA_CONFIG, str(RAIZ / "tests" / "conftest.py")], env=hostil,
                            capture_output=True, text=True, check=True, cwd=RAIZ)
    assert json.loads(salida.stdout) == {
        "llm_provider": "fake", "llm_model": "", "llm_model_vision": "", "fake_guionado": False,
        "web_selector_usuarios": False, "tope_usd_mensual": 20.0,
    }
