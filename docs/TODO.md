# TODO

Armado el 27/11/2026 a partir de:

- la calificación del QA de la ronda 5 con Manzanares: 47 preguntas, con 31 correctas, 3 parciales, 12 que
  derivaron cuando debían responder, 0 incorrectas y 1 dato de prueba inconsistente;
- los pendientes anotados en `docs/evaluaciones/2026-11-24-manzanares.md`, `docs/diseno/README.md`,
  `docs/observabilidad.md`, `docs/evaluaciones/2026-11-21-plan-agente.md`, `docs/informe.md` y el README.

La entrega es la primera semana de diciembre y la exposición, el 10 u 11/12.

- **Prioridades:**
  - **P1:** antes de la entrega;
  - **P2:** si hay tiempo;
  - **P3:** después del TP.
- **Al cerrar una tarea:** tildarla, anotar la fecha y sumar el caso al set de regresión.

## 1. Lo que encontró el QA (ronda 5)

Los ids son de `datos/entrada/doc-manzanares/evaluacion/preguntas.csv`. Esa carpeta está fuera de git: la
descripción de cada caso va acá.

### 1.1 Actas escaneadas: derivan 8 de las 12

- [ ] **P1. Preguntas de resumen sobre un acta.**
  - Casos:
    - "resumime el acta del 12/12/2023" (A01);
    - "qué temas se trataron en enero de 2024" (V26);
    - "qué se aprobó sobre la indemnización en agosto de 2023" (A02).
  - Problema: la búsqueda devuelve secciones sueltas y el modelo no junta 3 citas que alcancen.
  - Idea: si la pregunta nombra un acta por fecha o pide un resumen, traer el documento entero, o todas sus
    secciones, en vez de k secciones.
- [ ] **P1. Comparar dos actas** (A03, la portería entre agosto de 2023 y enero de 2024): hay que recuperar un
  documento por cada fecha nombrada.
- [ ] **P2. Datos puntuales de un acta.**
  - Casos:
    - "a qué hora empezó la asamblea del 12/12/2023" (V18);
    - "cuándo era la asamblea reprogramada en agosto de 2023" (V30);
    - "cuándo hay asamblea" (V03).
  - Revisar qué secciones devuelve el buscador antes de tocar el prompt.
- [ ] **P2. Errores de lectura del escaneo que llegan a la cita.**
  - En A04 la cita copia la fecha "29/03/2023", que en realidad es el 29/08, y otro apellido.
  - Además, el encabezado del acta del 29/08/2023 dice "29 de Marzo".
  - Hay que revisar a mano la transcripción de las actas de Manzanares, como ya está pendiente para las de
    Mercedes 274.

### 1.2 Búsqueda: vocabulario y forma de preguntar

- [ ] **P1. Sinónimos o embeddings.**
  - "perro" no encuentra "animales" (V19) y "no puedo ir a la asamblea" no encuentra "carta poder" (V21).
  - Hay que medir contra el baseline con `make evaluar-consultas` antes de decidir. En test, palabras clave le
    ganó a `tsvector` (0,917 contra 0,583 de recall@3).
  - Las opciones están en orden de costo:
    1. una lista chica de sinónimos del dominio;
    2. pesos por título;
    3. embeddings con pgvector, con un modelo multilingüe chico, por ejemplo `multilingual-e5-small`.
- [ ] **P1. Mismo dato, otra pregunta.** "cuál es el presupuesto de la obra" deriva (V04), pero "cuánto salió"
  responde.
- [ ] **P2. Preguntas que cruzan documentos** (V31, si la portería puede ser un consultorio): necesita el reglamento
  y un acta en la misma respuesta.
- [ ] **P2. Respuestas de lista incompletas.**
  - Casos:
    - trabajos del presupuesto, donde lista solo una parte (V05);
    - requisitos para una obra en la unidad, que omite la autorización de la asamblea y el profesional
      matriculado (A06).
  - Con 3 citas no entra una lista larga. Probar más citas cuando la pregunta pide "qué requisitos" o "qué
    trabajos".
- [ ] **P1. Sumar los 12 casos que derivan como regresión.** Llevarlos a `datos/evaluacion/preguntas.csv` sin
  datos personales, o como tests con documentos inventados en `sembrar-demo`.

### 1.3 Expensas en `/consultar`

- [ ] **P1. Importes en `/consultar`** (A11): la administración pregunta cuánto paga la 4-B en octubre de 2025 y
  la pantalla deriva, porque solo busca en documentos.
  - Opción a: pasar la pregunta por el router, como el chat, pero sin crear la solicitud.
  - Opción b: detectar unidad y período y consultar por SQL.

### 1.4 Datos de prueba

- [ ] **P1. Unidad de la vecina de prueba** (V34): está cargada en el 7-F, que no existe en la liquidación
  sintética (de 1-A a 7-D). Cambiarle la unidad o sumar el 7-F a la liquidación.
- [ ] **P1. Limpiar Manzanares.** Las rondas 3 a 5 dejaron 38 derivaciones y 9 reclamos. Respaldar
  (`make respaldar`) y decidir si se borran.
- [ ] **P2. Reingestar con Haiku.** 8 documentos de Manzanares siguen estructurados por Gemini; con Haiku solo se
  reprocesaron los que fallaron. Reingestarlos con `--forzar`, para que la evaluación use un solo modelo.

## 2. Errores del proveedor y robustez (hallazgos del 24/11)

- [ ] **P1. Cortar enseguida con un 429 de cuota diaria.** Hoy se reintenta unos 37 s antes de fallar, y un límite
  diario no se resuelve reintentando.
- [ ] **P1. Un error del proveedor no tiene que crear una derivación.**
  - Pasó en las rondas 1 y 2: dejaron 70 solicitudes en la bandeja, que después se borraron.
  - El vecino tiene que ver "probá más tarde" y el turno registrarse como `error`.
- [ ] **P2. Cola de cargas.** Vive en el proceso web (`shortcut:` en `web/cargas.py`): un reinicio pierde lo
  pendiente y solo sirve para una instancia. Como mínimo, al arrancar, marcar como fallidas las cargas que
  quedaron a medias.
- [ ] **P2. Avisar cuando termina una carga**, por mail o mensaje. No hay SMTP; hoy el estado solo se ve en
  Documentos.

## 3. Integración con las otras ramas

- [ ] **P1. Traer `origin/mlflow/trazas-y-evaluacion`.** Tiene 9 commits nuevos que no están en `feat/diseno-web`
  y resuelven pendientes de esta lista; no hay que rehacerlos:
  - `consorcio evaluar-agente`, con el set de 36 mensajes y el baseline por palabras clave;
  - MLflow fijado en la 3.18;
  - datos personales tapados en las trazas, que es la opción B de `docs/observabilidad.md`.
- [ ] **P1. Abrir un PR de `feat/diseno-web` contra `main`** y revisarlo con el equipo. Esta rama le lleva 47 commits
  a `origin/main`.

## 4. Evaluación y costos (para el informe)

- [ ] **P1. Evaluación del agente** según `docs/evaluaciones/2026-11-21-plan-agente.md`: correr `evaluar-agente`
  en dev y después una sola vez en test, sin ajustar nada mirando test.
- [ ] **P1. Evaluación de consultas en test** con un modelo real: `make evaluar-consultas PARTICION=test CON_MODELO=1`.
- [ ] **P1. Costo y latencia por tarea**, con MLflow y `gastos_modelo`. Ya hay una medición con Haiku 5.5: unos
  0,001 USD y 2 s por consulta. Falta la ingesta por documento y por método.
- [ ] **P2. Contrastar el tope de gasto con una factura real** del proveedor.
- [ ] **P2. Juez automático:** otro modelo que revisa si la respuesta se apoya en la fuente, más casos de prueba
  armados con las revisiones de la administración.

## 5. Informe y exposición

`docs/informe.md` tiene secciones marcadas "Falta". Completar:

- [ ] **P1. §1 Problema:** si apuntamos a consorcios chicos o a administraciones profesionales.
- [ ] **P1. §2 Por qué un modelo de lenguaje:** completar la comparación y sumar algunos papers.
- [ ] **P1. §4 Datos:** dejar el estado actual (OCR hecho, liquidaciones en tablas, preguntas armadas).
- [ ] **P1. §5 Cómo funciona:** diagrama, elección de modelos y orden de construcción. Se puede tomar de
  `docs/poc.md`.
- [ ] **P1. §6 Búsqueda y §7 Expensas:** se completan con lo que está construido.
- [ ] **P1. §8 Evaluación y §9 Costos:** con los resultados de la sección 4 de este TODO.
- [ ] **P1. §10 Límites y próximos pasos:** la sección 1 de este TODO es la base.
- [ ] **P1. Presentación** y guion de la demo en vivo.
- [ ] **P1. Integrantes** en el README.

## 6. Privacidad del repo público

- [ ] **P1. Muestra sanitizada en `datos/`.** `datos/` tiene datos personales reales de Mercedes 274 y la consigna
  pide una muestra sanitizada para replicarlo. Decidir qué se reemplaza por sintético antes de la entrega.
- [ ] **P1. Decisiones de MLflow pendientes** (`docs/observabilidad.md`):
  - quién entra;
  - si la ingesta sigue subiendo `texto.md` y `extraccion.json`;
  - si se borran las trazas viejas con datos reales.
- [ ] **P1. Rotar la clave de Anthropic** que se usó en las pruebas del 25/11. Se pasó solo al proceso, pero quedó
  en un archivo local de la sesión.

## 7. Web: pendientes de diseño (`docs/diseno/README.md` §7)

- [ ] **P2. Pantalla de vecinos y unidades:** hoy se cargan con `make alta-unidades`.
- [ ] **P2. "Olvidé mi contraseña":** necesita SMTP.
- [ ] **P2. Etapas de progreso** al cargar un PDF.
- [ ] **P2. Tarjeta de expensas en el chat.**
- [ ] **P3. Datos para las tarjetas del CRM que faltan:** series semanales y últimas revisiones.
- [ ] **P3. Nombre y logo definitivos.**

## 8. Fuera de esta etapa

- [ ] **P3. Facturas:** extraer los datos y compararlos contra el listado de gastos de agosto, que es el ground
  truth.
- [ ] **P3. Proveedores:** búsqueda por rubro y comparación de presupuestos.
- [ ] **P3. WhatsApp:** necesita número de negocio, verificación de Meta y un webhook público.
- [ ] **P3. Telegram por el router:** hoy está congelado.
- [ ] **P3. Tabla de unidades de Mercedes 274.**
