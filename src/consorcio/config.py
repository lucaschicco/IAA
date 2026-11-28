"""Configuración central, leída de variables de entorno / .env.

Todo lo que el sistema necesita decidir "desde afuera" (proveedor de modelo,
umbrales del OCR, conexiones) vive acá. El resto del código no lee os.environ.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

RAIZ_REPO = Path(__file__).resolve().parents[2]

# Modelo por defecto de cada proveedor cuando LLM_MODEL está vacío.
MODELOS_POR_DEFECTO = {
    "google_genai": "gemini-2.5-flash",
    "anthropic": "claude-opus-5-5",
    "openai": "gpt-5",
    # Endpoints compatibles con OpenAI: el modelo se elige con LLM_MODEL según el catálogo de cada uno
    "openrouter": "google/gemini-2.5-flash",
    "ollama": "gpt-oss:120b",  # texto; para visión usar LLM_MODEL_VISION=gemma4:31b
    "fake": "fake",
}

# USD por millón de tokens (entrada, salida). Alimenta la sección de costos del informe.
# Actualizar cuando cambie el proveedor o el modelo; los valores no verificados van en None.
PRECIOS_USD_POR_MILLON: dict[str, tuple[float | None, float | None]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "gpt-5": (None, None),
    "fake": (0.0, 0.0),
}


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=RAIZ_REPO / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Modelo
    llm_provider: str = "fake"
    llm_model: str = ""
    llm_model_vision: str = ""
    google_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    ollama_api_key: str = ""
    ollama_base_url: str = "https://ollama.com/v1"

    # OCR
    ocr_umbral_texto_chars: int = 200
    ocr_umbral_confianza: float = 75.0
    ocr_paginas_muestra: int = 3
    ocr_escalar_a_vision: bool = True
    ocr_dpi: int = 200
    # Cuántas páginas de texto van en cada llamada al estructurar
    estructurar_paginas_por_lote: int = 5
    # Tope de llamadas al modelo por documento (presupuesto, como pide la práctica)
    presupuesto_llamadas: int = 120
    # Tope de tokens de salida por llamada. Un lote de 5 páginas estructurado ocupa hasta ~8.000;
    # una página transcripta, ~1.000. Sin tope, un modelo en bucle llegó a 261.000 (26/10/2026).
    max_tokens_salida: int = 16000
    max_tokens_vision: int = 4000
    # Tope de gasto en modelos por mes calendario UTC, en USD; 0 = sin tope (ver tope.py)
    tope_usd_mensual: float = 20.0
    # True: el proveedor fake contesta con guion (agente/guion_fake.py) en vez de con esquemas vacíos
    fake_guionado: bool = False

    # Agente
    memoria_mensajes: int = 6  # cuántos mensajes de la conversación ve el router
    agente_max_mensaje: int = 2000  # caracteres

    # Web. Con el selector prendido cualquiera entra como usuario demo: es el modo desarrollo.
    web_selector_usuarios: bool = False
    web_secreto: str = ""  # firma las cookies de sesión; obligatorio con el selector apagado
    web_max_mb_pdf: int = 20
    web_max_mb_carga: int = 60  # todos los PDFs de una carga juntos
    web_max_archivos_carga: int = 10
    web_espera_carga: float = 3.0  # segundos que la pantalla espera el resultado antes de dejarlo en segundo plano
    web_sesion_horas: int = 8

    # Login (ver auth/). JWT_SECRETO firma los tokens de la API JSON (`make api`); la web usa WEB_SECRETO.
    jwt_secreto: str = ""

    # Bases de datos
    postgres_url: str = "postgresql+psycopg://consorcio:consorcio@localhost:5432/consorcio"

    # Trazabilidad
    mlflow_tracking_uri: str = ""
    mlflow_experiment: str = "consorcio-ingesta"

    # Consorcio (base) que usan la CLI y la evaluación cuando no se indica --consorcio.
    # Cada consorcio tiene su propia base: documentos, secciones, liquidaciones y consultas.
    consorcio_por_defecto: str = "mercedes-274"

    # Crudos: PDF originales en un bucket S3 (ver almacen/crudos.py). Sin S3_ENDPOINT_URL la ingesta
    # sigue solo en local, con un warning; con endpoint, un fallo del bucket la frena.
    s3_endpoint_url: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "consorcio-crudos"

    # Canales
    telegram_bot_token: str = ""
    # Ids de chat (separados por coma) a los que el bot responde; vacío = a todos (solo para probar)
    telegram_chats_permitidos: str = ""

    # Salidas
    dir_procesados: Path = Field(default=RAIZ_REPO / "datos" / "procesados")
    # PDFs que llegan por un canal (Telegram) antes de ingestarse
    dir_entrada: Path = Field(default=RAIZ_REPO / "datos" / "entrada")

    @property
    def modelo(self) -> str:
        return self.llm_model or MODELOS_POR_DEFECTO.get(self.llm_provider, "")

    @property
    def modelo_vision(self) -> str:
        return self.llm_model_vision or self.modelo

    def ruta_procesados(self) -> Path:
        return self._absoluta(self.dir_procesados)

    def ruta_entrada(self) -> Path:
        return self._absoluta(self.dir_entrada)

    @staticmethod
    def _absoluta(p: Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else RAIZ_REPO / p


config = Config()
