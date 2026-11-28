# IAA – Asistente para consorcios

TP grupal de Inteligencia Artificial Agéntica (Maestría en Data Mining, UBA), Grupo 6.

Es un asistente para la administración de consorcios:

- responde consultas sobre reglamento, actas y expensas citando la fuente;
- registra reclamos y deriva a una persona lo que no puede resolver;
- en las acciones importantes, la administración revisa antes de que se ejecuten.

Para ver cómo funciona hoy la prueba de concepto, con diagramas, el paso a paso de cada petición, números medidos
y límites, leer [`docs/poc.md`](docs/poc.md).

## Índice

1. [Cómo funciona](#1-cómo-funciona)
2. [Estado y próximos pasos](#2-estado-y-próximos-pasos)
3. [Stack](#3-stack)
4. [Estructura del repo](#4-estructura-del-repo)
5. [Puesta en marcha](#5-puesta-en-marcha)
6. [Configuración (`.env`)](#6-configuración-env)
7. [Comandos](#7-comandos)
8. [Componentes](#8-componentes)
9. [Modelo de datos](#9-modelo-de-datos)
10. [Pruebas](#10-pruebas)
11. [Probarlo por la web](#11-probarlo-por-la-web)
12. [Probarlo por Telegram](#12-probarlo-por-telegram)
13. [Entornos existentes y traspaso](#13-entornos-existentes-y-traspaso)

## 1. Cómo funciona

```mermaid
flowchart TD
    V["Vecinos<br/><small>propietarios e inquilinos</small>"] --> W
    A["Administración<br/><small>carga, consulta, aprueba</small>"] --> W
    W["Web (FastAPI + htmx)<br/><small>chat, documentos, bandeja</small>"] --> AG
    T["Telegram<br/><small>congelado, sin router</small>"] --> TO

    subgraph S[Sistema]
        AG["Agente: router<br/><small>1 llamada al modelo</small>"]
        AG --> F1[Reglamento / documentos]
        AG --> F2[Expensas]
        AG --> F3[Reclamo]
        AG --> F4[Derivar / fuera de alcance]
        TO["Tools<br/><small>ingesta · consulta · expensas</small>"]
        F1 --> TO
        F2 --> TO
        PG[("Postgres<br/><small>documentos, secciones, liquidaciones, usuarios, solicitudes</small>")]
        S3[("S3 / SeaweedFS<br/><small>PDF originales</small>")]
        TO --> PG
        TO --> S3
        F3 --> PG
        F4 --> PG
    end

    F1 --> O1["Respuesta con cita verificada<br/><small>o deriva a la administración</small>"]
    F2 --> O2["Importe exacto por SQL"]
    F3 --> O3["Bandeja de aprobación"]
    F4 --> O3
```

- **Consultas:** el sistema busca en los documentos o en las tablas y responde citando. Si no puede verificar la
  cita contra el texto original, deriva a la administración.
- **Importes:** salen siempre de SQL sobre las liquidaciones, nunca del modelo.
- **Acciones:** un reclamo queda como solicitud pendiente, y la administración la aprueba, edita o rechaza.
- **Aislamiento:** cada consorcio es una base lógica (`consorcio_id`) y nada cruza entre consorcios. La única
  excepción es el CRM de plataforma, que solo muestra agregados.

En el diseño original había dos flujos más, facturas y proveedores, que no entran en esta etapa.

## 2. Estado y próximos pasos

**Datos**
- [x] OCR del reglamento de copropiedad y las 4 actas (76 páginas escaneadas) (26/10). Falta revisarlo a mano
- [x] Texto del reglamento de cocheras y las convocatorias (26/10). Falta la normativa
- [x] 8 liquidaciones pasadas a tablas: rubros, gastos y prorrateo por unidad (26/10)
- [x] Preguntas de prueba con respuesta y partición dev/test: `datos/evaluacion/preguntas.csv` y `mensajes_agente.csv`
- [ ] Facturas contra el listado de gastos de agosto (fuera de esta etapa)
- [ ] Tabla de unidades de Mercedes 274 (`make alta-unidades`)

**Búsqueda y respuestas**
- [x] Búsqueda de texto completo en español sobre las secciones y baseline por palabras clave (26/10)
- [x] Respuesta con cita verificada textualmente; si no se verifica, deriva (26/10)
- [x] Consulta en dos pasos: `extraer` hasta 3 citas, `verificar` y `redactar` solo con ellas (24/11)
- [ ] Embeddings o sinónimos: "perros" no encuentra "animales" (ver `docs/evaluaciones/2026-11-24-manzanares.md`)
- [ ] Actas escaneadas: recuperar el documento entero para preguntas de resumen

**Agente y canales**
- [x] Router con reglamento, expensas, documentos, reclamo, derivar y fuera de alcance (01/11)
- [x] Una base por consorcio, crudos en S3 y control de duplicados por hash y por período (01/11)
- [x] Telegram: PDF → ingesta, texto → consulta, `/expensas` (congelado, sin router)
- [ ] Que un 429 de cuota diaria corte enseguida y no cree una derivación
- [ ] WhatsApp (la API de Meta pide número de negocio, verificación y webhook público)

**Web y seguimiento**
- [x] Chat, documentos, solicitudes y bandeja (01/11); login con mail, contraseña e invitaciones (09/11)
- [x] CRM de plataforma y tope de gasto mensual (01/11)
- [x] Medidor de certeza, revisión de respuestas por la administración y elevar a la administración (19/11)
- [x] Rediseño, panel multi-consorcio, invitaciones desde la web (20–23/11)
- [x] Carga de hasta 10 PDFs en segundo plano y consultas de la administración en `/consultar` (24/11)
- [x] Prueba de punta a punta con un consorcio nuevo y Claude Haiku 5.5: 31 de 47 correctas y 0 incorrectas (25/11)
- [ ] Juez automático y casos de prueba a partir de las revisiones

**Cierre**
- [ ] Correr la evaluación sobre el set de test
- [ ] Medir costos y tiempos para el informe
- [ ] Completar el informe y armar la presentación

## 3. Stack

| Capa | Tecnología |
|---|---|
| Lenguaje | Python 3.12 (imagen `python:3.12-slim`; `requires-python >= 3.11`) |
| Orquestación | LangGraph 1.x, LangChain 1.x (`init_chat_model`) |
| Modelos | `google_genai`, `anthropic`, `openai`, `openrouter`, `ollama` o `fake`, sin acoplarse a un SDK |
| PDF y OCR | pdfplumber, pypdfium2, Tesseract (`spa`) con pytesseract y poppler |
| Datos | Postgres 16, SQLAlchemy 2, psycopg 3 y Alembic |
| PDF crudos | API S3 con boto3; SeaweedFS en Compose |
| Web | FastAPI, Uvicorn, Jinja2, htmx vendorizado y CSS propio |
| Login | argon2-cffi, PyJWT (API JSON), email-validator |
| Observabilidad | MLflow (runs y trazas GenAI) y tablas de costo propias |
| Telegram | python-telegram-bot, con long polling |
| Calidad | pytest, ruff (`E,F,I,UP,B`, línea 100) y httpx para los tests web |

## 4. Estructura del repo

```
src/consorcio/          paquete Python (mapa de módulos en src/LEEME.md)
  ingesta/              tool 1: PDF → clasificar → extraer → estructurar → persistir
  consulta/             tool 2: buscar → extraer citas → verificar → redactar
  agente/               router, flujos, bandeja, memoria, certeza, revisión, panel, semilla demo
  almacen/              postgres.py (todo el SQL) y crudos.py (S3)
  web/                  FastAPI: rutas, sesión/CSRF, cargas en segundo plano, plantillas, estáticos
  auth/                 login: cuentas, Argon2, invitaciones, API JSON con JWT
  canales/              telegram.py y formato.py (textos compartidos con la web)
  llm.py · config.py · identidad.py · tope.py · crm.py · observabilidad.py · texto.py · cli.py
skills/                 prompts versionados: ingesta, consulta, agente (SKILL.md con version:)
migraciones/            Alembic, de 0001_base a 0011_administraciones
tests/                  pytest con el proveedor fake (unos 940 tests)
scripts/e2e_web.py      prueba de punta a punta por HTTP contra la web levantada
datos/                  normativa, documentos y expensas reales, facturas, evaluación (ver docs/fuentes.md)
docs/                   poc, informe, bitácora, evaluaciones/, diseno/ (spec de la web), observabilidad, consigna
```

## 5. Puesta en marcha

Hace falta Docker con Compose v2. `make` es opcional: sin él, usar el comando equivalente del `Makefile`.

```bash
cp .env.example .env        # con LLM_PROVIDER=fake corre sin clave
docker compose build
docker compose up -d        # app, postgres, seaweedfs, mlflow
make test && make lint
```

El proyecto de Compose se llama `iaa`. Los volúmenes `iaa_pgdata` e `iaa_seaweeddata` no dependen del nombre de la
carpeta.

| Servicio | Contenedor | Puerto | Para qué |
|---|---|---|---|
| `app` | `consorcio-app` | 127.0.0.1:8001, 8002 | CLI, tests, web a mano (8001) y API del login (8002). Corre `sleep infinity` y monta el repo en `/app` |
| `web` (perfil `web`) | `consorcio-web` | 127.0.0.1:8000 | `make web` |
| `bot` (perfil `bot`) | `consorcio-bot` | — | `make bot` |
| `postgres` | `consorcio-postgres` | 5432 | Base `consorcio` y la de tests, `consorcio_test` |
| `seaweedfs` | `consorcio-seaweedfs` | 127.0.0.1:8333 | Bucket S3 de PDF originales |
| `mlflow` | `consorcio-mlflow` | 127.0.0.1:5000 | Trazas y métricas, sin login (ver `docs/observabilidad.md`) |

El código se monta como volumen, así que un cambio en Python se ve al reiniciar el proceso. Un cambio en
`pyproject.toml` pide `docker compose build`, y un cambio en `.env` pide `docker compose up -d --force-recreate app`.

**La web dentro de `app`, en el 8001**, con las variables solo para ese proceso:

```bash
docker compose exec -d -T \
  -e WEB_SELECTOR_USUARIOS=false -e WEB_SECRETO="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  -e LLM_PROVIDER=anthropic -e LLM_MODEL=claude-haiku-5-5 -e LLM_MODEL_VISION=claude-haiku-5-5 \
  -e ANTHROPIC_API_KEY=... \
  app uvicorn --factory consorcio.web.app:crear_app --host 0.0.0.0 --port 8001
```

## 6. Configuración (`.env`)

`.env.example` documenta cada variable. Las principales:

| Variable | Default | Qué hace |
|---|---|---|
| `LLM_PROVIDER` | `fake` | `google_genai`, `anthropic`, `openai`, `openrouter`, `ollama` o `fake` |
| `LLM_MODEL`, `LLM_MODEL_VISION` | vacío (default de `config.py`) | Modelo de texto y de visión. Por corrida: `--proveedor`, `--modelo`, `--modelo-vision` |
| `*_API_KEY` | — | Clave del proveedor elegido. Solo en `.env` |
| `FAKE_GUIONADO` | `false` | Con `fake`, contesta con guion (`agente/guion_fake.py`) en vez de con esquemas vacíos |
| `TOPE_USD_MENSUAL` | `20` | Tope de gasto por mes calendario UTC, para todo el sistema; 0 = sin tope |
| `MEMORIA_MENSAJES`, `AGENTE_MAX_MENSAJE` | `6`, `2000` | Historial que ve el router y largo máximo de un mensaje |
| `OCR_UMBRAL_TEXTO_CHARS`, `OCR_UMBRAL_CONFIANZA` | `200`, `75` | Desde cuántos caracteres una página tiene texto; desde qué confianza Tesseract alcanza |
| `OCR_PAGINAS_MUESTRA`, `OCR_ESCALAR_A_VISION`, `OCR_DPI` | `3`, `true`, `200` | Muestra del clasificador, escalar a visión, resolución |
| `POSTGRES_URL` | `…@postgres:5432/consorcio` | Base de trabajo. Los tests usan `consorcio_test` |
| `MLFLOW_TRACKING_URI`, `MLFLOW_EXPERIMENT` | `http://mlflow:5000`, `consorcio-ingesta` | Vacío = sin MLflow |
| `CONSORCIO_POR_DEFECTO` | `mercedes-274` | Solo la CLI y la evaluación lo usan |
| `WEB_SELECTOR_USUARIOS` | `false` | `true` = entrar como usuario demo sin contraseña. Solo desarrollo |
| `WEB_SECRETO` | vacío | Firma las cookies: aleatorio y de 32 caracteres o más. Obligatorio con el selector apagado |
| `WEB_MAX_MB_PDF`, `WEB_SESION_HORAS` | `20`, `8` | Tamaño máximo de un PDF y duración de la sesión |
| `JWT_SECRETO` | vacío | Solo para la API JSON del login (`make api`) |
| `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` | SeaweedFS local | Sin endpoint, la ingesta sigue sin guardar el crudo, con un warning |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHATS_PERMITIDOS` | vacío | Bot y lista blanca de chats |
| `DIR_PROCESADOS`, `DIR_ENTRADA` | `datos/procesados`, `datos/entrada` | Salidas de la ingesta y PDFs recibidos |

Los precios por millón de tokens, usados para el costo y el tope, están en `config.py`
(`PRECIOS_USD_POR_MILLON`). Un modelo sin precio no suma al tope.

## 7. Comandos

### Make

| Comando | Qué hace |
|---|---|
| `make build` / `up` / `down` / `logs` / `shell` | Imagen, servicios, logs, bash en `app` |
| `make test` / `make lint` | pytest sobre `consorcio_test` (la crea si falta) / `ruff check src tests` |
| `make ingestar PDF=... [METODO=auto\|texto\|tesseract\|vision]` | Tool 1 |
| `make preguntar Q="..."` | Tool 2: respuesta con cita verificada |
| `make expensas QUE=resumen\|rubros\|gastos\|unidades [PERIODO=AAAA-MM] [UNIDAD=5-D] [TEXTO=...]` | Liquidaciones por SQL, sin modelo |
| `make evaluar-consultas [PARTICION=dev\|test\|todas] [CON_MODELO=1]` | recall@3 del buscador y, con modelo, calidad de las respuestas |
| `make evaluar-ocr PDF=... REF=...` | Compara la transcripción contra una referencia hecha a mano |
| `make web` / `web-logs` / `web-down` | Web en el 8000 |
| `make sembrar-demo` | Consorcios y usuarios inventados, idempotente |
| `make alta-consorcio NOMBRE=... DIRECCION=... ADMIN=mail [ID=...]` | Crea el consorcio e imprime la invitación del administrador |
| `make alta-unidades CONSORCIO=... CSV=...` | Carga unidades (`piso,depto[,uf][,porcentual]`) |
| `make api` | API JSON del login en http://127.0.0.1:8002/docs |
| `make bot` / `bot-logs` / `bot-down` | Telegram |
| `make gasto` | Gasto del mes contra el tope |
| `make consultas [ORIGEN=...] [CONSORCIO=...] [N=20]` | Últimas consultas registradas |
| `make consorcios` / `make asignar CHAT=... A=...` | Bases y chats de Telegram |
| `make respaldar` / `make restaurar [DUMP=...]` | Volcado de Postgres en `datos/respaldo/` (ignorado por git) |
| `make db-actualizar` | Lleva la base a head con o sin versión de Alembic, sin tocar datos. Respaldar antes |
| `make migrar` | Migra una base anterior al 26/10/2026 al esquema con consorcios |
| `make psql` | Consola de Postgres |

### CLI (`consorcio ...`, dentro de `app`)

`ingestar`, `clasificar`, `evaluar-ocr`, `preguntar`, `expensas`, `evaluar-consultas`, `documentos`, `consorcios`,
`administraciones`, `migrar`, `actualizar-base`, `consultas`, `gasto`, `sembrar-demo`, `dibujar-grafo` y `bot`.
`consorcio <cmd> --help` muestra las opciones. Ejemplos:

```bash
docker compose exec app consorcio ingestar "datos/documentos/ACTA_MERCEDES_25-7-2024.pdf" --metodo vision
docker compose exec app consorcio ingestar "<pdf>" --paginas 1-5 --proveedor anthropic --modelo claude-haiku-5-5
docker compose exec app consorcio ingestar "<pdf>" --reusar-texto   # reestructura sin volver a pagar OCR/visión
docker compose exec app consorcio ingestar "<pdf>" --forzar         # reprocesa y reemplaza (también otro PDF del período)
docker compose exec app consorcio clasificar "<pdf>"                # solo la decisión del clasificador, sin modelo
docker compose exec app consorcio preguntar "¿Qué dice sobre el fondo de reserva?" --consorcio cabrera --json
docker compose exec app consorcio preguntar "..." --busqueda palabras   # baseline por palabras clave
docker compose exec app consorcio documentos --desactivar "ACTA_MERCEDES_25-7-2024"   # saca un doc del buscador
docker compose exec app consorcio administraciones --crear "NOMBRE"     # empresas administradoras y sus consorcios
docker compose exec app consorcio dibujar-grafo                         # mermaid de los grafos
```

## 8. Componentes

### 8.1 Ingesta (`ingesta/`, tool 1)

```
hash sha256 → ¿duplicado? → clasificar (sin modelo) ─┬→ extraer_texto      ─┐
                                                     ├→ extraer_tesseract  ─┼→ estructurar → guardar_disco → preparar_guardado → persistir
                                                     └→ extraer_vision     ─┘
```

- **`clasificador.py`:** decide sin modelo, página por página.
  - capa de texto de `OCR_UMBRAL_TEXTO_CHARS` caracteres o más → `texto`, gratis;
  - escaneo con confianza de Tesseract de `OCR_UMBRAL_CONFIANZA` o más → `tesseract`, gratis;
  - el resto → `vision`, con el modelo multimodal.
- **`estructurar.py`:** una llamada que devuelve título, tipo de documento, secciones, personas, organizaciones,
  direcciones, eventos, montos y datos sensibles. Los prompts están en `skills/ingesta/SKILL.md`.
- **`liquidacion.py`:** en las liquidaciones, el modelo extrae encabezado, rubros y gastos, y una regex parsea el
  prorrateo por unidad (unas 140 filas).
- **Freno a los bucles:**
  - toda llamada lleva `max_tokens` (16.000; visión 4.000);
  - `texto_degenerado()` manda a Tesseract las transcripciones en bucle;
  - hay un tope de llamadas por documento.
- **Resultado:** `final["estado"]` dice cómo terminó.
  - `procesado`;
  - `solo_disco`: con `--sin-persistir` o `--paginas`;
  - `duplicado`: ya estaba en ese consorcio y corta sin OCR ni modelo;
  - `en_proceso`: hay un candado `pg_try_advisory_lock`;
  - `periodo_duplicado`: ya hay una liquidación de ese período;
  - `tope_excedido`.
- **Documentos incompletos:** si falla `estructurar` y el documento queda sin secciones, se guarda con
  `estado='incompleto'`. Al subirlo de nuevo se reprocesa reusando el texto.
- **Qué deja:**
  - `datos/procesados/<archivo>-<hash>-<consorcio>[-pags-N-M]-<proveedor>-<modelo>/`, con `texto.md` y
    `extraccion.json`;
  - el PDF en S3, con la clave `<consorcio>/<sha256>.pdf`;
  - las filas en Postgres (§9) y un run en MLflow.

### 8.2 Consulta (`consulta/`, tool 2)

```
buscar (Postgres tsvector 'spanish', sin modelo) → extraer (≤ 3 citas textuales) → verificar → redactar
```

- **`buscador.py`:** busca con `tsvector` en español sobre el título, el tema y el resumen de `secciones`, solo en
  documentos `activo=true` del consorcio. El baseline es `--busqueda palabras`.
- **`extraer`:** el modelo devuelve hasta 3 citas textuales con su documento y página, más `puede_responder`.
- **`verificar`:** cada cita tiene que aparecer textualmente, una vez normalizada, en las páginas originales. Las
  que no aparecen se descartan; si no queda ninguna, deriva.
- **`redactar`:** una segunda llamada que ve solo la pregunta y las citas verificadas.
- **`verificar_respuesta`:** la piden el agente y la evaluación.
  - ningún número del texto libre puede faltar en las citas o las fuentes;
  - hay una cuota de palabras ajenas del 25 %, sin contar las de la pregunta;
  - la página se calcula desde el texto.
- **`evaluar.py`:** recall@k y calidad de las respuestas contra `datos/evaluacion/preguntas.csv`. Los resultados
  están en `docs/evaluaciones/`.

### 8.3 Agente (`agente/`)

- **Entrada:** `atender(mensaje, identidad, conversacion_id, canal)`. Nunca propaga excepciones.
- **Grafo:** `preparar` (sin modelo: conversación, mensaje y tope) → `router` (una llamada; `SalidaRouter` plana,
  con intención, pregunta reescrita, período, unidad, filtros y título/detalle del reclamo) → flujo → `registrar`.
- **Flujos** (`nodos.py`, registrados en `registro.py`):
  - `reglamento` y `documentos` → tool 2;
  - `expensas` → SQL;
  - `reclamo` → solicitud pendiente;
  - `derivar` → solicitud;
  - `fuera_de_alcance` → mensaje de bienvenida.
- **Otros módulos:**
  - `bandeja.py`: solicitudes, aprobación y resolución;
  - `memoria.py`: conversaciones;
  - `certeza.py`: medidor de 0 a 1 calculado en código;
  - `revision.py`: la administración califica las respuestas;
  - `panel.py` y `ficha.py`: vistas por consorcio;
  - `semilla.py`: datos de `sembrar-demo`.
- **Prompt:** `skills/agente/SKILL.md` (0.1.3). La consulta usa `skills/consulta/SKILL.md` (0.3.0) y la ingesta
  `skills/ingesta/SKILL.md` (0.2.0).

### 8.4 Web (`web/`)

FastAPI con Jinja y htmx. Es cliente de las tools y del agente. Para entrar hace falta sesión; sin ella, todo
redirige a `/entrar`, salvo `/salud`, `/entrar`, `/registro` y los estáticos.

| Ruta | Quién | Qué |
|---|---|---|
| `/entrar`, `/salir`, `/registro?token=` | todos | Login con mail y contraseña; alta desde una invitación |
| `/consorcios`, `/panel` | con membresía | Elegir consorcio y fichas; panel de quien administra varios |
| `/consorcios/nuevo` | quien puede crear consorcios | Alta de un consorcio; quien lo crea queda como administración |
| `/chat`, `/chat/mensaje`, `/chat/feedback`, `/chat/elevar` | vecinos | Chat con el agente, opinión sobre la respuesta y "Pasar a la administración" |
| `/mis-solicitudes` | vecinos | Reclamos y derivaciones propios, con la respuesta |
| `/documentos`, `/documentos/cargas` | todos (cargar: administración) | Listado; carga de hasta 10 PDFs en segundo plano con polling |
| `/bandeja`, `/bandeja/{id}/aprobar\|editar\|rechazar\|resolver` | administración | Solicitudes pendientes |
| `/revisar`, `/revisar/{turno_id}` | administración | Calificar respuestas del agente |
| `/consultar` | administración | Consultas sobre los documentos (k=5, sin crear solicitud) |
| `/invitaciones`, `/invitaciones/{id}/anular` | administración | Links de invitación (el token se ve una sola vez) |
| `/crm` | `usuarios.plataforma` | Métricas agregadas entre consorcios; el resto recibe 404 |
| `/salud` | todos | Chequeo de vida |

- **Seguridad:**
  - todo POST lleva CSRF;
  - CSP `default-src 'self'`, sin script ni style en línea;
  - la cookie es firmada con `WEB_SECRETO` y lleva `Secure` cuando el selector está apagado;
  - rol y membresía se resuelven en cada pedido.
- **`cargas.py`:** una cola con una sola hebra en el proceso web. Sirve para una sola instancia, y un reinicio
  pierde lo pendiente.

### 8.5 Login (`auth/`)

- **Cuentas:**
  - `cuentas.py` autentica, con bloqueo por intentos (`accesos`), y gestiona invitaciones;
  - en la base se guarda solo el hash del token;
  - `seguridad.py` hace Argon2 y JWT.
- **API JSON** (`make api`): `/login`, `/yo`, `/invitaciones`, `/registro`, `/{consorcio_id}/unidades` y `/salud`.
- **Alta por terminal:** `alta.py` (`make alta-consorcio`, `make alta-unidades`).
- **Frontera con el resto:** al sistema solo le llega un `sujeto_externo`, que
  `identidad.resolver_identidad(sujeto_externo, consorcio_id)` convierte en `Identidad` (rol principal y UF
  propias). Si no hay membresía, lanza `SinMembresia`. Ver `src/consorcio/auth/LEEME.md`.

### 8.6 Costos, tope y observabilidad

- **`llm.Contador`:** cuenta llamadas, tokens, segundos y USD por nodo. Se guarda en `llamadas_modelo`, en
  `extraccion.json` y en MLflow.
- **`gastos_modelo`:** libro de solo inserción con toda llamada real. `tope.py` lo suma contra `TOPE_USD_MENSUAL`.
  - corta el chat web y toda carga de PDF;
  - Telegram, la CLI y la evaluación cuentan, pero no se frenan;
  - se chequea antes de cada corrida, así que no reemplaza al tope de la consola del proveedor.
- **MLflow** (http://localhost:5000, experimento `consorcio-ingesta`): runs con métricas y trazas GenAI de
  LangGraph. Qué se guarda y quién lo ve está en `docs/observabilidad.md`.
- **Costo medido** con Claude Haiku 5.5 (25/11): unos 0,001 USD y 2 s por consulta.

### 8.7 Telegram (`canales/telegram.py`)

Está congelado hasta terminar la web. Usa long polling y las mismas tools, sin router (ver §12).

## 9. Modelo de datos

El esquema se versiona con Alembic (`migraciones/versions/0001` a `0011`). `crear_tablas()` sube a head solo una
base vacía; con otra frena y dice qué comando correr (`make db-actualizar`). Nunca se edita una revisión ya
aplicada: se agrega una nueva y se comprueba con `alembic check`.

| Grupo | Tablas |
|---|---|
| Consorcios | `consorcios`, `administraciones`, `chats` (Telegram → consorcio) |
| Documentos | `documentos` (`consorcio_id`, `sha256`, `crudo_clave`, `estado`, `activo`), `paginas`, `secciones` |
| Entidades | `personas`, `organizaciones`, `direcciones`, `eventos`, `unidades`, `montos`, `datos_sensibles` (`sensible=true`) |
| Expensas | `liquidaciones`, `liquidacion_rubros`, `liquidacion_gastos`, `liquidacion_unidades` |
| Usuarios | `usuarios` (Argon2, `activo`, `plataforma`), `unidades_funcionales`, `membresias` (rol: `administracion`, `consejo`, `propietario`, `inquilino`), `invitaciones`, `accesos` |
| Agente | `conversaciones`, `mensajes` (texto), `turnos` (solo métricas, certeza), `solicitudes` (derivaciones y reclamos), `revisiones` |
| Costos | `consultas`, `llamadas_modelo`, `gastos_modelo`, `cargas` |

El id de un documento es `<consorcio>:<hash>`, así que el mismo PDF en dos consorcios da dos documentos
independientes. `unidades_funcionales` son las unidades reales del consorcio; `unidades` son las que se extraen
de los documentos.

## 10. Pruebas

### 10.1 Unitarias e integración (pytest)

```bash
make test       # = docker compose exec -e POSTGRES_URL=.../consorcio_test app pytest -q
make lint
```

- **Base:** corren con el proveedor `fake`, sin tokens, sobre `consorcio_test`. Solo se permiten bases
  descartables (`BASES_DESCARTABLES`), porque varios tests borran la base.
- **S3:** `test_crudos.py` y parte de `test_duplicados.py` necesitan SeaweedFS y las variables `S3_*`; sin ellas
  se saltean. Para correr todo, sin salteados:

  ```bash
  docker compose exec -T \
    -e POSTGRES_URL=postgresql+psycopg://consorcio:consorcio@postgres:5432/consorcio_test \
    -e S3_ENDPOINT_URL=http://seaweedfs:8333 -e S3_ACCESS_KEY=consorcio -e S3_SECRET_KEY=consorcio123 \
    app sh -c "ruff check src tests && pytest -q"
  ```

- **Resultados y tiempos:**
  - la suite completa dio 943 passed y 0 skipped el 25/11, en unos 7 minutos;
  - con poca RAM, correrla en dos partes (`tests/test_[a-l]*.py` y `tests/test_[m-z]*.py`).
- **Qué cubre cada grupo:**
  - **ingesta y almacén:** `test_ingesta`, `test_duplicados`, `test_crudos`, `test_almacen`, `test_migraciones`;
  - **consultas y modelo:** `test_consulta`, `test_llm`;
  - **agente:** `test_agente`, `test_bandeja`, `test_calidad`, `test_elevar`, `test_arreglos_qa`;
  - **web y seguridad:** `test_web`, `test_web_seguridad`, `test_login_web`, `test_invitaciones_web`,
    `test_aislamiento` (nada cruza entre consorcios);
  - **login y altas:** `test_auth`, `test_alta`, `test_identidad`, `test_administraciones`;
  - **CRM, tope y observabilidad:** `test_crm`, `test_crm_web`, `test_tope`, `test_observabilidad`;
  - **otros:** `test_recorrido` (recorre todas las pantallas con los datos demo y el fake guionado), `test_semilla`,
    `test_telegram`, `test_entorno`.

### 10.2 Evaluación de consultas

- `make evaluar-consultas PARTICION=dev` mide el recall@3 del buscador (`tsvector` contra palabras clave), sin modelo.
- Con `CON_MODELO=1` y `PARTICION=test` mide además la calidad de las respuestas y deja
  `docs/evaluaciones/<fecha>-consultas-test.json`, que es lo que muestra el CRM.
- El plan de evaluación del agente está fijado en `docs/evaluaciones/2026-11-21-plan-agente.md`.

### 10.3 Punta a punta por HTTP (`scripts/e2e_web.py`)

Hace lo mismo que un navegador, solo con la biblioteca estándar:

1. entra como administración;
2. sube los PDFs de una carpeta (todos juntos; si pasan de 10, la web responde 422 y se mandan en lotes);
3. espera a que termine la cola;
4. hace cada pregunta de `preguntas.csv`: las de rol `vecino` por `/chat` y las demás por `/consultar`;
5. guarda `resultados-<ronda>.json`.

```bash
# web levantada en el 8001 con un modelo real (§5) y usuarios creados con membresía en el consorcio
E2E_CLAVE=... python -u scripts/e2e_web.py ronda1
E2E_CLAVE=... python -u scripts/e2e_web.py ronda2 --sin-carga
```

| Variable | Default |
|---|---|
| `E2E_BASE` | `http://127.0.0.1:8001` |
| `E2E_DOCS` | `datos/entrada/doc-manzanares`. Ignorada por git: los PDFs y `evaluacion/preguntas.csv` no vienen con el repo |
| `E2E_CONSORCIO` | `manzanares` |
| `E2E_ADMIN`, `E2E_VECINO` | usuarios de prueba de administración y vecino |
| `E2E_CLAVE` | contraseña de ambos (obligatoria) |

El método, los criterios y los resultados de las rondas 1 a 5 están en
`docs/evaluaciones/2026-11-24-manzanares.md`.

## 11. Probarlo por la web

Para probarla sin clave ni datos reales, agregar al `.env`:

```bash
LLM_PROVIDER=fake
FAKE_GUIONADO=true           # sin esto, el fake deriva casi todo
WEB_SELECTOR_USUARIOS=true   # usuarios demo sin contraseña; solo desarrollo, nunca expuesto a una red
```

```bash
make db-actualizar           # con una base existente, después de make respaldar
make sembrar-demo            # sin modelo, sin bucket y sin datos/
make web                     # http://127.0.0.1:8000
```

`sembrar-demo` crea dos consorcios inventados, `demo-palmeras` y `demo-robles`, cada uno con reglamento, acta y dos
liquidaciones. Salvo el de plataforma, los usuarios también entran con `<usuario>@demo.test` y la contraseña
`demo-consorcio-2026`:

| Usuario | Qué ve |
|---|---|
| `demo-admin-palmeras` | Administración de Palmeras: bandeja, carga de PDF, consultar, revisar, invitar |
| `demo-prop-palmeras` | Propietario de la UF 3 de Palmeras: chat, documentos y sus solicitudes |
| `demo-prop-robles` | Propietario de la UF 1 de Robles: lo mismo, en otro consorcio |
| `demo-multi` | Propietario en Palmeras y administración en Robles: elige consorcio al entrar |
| `demo-plataforma` | Equipo de plataforma, sin membresías: va directo a `/crm` |

**Para probar:**

1. preguntar por el reglamento o las expensas;
2. pedir un listado de documentos;
3. cargar un reclamo;
4. aprobarlo desde la bandeja con el administrador;
5. ver la respuesta en "Mis solicitudes".

**Cuentas reales.** Una cuenta real se crea desde una invitación:

- la del administrador sale de `make alta-consorcio`;
- las demás, de `/invitaciones` o de la API.

Con `WEB_SELECTOR_USUARIOS=false`, la web exige `WEB_SECRETO`. Las cookies llevan `Secure`: los navegadores las
aceptan en `http://127.0.0.1`, pero fuera de la máquina local hace falta HTTPS.

## 12. Probarlo por Telegram

Telegram está congelado hasta terminar la web: no usa el router.

**Qué hace:**

- un PDF adjunto dispara la ingesta;
- un texto o `/preguntar` va a la consulta;
- `/expensas` consulta las liquidaciones por SQL y `/documentos` lista los documentos.

**Cómo se levanta:**

```bash
# 1. Crear el bot con @BotFather y poner el token en .env (TELEGRAM_BOT_TOKEN=...). El token no va al repo.
make bot && make bot-logs
# 2. Mandar /start: contesta la ayuda y el id del chat. Ponerlo en TELEGRAM_CHATS_PERMITIDOS
#    (vacío = responde a cualquiera, solo para probar) y recrear:
docker compose --profile bot up -d --force-recreate bot
```

**Bases y pedidos:**

- Cada chat tiene su propia base: la primera vez se crea un consorcio vacío, `chat-<id>` o `grupo-<id>`.
- `make asignar CHAT=<id> A=<consorcio>` hace que varios números compartan un consorcio.
- Los pedidos se atienden de a uno, y las consultas quedan en `consultas` con `origen=telegram`.
- En los grupos, el *privacy mode* de BotFather impide que el bot vea los mensajes comunes. Se puede desactivar
  con `/setprivacy` o usar `/preguntar ...`.

## 13. Entornos existentes y traspaso

**Si recibís el proyecto.** Para probarlo sin reingestar nada, porque la ingesta completa con una cuota gratuita
lleva horas:

```bash
cp .env.example .env
docker compose build && docker compose up -d
make restaurar          # sobre una base vacía: carga el último datos/respaldo/postgres-<fecha>.sql
make db-actualizar      # sirve para cualquier volcado, tenga o no versión de Alembic
make test && make lint
make expensas QUE=rubros PERIODO=2026-07
make evaluar-consultas PARTICION=dev
```

- **Volcado:** está fuera de git, porque tiene datos personales, y se comparte por fuera.
- **`make restaurar`:** no reinicia `alembic_version`, así que tiene que ir sobre una base vacía.
- **Modelo real:** hace falta una clave en `.env`. La opción gratuita es Ollama cloud (`LLM_PROVIDER=ollama`,
  `LLM_MODEL=gpt-oss:120b`, `LLM_MODEL_VISION=gemma4:31b`, un pedido a la vez).
- **Cuotas:** la gratuita de Gemini es de 20 pedidos por día y por modelo, y no alcanza para una evaluación. Con
  una clave paga, Claude Haiku 5.5 fue la mejor relación entre calidad y costo.

**Base anterior al 01/11.** Desde el 01/11 el esquema va con Alembic, y hasta migrar, la app, la web y el bot
frenan con un error que indica el comando:

```bash
docker compose build && docker compose up -d
make respaldar
make migrar            # solo si la base es anterior al 26/10
make db-actualizar
```

**Neo4j.** Se quitó el 01/11. Un entorno viejo conserva el contenedor `consorcio-neo4j` y el volumen
`<proyecto>_neo4jdata`. Se borran a mano con `docker rm -f consorcio-neo4j` y `docker volume rm <proyecto>_neo4jdata`,
y lo que tenga el volumen no se puede recuperar. No se pierde nada que use el agente, porque las entidades están en
Postgres.

**Windows sin `make`.** Usar el comando del `Makefile`, por ejemplo
`docker compose exec app consorcio ingestar "ruta.pdf" --metodo auto`. Con Git Bash, anteponer `MSYS_NO_PATHCONV=1`
a los `docker compose exec` que llevan rutas.

## Integrantes

Pendiente.
