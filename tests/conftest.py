"""Entorno común de los tests: nunca un modelo real, MLflow ni el bucket de los crudos.

Se fija antes de que cualquier test importe `consorcio.config` (pytest carga este archivo primero);
si se dejara en cada test, el orden de importación decidía si una corrida de prueba terminaba
trazada en el MLflow del proyecto (pasó el 26/10).

También se fija lo que cambia cómo se comporta el sistema, para que `make test` dé lo mismo con
cualquier `.env`: un `FAKE_GUIONADO=true` o un `LLM_MODEL` de clave paga rompían tests que no los
esperaban. Lo que queda afuera es cómo llegar a la infraestructura: `test_entorno.py` lo controla."""
import os

ENTORNO_FIJO = {
    "LLM_PROVIDER": "fake",
    "LLM_MODEL": "",
    "LLM_MODEL_VISION": "",
    "FAKE_GUIONADO": "false",
    "TOPE_USD_MENSUAL": "20",
    "MEMORIA_MENSAJES": "6",
    "AGENTE_MAX_MENSAJE": "2000",
    "OCR_UMBRAL_TEXTO_CHARS": "200",
    "OCR_UMBRAL_CONFIANZA": "75",
    "OCR_PAGINAS_MUESTRA": "3",
    "OCR_ESCALAR_A_VISION": "true",
    "OCR_DPI": "200",
    "MLFLOW_TRACKING_URI": "",
    "CONSORCIO_POR_DEFECTO": "mercedes-274",
    "TELEGRAM_CHATS_PERMITIDOS": "",
    "WEB_SELECTOR_USUARIOS": "false",
    "WEB_SECRETO": "secreto-solo-para-tests-de-mas-de-32-caracteres",
    "WEB_MAX_MB_PDF": "20",
    "WEB_SESION_HORAS": "8",
    "S3_BUCKET": "consorcio-crudos-test",  # test_crudos y test_duplicados lo vacían
}
os.environ.update(ENTORNO_FIJO)
