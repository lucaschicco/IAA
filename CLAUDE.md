# CLAUDE.md

Guía para agentes (Claude Code u otros) que trabajan en este repo. Los detalles técnicos completos están en
`README.md`. Esto es lo que hay que saber antes de tocar código.

## 1. Qué es

TP grupal de Inteligencia Artificial Agéntica (Maestría en Data Mining, UBA, Grupo 6). Es un asistente para la
administración de consorcios:

- responde consultas sobre reglamento, actas y expensas citando la fuente;
- registra reclamos y deriva lo que no puede resolver a una persona;
- en las acciones importantes, la administración revisa en una bandeja (human-in-the-loop).

**Estado (27/11/2026):**

- **Funciona:**
  - ingesta de PDF, incluidas las liquidaciones de expensas;
  - consultas con cita verificada, en dos pasos;
  - agente con router;
  - web con login, carga masiva, bandeja, revisión, consultas de la administración y CRM.
- **Fuera de esta etapa:** facturas, proveedores, calendario y WhatsApp.
- **Telegram está congelado:** no cambiar su comportamiento ni pasarlo por el router.

Idioma del proyecto: **español** en docs, nombres, mensajes de commit y textos de la UI. Mantenerlo.

## 2. Mapa del repo

| Ruta | Qué hay |
|---|---|
| `src/consorcio/` | El paquete. El mapa de módulos está en `src/LEEME.md` (actualizarlo al agregar uno) |
| `skills/{ingesta,consulta,agente}/SKILL.md` | Prompts versionados (frontmatter `version:`). Los prompts no van en el código |
| `migraciones/versions/` | Alembic, de `0001_base` a `0011_administraciones`. Toda tabla o columna nueva lleva una revisión |
| `tests/` | pytest (unos 940 tests). Usan el proveedor `fake`: no gastan tokens |
| `scripts/e2e_web.py` | Prueba de punta a punta contra la web levantada (carga masiva y preguntas como un navegador) |
| `datos/` | Documentos **reales** del consorcio Mercedes 274, normativa, preguntas de evaluación (ver §9) |
| `docs/` | `poc.md` (cómo funciona hoy), `informe.md`, `bitacora.md`, `evaluaciones/`, `diseno/` (spec de la web), `observabilidad.md`, `fuentes.md`, `consigna.md`, `propuesta/` |
| `docker-compose.yml`, `Dockerfile`, `Makefile` | Entorno: todo corre en Docker |

## 3. Cómo levantarlo

Todo corre en Docker. El proyecto de Compose se llama `iaa` (volúmenes `iaa_pgdata` e `iaa_seaweeddata`).

```bash
cp .env.example .env              # LLM_PROVIDER=fake corre sin clave
docker compose build
docker compose up -d              # app (sleep infinity), postgres, seaweedfs, mlflow
make db-actualizar                # sobre una base existente; una vacía se crea sola en head
make sembrar-demo                 # consorcios y usuarios inventados (demo-*), idempotente
make web                          # http://127.0.0.1:8000 (servicio `web`, perfil web)
```

| Servicio | Contenedor | Puerto (solo 127.0.0.1) |
|---|---|---|
| app (CLI, tests, web a mano) | `consorcio-app` | 8001 (web a mano) y 8002 (API del login, `make api`) |
| web (`make web`) | `consorcio-web` | 8000 |
| postgres 16 | `consorcio-postgres` | 5432 |
| seaweedfs (S3 de PDFs crudos) | `consorcio-seaweedfs` | 8333 |
| mlflow | `consorcio-mlflow` | 5000 |
| bot de Telegram (`make bot`) | `consorcio-bot` | — |

**Web dentro de `app`, en el 8001**, con variables solo para ese proceso y sin tocar `.env`. Es lo que se usó en
las pruebas de punta a punta:

```bash
docker compose -p iaa exec -d -T \
  -e WEB_SELECTOR_USUARIOS=false -e WEB_SECRETO=<aleatorio de 32+> \
  -e LLM_PROVIDER=anthropic -e LLM_MODEL=claude-haiku-5-5 -e LLM_MODEL_VISION=claude-haiku-5-5 \
  -e ANTHROPIC_API_KEY=... \
  app uvicorn --factory consorcio.web.app:crear_app --host 0.0.0.0 --port 8001
```

- El contenedor no tiene `kill`. Para reiniciar ese proceso: `docker compose exec app python -c "import os; os.kill(<pid>, 15)"`.
- Si cambia el `.env`: `docker compose up -d --force-recreate app`. Compose inyecta las variables al arrancar.
- Windows con Git Bash: anteponer `MSYS_NO_PATHCONV=1` a los `docker compose exec` que llevan rutas.

## 4. Cómo correr las pruebas

```bash
make lint                         # ruff check src tests
make test                         # crea consorcio_test si falta y corre pytest -q ahí
```

- Los tests **solo** corren sobre `consorcio_test` (`BASES_DESCARTABLES`): varios la borran. No apuntarlos nunca
  a `consorcio`.
- Los tests de S3 (`test_crudos.py`, parte de `test_duplicados.py`) se saltean sin `S3_*`. Para que no se salteen,
  seaweedfs tiene que estar arriba (`docker start consorcio-seaweedfs`) y hay que pasar las variables:

  ```bash
  MSYS_NO_PATHCONV=1 docker compose -p iaa exec -T \
    -e POSTGRES_URL=postgresql+psycopg://consorcio:consorcio@postgres:5432/consorcio_test \
    -e S3_ENDPOINT_URL=http://seaweedfs:8333 -e S3_ACCESS_KEY=consorcio -e S3_SECRET_KEY=consorcio123 \
    app sh -c "ruff check src tests && pytest -q"
  ```

- Dura unos 7 minutos. Con poca memoria, el proceso puede morir: correrlo en dos mitades
  (`pytest -q tests/test_[a-l]*.py` y `pytest -q tests/test_[m-z]*.py`).
- Un archivo: `docker compose exec -e POSTGRES_URL=... app pytest -q tests/test_consulta.py -k cita`.
- Última corrida completa (25/11): 943 passed, 0 skipped.
- `alembic check` tiene que dar sin diferencias después de agregar una migración.

## 5. Cómo probarlo a mano y de punta a punta

- **Sin clave:** con `LLM_PROVIDER=fake`, `FAKE_GUIONADO=true` y `WEB_SELECTOR_USUARIOS=true`, `make sembrar-demo`
  y `make web`. En `/entrar` se elige un usuario `demo-*`. Las respuestas salen de `agente/guion_fake.py`.
- **CLI con modelo real:**
  - `make preguntar Q="..."`;
  - `make ingestar PDF="..."`;
  - `make expensas QUE=rubros PERIODO=2026-07`;
  - `make evaluar-consultas PARTICION=dev [CON_MODELO=1]`.
- **Punta a punta por HTTP:** `E2E_CLAVE=... python -u scripts/e2e_web.py rondaN [--sin-carga]`.
  - Necesita la web en el 8001 (o en `E2E_BASE`) y los usuarios de prueba creados.
  - Lee los PDFs y `evaluacion/preguntas.csv` de `E2E_DOCS`, por defecto `datos/entrada/doc-manzanares/`, que
    está ignorada por git.
  - Deja `resultados-<ronda>.json` en esa misma carpeta.
  - El método y los resultados están en `docs/evaluaciones/2026-11-24-manzanares.md`.
- **Navegador:** la cookie de sesión es `Secure` cuando el selector está apagado. Chrome la acepta en
  `http://127.0.0.1`, pero un cliente HTTP propio no la manda: el script usa una política de cookies propia.

## 6. Arquitectura

Grafos de LangGraph (estado tipado, nodos, aristas condicionales). Se dibujan con `consorcio dibujar-grafo`.

- **Ingesta** (`ingesta/grafo.py`):
  - pasos: hash (antes de todo) → `clasificar` (sin modelo) → `extraer_texto | extraer_tesseract | extraer_vision`
    → `estructurar` (modelo; `liquidacion.py` para expensas) → `guardar_disco` → `preparar_guardado` → `persistir`;
  - hay un candado por PDF y consorcio (`pg_try_advisory_lock`).
- **Consulta** (`consulta/grafo.py`):
  - pasos: `buscar` (Postgres `tsvector` en español, sin modelo) → `extraer` (hasta 3 citas textuales) →
    `verificar` (cada cita tiene que estar en el texto original; si no queda ninguna, deriva) → `redactar`
    (ve solo la pregunta y las citas verificadas);
  - después, `verificar_respuesta`: ningún número fuera de las fuentes y una cuota de palabras ajenas del 25 %,
    descontando las de la pregunta.
- **Agente** (`agente/grafo.py`):
  - entrada única `atender(mensaje, identidad, conversacion_id, canal)`, que nunca propaga excepciones;
  - pasos: `preparar` → `router` (una llamada, `SalidaRouter`) → `reglamento | expensas | documentos | reclamo |
    derivar | fuera_de_alcance` → `registrar`;
  - una funcionalidad nueva se agrega en `agente/registro.py`.
- **Web** (`web/`):
  - FastAPI, Jinja y htmx vendorizado; es cliente de las tools y no tiene lógica de negocio;
  - rutas en `rutas.py`, `crm_rutas.py` y `invitaciones.py`;
  - la carga de PDFs va en segundo plano (`cargas.py`): una hebra y una cola en memoria del proceso
    (`shortcut:`), así que un reinicio pierde lo pendiente.
- **Login** (`auth/`): mail y contraseña con Argon2, invitaciones por token (en la base va solo el hash) y una API
  JSON con JWT. Al resto del sistema solo le pasa un `sujeto_externo`, que `identidad.resolver_identidad`
  convierte en `Identidad`.

## 7. Reglas que no se rompen

- **Aislamiento por consorcio.**
  - La capa de datos y las tools exigen `consorcio_id` (`exigir_consorcio`, ValueError si falta).
  - Solo la CLI y la evaluación resuelven `CONSORCIO_POR_DEFECTO`.
  - Nada cruza entre consorcios, salvo el CRM, que solo muestra agregados y es solo para `usuarios.plataforma`;
    el resto recibe 404.
- **Importes:** salen de SQL, nunca del modelo.
- **Citas:** toda respuesta de documentos cita textualmente y se verifica en código; si no se puede, deriva.
- **Acciones:** los reclamos solo crean una solicitud `pendiente`, y la administración aprueba, edita o rechaza.
  Lo que propone el modelo queda en `titulo`/`detalle` y lo humano en `titulo_final`/`detalle_final`.
- **Texto y métricas separados:** `mensajes` tiene texto; `turnos` solo métricas, y es lo que lee el CRM.
  `crm.py` no importa `Mensaje`.
- **Proveedor de modelo:**
  - siempre a través de `llm.py` (`init_chat_model`, `invocar_estructurado`); no acoplar código a un SDK;
  - `LLM_PROVIDER` puede ser `google_genai`, `anthropic`, `openai`, `openrouter`, `ollama` o `fake`;
  - para `ollama` y `openrouter`, la salida estructurada se pide por prompt;
  - `_reparar_listas_como_texto` arregla listas que el modelo manda como texto JSON.
- **Costo:**
  - `Contador` registra llamadas, tokens, segundos y USD por nodo;
  - `TOPE_USD_MENSUAL` (`tope.py`) bloquea `atender` e `ingestar_pdf`;
  - toda llamada lleva `max_tokens`, y `texto_degenerado()` corta las transcripciones en bucle.
- **Duplicados:**
  - `final["estado"]` es `procesado`, `solo_disco`, `duplicado`, `en_proceso`, `periodo_duplicado` o
    `tope_excedido`;
  - un documento con `estado='incompleto'` (falló `estructurar` y quedó sin secciones) se reprocesa al subirlo de
    nuevo, reusando el texto;
  - `--forzar` reprocesa y reemplaza.
- **Esquema:**
  - nunca editar una revisión ya aplicada: agregar una nueva;
  - `crear_tablas()` solo sube a head una base vacía, y con cualquier otra frena con RuntimeError y el comando a
    correr;
  - antes de migrar una base con datos: `make respaldar`.
- **Web:**
  - todo POST lleva CSRF;
  - la CSP es `default-src 'self'`: nada de `script` o `style` en línea ni `|safe`;
  - los tonos van en variables de `web.css` y los íconos en `_iconos.html`;
  - el menú se abre con CSS, sin scripts propios.
- **Skills:** al cambiar un prompt, subir el `version:` del SKILL.md y el test que lo espera
  (`test_agente.py` espera la versión del skill del agente) y anotar la falla que lo motivó en la bitácora.

## 8. Bases y datos sensibles

| Base | Qué es | Cuidado |
|---|---|---|
| `consorcio` | Base de trabajo: Mercedes 274, Cabrera, Manzanares y demo. Hoy es de pruebas | Respaldar antes de cambios destructivos (`make respaldar` → `datos/respaldo/`, ignorado). No borrar Mercedes ni Cabrera sin preguntar |
| `consorcio_test` | La de los tests | Se borra en cada corrida |
| `consorcio_local` | Copia de datos reales | No tocar |

- **El repo es público y `datos/` tiene datos personales reales.** No copiar su contenido a servicios externos ni
  a otros repos.
- No commitear `datos/respaldo/*.sql`, `datos/procesados/*` ni `datos/entrada/*`: ya están en `.gitignore`.
- Las claves viven solo en `.env`, que está ignorado. No escribirlas en docs, commits ni respuestas.
- En los documentos de evaluación no van nombres de vecinos ni direcciones reales.

## 9. Datos (`datos/`)

| Carpeta | Contenido |
|---|---|
| `normativa/` | Leyes 941 y 3254, modelo de reglamento PH, manual de prácticas consorciales |
| `documentos/` | Reglamento de copropiedad (60 pág.), reglamento de cocheras, 4 actas y 2 convocatorias de Mercedes 274 |
| `expensas/` | 8 liquidaciones, de feb a sep de 2026 (el nombre es la fecha de emisión; cada una liquida el mes anterior) |
| `facturas/` | 29 comprobantes y el listado de gastos de agosto de 2026 (ground truth para cuando se haga facturas) |
| `evaluacion/` | `preguntas.csv` (consultas, partición dev/test) y `mensajes_agente.csv` (set del agente) |
| `entrada/` | PDFs recibidos por la web o Telegram. Ignorada; ahí están también los docs de Manzanares |
| `procesados/` | Lo que deja cada ingesta: `texto.md` y `extraccion.json`. Ignorada |

## 10. Documentación que hay que mantener

- `docs/bitacora.md`: una entrada con fecha ante cada decisión, falla o bloqueo relevante.
- `docs/poc.md`: diagramas y paso a paso. Actualizarlo cuando cambie un grafo, un canal o los resultados.
- `docs/evaluaciones/`: un archivo por evaluación, con fecha. Los resultados tienen que poder reproducirse desde
  el repo.
- `README.md`: referencia técnica y checklist de próximos pasos.
- `src/LEEME.md`: mapa de módulos.
- `docs/informe.md`: informe final, una sección por ítem de la consigna.

## 11. Git

- Commits chicos y descriptivos, en español. La consigna pide una historia distribuida, no "Add files via upload".
- No hacer push ni merge a `main` sin que lo pida el usuario.
- Remoto: `origin` = `github.com/lucaschicco/IAA`, que es público.
- Ramas:
  - `feat/diseno-web`: trabajo actual;
  - `integracion/login`;
  - `mlflow/trazas-y-evaluacion`, de otra integrante.
- Revisar que no viajen claves, mails reales ni datos de `datos/entrada` antes de cada push.
