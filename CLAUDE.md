# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es este repo

TP grupal de Inteligencia Artificial Agéntica (Maestría en Data Mining, UBA, Grupo 6): un asistente para la administración de consorcios que responde consultas sobre reglamento y expensas citando la fuente, asiste la carga de facturas, registra reclamos y gestiona proveedores, con revisión humana (bandeja de aprobación) en las acciones importantes.

**Estado actual (26/10/2026): estructura del agente, tool 1 (ingesta de PDF, incluidas liquidaciones de expensas) y tool 2 (consultas con cita verificada).** Paquete `src/consorcio/`, todo corre en Docker (`docker compose up -d`; ver "Cómo correrlo" en el README). Comandos: `make test`, `make lint`, `make ingestar PDF=...`, `make preguntar Q=...`, `make expensas QUE=...`, `make evaluar-consultas`, `make evaluar-ocr PDF=... REF=...`, `make bot` (Telegram), `make respaldar` / `make restaurar` (volcado de Postgres en `datos/respaldo/`). Todos los documentos de `datos/documentos/` y las 8 liquidaciones están ingestados en el Postgres local. Hay un **canal de Telegram** (`src/consorcio/canales/telegram.py`, `consorcio bot`, long polling): PDF adjunto → ingesta, texto o `/preguntar` → `preguntar`, `/expensas` → SQL, `/documentos`; lista blanca de chats en `TELEGRAM_CHATS_PERMITIDOS`; es un cliente de las tools y no debe tener lógica propia. **Una base por consorcio (26/10)**: `consorcios` y `chats` en Postgres, `documentos.consorcio_id` y `consultas.consorcio_id`, id de documento `<consorcio>:<hash>`; cada chat de Telegram usa su propio consorcio (`chat-<id>`, creado al primer mensaje) y `consorcio consorcios --asignar-chat ID --a X` agrupa varios números en uno. La CLI y la evaluación usan `CONSORCIO_POR_DEFECTO=mercedes-274` salvo `--consorcio`. El contrato Cabrera está en el consorcio `cabrera`, activo. Las bases anteriores se migran con `consorcio migrar` (idempotente). Neo4j comparte Persona/Organizacion/Direccion por nombre entre consorcios (pendiente separarlos si hace falta). El resto de los flujos (router, reclamos, proveedores, facturas, bandeja de aprobación, calendario, WhatsApp) todavía no existe; el router se reemplaza por el subcomando elegido a mano o por el tipo de mensaje en Telegram.

Decisiones de implementación ya tomadas (24/10/2026):
- **LangGraph** para los grafos (siguiendo `docs/practicas/10_langgraph_y_mlflow_self_hosted.py`): estado tipado, nodos, aristas condicionales, dibujable con `consorcio dibujar-grafo`.
- **Proveedor de modelo abstraído** en `src/consorcio/llm.py` vía `init_chat_model` de LangChain; se elige con `LLM_PROVIDER` (`google_genai`, `anthropic`, `openai`, `openrouter`, `ollama`, `fake`) y se puede pisar por corrida con `--proveedor/--modelo`. El `fake` corre el pipeline sin clave y es lo que usan los tests. No acoplar código a un SDK de proveedor. Resultados comparados en `docs/evaluaciones/`.
- **Clasificador de calidad de PDF sin modelo** (`ingesta/clasificador.py`): capa de texto → gratis; escaneo legible (confianza Tesseract ≥ umbral) → Tesseract; escaneo malo → visión del modelo. Es el "paso inicial de clasificación símil Jev" de la práctica: decisión tipada con confianza y motivo.
- **Prompts en `skills/ingesta/SKILL.md`**, no en el código; tienen versión y se iteran contra fallas observadas (anotar en la bitácora).
- **Almacenamiento doble**: Postgres para datos estructurados (SQLAlchemy, `almacen/postgres.py`) y Neo4j para el grafo de contexto (`almacen/grafo_neo4j.py`). Los datos sensibles (DNI, CUIT, teléfonos, emails, fechas de nacimiento) se guardan completos en Postgres con `sensible=true` y **nunca** se replican en Neo4j.
- **Costo medido por corrida**: `Contador` en `llm.py` registra llamadas, tokens, segundos y USD por nodo; se guarda en `llamadas_modelo`, en `extraccion.json` y en MLflow si `MLFLOW_TRACKING_URI` está seteado. Hay un tope de llamadas por documento (`presupuesto_llamadas`).
- Cada ingesta deja `datos/procesados/<slug>-<hash>[-pags-N-M]-<proveedor>-<modelo>/texto.md` y `extraccion.json` (reproducible y revisable a mano). En Postgres/Neo4j el documento se identifica por el hash del archivo: la última corrida pisa a la anterior.
- **Consultas con cita (`consulta/`)**: `buscar` (Postgres, sin modelo) → `responder` (una llamada, salida estructurada) → `verificar` (la cita tiene que aparecer textualmente en las páginas originales, no en el resumen) o `derivar`. El baseline es la búsqueda por palabras clave sin stemming (`--busqueda palabras`); el sistema usa `tsvector` en español sobre título+tema+resumen de `secciones`. Sin embeddings por ahora: decidir con `consorcio evaluar-consultas` (26/10: tsvector gana en dev, 1,0 vs 0,917 de recall@3, pero pierde en test, 0,583 vs 0,917; no se ajusta nada mirando test). El buscador solo usa documentos con `activo=true` (`consorcio documentos --desactivar X`) del consorcio consultado.
- **Liquidaciones de expensas**: el modelo extrae encabezado, rubros y gastos (sección `liquidacion` del skill); el prorrateo por unidad (~140 filas) lo parsea una regex en `ingesta/liquidacion.py` y se cuentan las filas que no matchean. Los importes se consultan con `consorcio expensas` por SQL; nunca los produce el modelo.
- **Salida estructurada por prompt** para proveedores sin tool calling confiable (`ollama`, `openrouter`): `invocar_estructurado` pide el JSON Schema en el prompt y valida con pydantic. Para `google_genai`/`anthropic`/`openai` se sigue usando `with_structured_output`.
- **Cuotas gratuitas (26/10/2026)**: la clave de Anthropic venció; AI Studio gratuito da 20 pedidos/día en `gemini-2.5-flash`; Ollama cloud gratuito (`gpt-oss:120b` texto, `gemma4:31b` visión) admite un pedido a la vez. Por eso la mayor parte de la base se ingestó con Ollama. Al conseguir una clave paga, reingestar con Haiku 5.5 (el hash hace que pise lo anterior).
- **Freno a los bucles del modelo**: toda llamada lleva `max_tokens` (`max_tokens_salida`, 16.000; visión 4.000) y `texto_degenerado()` detecta transcripciones en bucle ("l l l l…", "[ilegible]" × 10.000) y las manda directo a Tesseract (el bucle es determinista y Ollama cloud ignora `max_tokens`). `consorcio ingestar --reusar-texto` reutiliza las páginas OCR/visión ya guardadas en Postgres (misma hash) para iterar el estructurado sin volver a pagar la transcripción.
- Las claves de API viven solo en `.env` (ignorado por git). Si se cambia el `.env`, hay que recrear el contenedor (`docker compose up -d --force-recreate app`) porque Compose inyecta las variables al arrancar.

El repo es **privado porque `datos/` contiene datos personales reales** del consorcio Mercedes 274 (CABA). No copiar su contenido a servicios externos ni a repos públicos; la consigna pide una muestra sanitizada para replicabilidad.

El idioma del proyecto es español (docs, nombres de archivos `LEEME.md`, commits). Mantenerlo.

## Arquitectura prevista

Descrita en `README.md` (diagrama mermaid) y `docs/informe.md` §5. Un router clasifica cada pedido y lo deriva a uno de cinco flujos:

| Flujo | Fuente | Salida |
|---|---|---|
| Reglamento | Buscador (RAG) sobre reglamento, actas, normativa | Respuesta con cita verificada, o deriva a la administración |
| Expensas | Tabla extraída de las liquidaciones | Respuesta con el dato exacto (los montos nunca los inventa el modelo) |
| Facturas | Extracción con LLM de PDF/foto → tabla | Bandeja de aprobación |
| Reclamos | Tabla propia del sistema | Bandeja de aprobación |
| Proveedores | `datos/proveedores/proveedores.csv` | Bandeja de aprobación |

Principios de diseño que ya están decididos:
- **Consultas** se responden citando; si la cita no se puede verificar programáticamente contra lo recuperado, se deriva a la administración.
- **Acciones** (guardar factura, registrar reclamo, contactar proveedor) nunca se ejecutan sin aprobación humana; la administración aprueba, edita o rechaza. Hay que registrar cada acción propuesta y su resultado para medir el costo y efecto del human-in-the-loop.
- Las actas pueden modificar lo que dice el reglamento: el buscador debe recuperar de varios documentos y el sistema debe considerar vigencia.
- Pagos y deuda por unidad son sensibles: no se muestran a otros vecinos.
- Registrar llamadas al modelo, costo y latencia por consulta (va a la sección de costos del informe).

`docs/fuentes.md` detalla cada fuente, si es real/pública/inventada y cómo la lee el sistema (buscador, tabla, o extracción → tabla).

## Datos (`datos/`)

| Carpeta | Contenido | Estado |
|---|---|---|
| `normativa/` | Leyes 941 y 3254, modelo de reglamento PH, manual de prácticas consorciales | Pública, con texto |
| `documentos/` | Reglamento de copropiedad (60 pág.), reglamento de cocheras, 4 actas, 2 convocatorias, contrato Cabrera (otro edificio, con transcripción a mano para evaluar OCR) | Todo ingestado (26/10); reglamento y actas por visión del modelo, falta revisión a mano |
| `expensas/` | 8 liquidaciones mensuales feb–sep 2026 (el nombre del archivo es la fecha de emisión; liquida el mes anterior) | Ingestadas a tablas (26/10): rubros, gastos y prorrateo por unidad |
| `facturas/` | 29 facturas/comprobantes + listado de gastos de agosto 2026 | El listado de agosto es el **ground truth** para evaluar la extracción de facturas |
| `comunicados/` | Mensajes de la administración jun–sep 2026 (`.docx`) | Con texto |
| `proveedores/` | CSV inventado | Listo |
| `evaluacion/` | `preguntas.csv`: preguntas con documento y sección esperados, partición dev/test, y preguntas que deben derivarse | 26 preguntas (26/10), 2 de ellas "trampa" que deben derivarse; ampliar |
| `unidades/` | Tabla de unidades | A armar (el prorrateo ya da UF, piso-depto y porcentual) |

## Evaluación y entrega

La consigna (`docs/consigna.md`) exige hipótesis medible contra baseline con partición fija dev/test, y profundizar en al menos 2 áreas. Las elegidas de hecho son retrieval evaluado por separado de la generación, herramientas/router y human-in-the-loop. Baselines planeados: búsqueda por palabras clave vs. nuestro retrieval, y modelo sin documentos vs. el sistema completo. Los resultados deben ser reproducibles desde el repo, no fabricados para la demo.

Fechas: entrega final 19/11/2026, exposición 10 u 11/12/2026.

## Documentación que hay que mantener

- `docs/poc.md`: la prueba de concepto tal como está (diagramas del agente, paso a paso de cada petición, números y límites). Actualizarlo cuando cambie un grafo, un canal o los resultados.
- `docs/informe.md`: informe final, una sección por ítem de la consigna. Las partes marcadas "Falta" se completan a medida que se construye.
- `docs/bitacora.md`: notas cortas con fecha de qué se probó, qué no funcionó y qué se decidió. Agregar una entrada ante cada decisión o bloqueo relevante; alimenta la exposición final.
- `README.md`: checklist de próximos pasos (ir tildando) y sección "Cómo correrlo".
- `src/LEEME.md`: mapa de módulos del paquete; actualizar al agregar uno.
- `docs/propuesta/`: propuesta presentada, devolución de la cátedra (pidió más que consultas y revisión humana explícita), entrevista al administrador (dolores: carga de facturas y seguimiento de reclamos por WhatsApp).

La consigna pide que el repo incluya todos los artefactos usados en el desarrollo (skills, reglas, etc.) y una historia de git con contribuciones distribuidas: commits chicos y descriptivos, no "Add files via upload".
