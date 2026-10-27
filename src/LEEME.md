# Código

Paquete `consorcio/` (se instala con `pip install -e .`; dentro de Docker ya está instalado).

| Módulo | Qué hace |
|---|---|
| `config.py` | Configuración desde `.env` (proveedor de modelo, umbrales de OCR, conexiones, precios) |
| `llm.py` | Acceso al modelo independiente del proveedor (`google_genai`, `anthropic`, `openai`, `fake`) + contador de llamadas, tokens y costo |
| `observabilidad.py` | Trazas en MLflow (opcional) y logging |
| `cli.py` | Línea de comandos `consorcio ...` |
| `texto.py` | Normalización de texto compartida (búsqueda, verificación de citas, evaluación) e ids: `id_documento(consorcio, hash)`, `consorcio_de_chat_id` |
| `ingesta/` | **Tool 1**: PDF → clasificar calidad → extraer texto → estructurar → persistir (grafo de LangGraph en `grafo.py`). `liquidacion.py`: liquidaciones de expensas (modelo para rubros y gastos, regex para el prorrateo por unidad) |
| `consulta/` | **Tool 2**: pregunta → `buscador.py` (tsvector o palabras clave en Postgres) → responder con cita → verificar la cita en el texto original o derivar (grafo en `grafo.py`); `evaluar.py` mide recall@k y calidad de respuestas contra `datos/evaluacion/preguntas.csv` |
| `almacen/` | Persistencia: Postgres (consorcios y chats, documentos con `consorcio_id`, datos estructurados y sensibles, liquidaciones, consultas; `migrar_consorcios`) y Neo4j (grafo de contexto) |
| `canales/` | Cómo le hablan al agente: `telegram.py` (bot por long polling: PDF → ingesta, texto → consulta, `/expensas` → SQL; lista blanca de chats). Solo formatea; no tiene lógica propia |

Las instrucciones que recibe el modelo viven en `skills/ingesta/SKILL.md` (transcribir, clasificar, estructurar, liquidación) y `skills/consulta/SKILL.md` (responder con cita), no en el código.
