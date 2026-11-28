# Evaluación 26/10/2026: corpus ingestado, liquidaciones a tabla y consultas con cita

Segunda jornada de medición. Objetivo: tener toda la base cargada, una primera versión de la
tool de consultas con cita verificada, las liquidaciones de expensas en tablas y un set de
preguntas dev/test para medir retrieval y generación por separado.

## 0. Con qué modelo se corrió (y por qué)

La clave de Anthropic venció y el tier gratuito de AI Studio permite **20 pedidos por día** en
`gemini-2.5-flash`, que se agotaron a mitad del lote. OpenRouter ya no sirve gratis los modelos
que conocíamos. Quedó **Ollama cloud (plan gratuito)**: `gpt-oss:120b` para texto y
estructuración, `gemma4:31b` para visión, de a un pedido por vez. Como esos modelos no soportan
tool calling confiable, `invocar_estructurado` ahora pide el JSON Schema en el prompt y valida
con pydantic cuando el proveedor es `ollama` u `openrouter`.

Consecuencia para el informe: los números de hoy miden el **sistema**, no el mejor modelo. Con
una clave paga se reingesta todo con Haiku 5.5 (el hash del archivo pisa lo anterior) y se
vuelve a correr `consorcio evaluar-consultas`.

## 1. Corpus ingestado

| Documento | Método | Págs. | Modelo | Llamadas | Seg. | Secciones | Observaciones |
|---|---|---|---|---|---|---|---|
| Convocatoria 3/12/2025 | texto | 1 | Gemini 2.5 Flash | 2 | 15 | 6 | |
| Convocatoria 5/05/2025 | texto | 1 | Gemini 2.5 Flash | 2 | 18 | 7 | incluye el modelo de autorización |
| Reglamento de cocheras | texto | 2 | Gemini 2.5 Flash | 2 | 21 | 13 | un artículo por sección |
| Acta 25/07/2024 (manuscrita) | visión | 2 | Gemini 2.5 Flash | 4 | 70 | 6 | |
| Acta 3/12/2025 | visión | 3 | Gemini 2.5 Flash | 5 | 96 | 7 | |
| Acta 5/05/2025 | visión | 4 | Gemini 2.5 Flash | 6 | 135 | 7 | |
| Acta 30/04/2026 | visión | 7 | gemma4 + gpt-oss (Ollama) | 10 | 55 | 15 | transcribe los artículos del reglamento de cocheras que convalida: quedan duplicados con el reglamento |
| Contrato Cabrera | tesseract | 42 | gpt-oss (Ollama) | 10 | 212 | 19 | 1er intento: un lote devolvió JSON con un salto de línea dentro de una cadena y se perdió todo (0 secciones). 2do intento, con parseo tolerante y lotes independientes: 19 secciones, 15 eventos, 17 unidades, 14 organizaciones; 2 de 9 lotes descartados porque gpt-oss inventó un `rol` fuera del enum y omitió un `titulo` |
| Reglamento de copropiedad | visión | 60 | gemma4 + gpt-oss (Ollama) | 60 + 13 | 3.264 + 134 | 35 | 3 intentos: conexión cortada tras horas en cola; 2 páginas en bucle (261k tokens c/u) que tiraron 2 lotes; el 3.º con `--reusar-texto` (13 llamadas, 134 s) sin errores. La página 57 quedó con Tesseract (confianza 47). Ver §1.1 |
| 8 liquidaciones feb–sep 2026 | texto | 14–16 c/u | gpt-oss (Ollama) | 2 c/u | 27–42 | 10–11 c/u (rubros) | ver §2 |

### 1.1 Fallas y qué se cambió

- **Un lote malo tiraba el documento entero**: `estructurar_lotes` ahora anota el error del lote y
  sigue con el siguiente; solo el presupuesto excedido corta.
- **JSON con caracteres de control** (gpt-oss manda saltos de línea crudos dentro de las cadenas):
  se parsea con `json.loads(..., strict=False)` antes de validar con pydantic.
- **Un valor inventado en un campo enumerado tiraba el lote** (`rol: "comprador"`, `tipo: "pasaporte"`):
  los campos `rol`/`tipo` mapean lo desconocido a `"otro"` antes de validar. Un `titulo` faltante
  en una sección sigue descartando el lote: ahí no hay valor razonable que inventar.
- **Una página de visión que falla tiraba el documento**: `vision_pagina` reintenta 3 veces con
  espera creciente y, si sigue fallando, usa Tesseract para esa página dejando una marca en el
  texto. La confianza del clasificador y el método por página quedan registrados igual.
- **Bucle de repetición del modelo de visión**: en 2 de las 60 páginas del reglamento gemma4
  devolvió 261.563 tokens de "l l l l…" y "[ilegible]" repetidos (900 KB por página). Sin tope de
  salida ni precio (Ollama gratuito) pasó desapercibido hasta que 2 lotes del estructurado
  desbordaron el contexto de 131k. Ahora toda llamada lleva `max_tokens` (16.000; visión 4.000) y
  `texto_degenerado()` detecta el bucle (página de más de 15.000 caracteres o cola que repite ≤ 3
  palabras) y la manda directo a Tesseract: en la reingesta se vio que el bucle es determinista
  (la misma página devolvió exactamente 261.563 tokens en 3 reintentos, 20 minutos cada uno) y que
  **Ollama cloud ignora `max_tokens`**, así que reintentar solo gastaba una hora.
- **Reutilizar la transcripción** (`--reusar-texto`): las páginas OCR/visión ya guardadas en
  Postgres se reutilizan y solo se vuelven a transcribir las degeneradas. Es lo que permite iterar
  el skill de estructurado sobre el reglamento con 12 llamadas en vez de 72.

## 2. Liquidaciones de expensas

Decisión: "lo que es tabla regular se parsea en código, lo que es prosa va al modelo". El modelo
(sección `liquidacion` del skill, una llamada por liquidación) extrae período, vencimientos,
administración, rubros con total y porcentaje, y los gastos con proveedor, concepto, importe,
fecha y comprobante. La tabla "Estado de cuentas y prorrateo" (141 unidades por mes) la parsea
una expresión regular.

| Período (archivo) | Rubros | Gastos | Unidades parseadas | Sin parsear | Total gastos del período |
|---|---|---|---|---|---|
| 2026-01 (`2026-02-11`) | 11 | 37 | 141 | 0 | 19.544.979,52 |
| 2026-02 (`2026-03-12`) | 11 | 39 | 141 | 0 | 14.373.797,52 |
| 2026-03 (`2026-04-12`) | 10 | 32 | 141 | 0 | 22.137.414,41 |
| 2026-04 (`2026-05-08`) | 11 | 53 | 141 | 0 | 19.886.993,72 |
| 2026-05 (`2026-06-07`) | 11 | 42 | 141 | 0 | 18.060.808,34 |
| 2026-06 (`2026-07-07`) | 11 | 40 | 141 | 0 | 19.567.538,57 |
| 2026-07 (`2026-08-09`) | 10 | 39 | 141 | 0 | 20.280.022,36 |
| 2026-08 (`2026-09-06`) | 11 | 45 | 141 | 0 | 31.940.692,52 |

Lo que se aprendió del parser:
- La primera versión asumía tres pares "porcentaje % importe" (ordinarias, extraordinaria,
  previsión). **El formato cambió tres veces en ocho meses**: enero, marzo y abril no tienen
  columna de previsión; mayo agrega "gasto particular"; agosto agrega un concepto con otro
  porcentual (1,47 %). Con la regex original, 3 meses quedaron en 0 de 141 filas.
- La versión general acepta de 2 a 5 pares y de 0 a 2 importes sueltos entre el total y lo que
  hay que pagar. Asigna ordinarias/extraordinaria/previsión a los pares que tienen el porcentual
  de la unidad, y guarda el resto en `otros_conceptos` para no perderlos. Cobertura: 1.128 de
  1.128 filas. La métrica `filas_no_parseadas` queda en la tabla para detectar el próximo cambio
  de formato.
- Los nombres de propietarios del prorrateo son datos personales: la tabla se trata como
  sensible al generar la muestra sanitizada, y `preguntar` nunca la ve (solo `expensas`, por SQL).
- Lo que **no** se validó todavía: que los 37 a 53 gastos por mes que extrae el modelo sean todos
  los del PDF y con el importe exacto. Para eso sirve el listado de gastos de agosto 2026 de
  `datos/facturas/` como ground truth; queda pendiente.

Ejemplo de consulta por SQL (la unidad 5-D, porcentual 0,85 %):

```
consorcio expensas unidades --unidad 5-D
2026-01 ord 154.107,24  extra 34.388,00  prev —          total 188.495,24  a pagar 188.495,00
2026-02 ord 145.510,24  extra 34.388,00  prev 13.002,96  total 192.901,20  a pagar 192.901,00
...
2026-08 ord 180.880,88  extra 34.388,00  prev 36.107,40  total 254.419,78  a pagar 911.575,54
```

El "a pagar" de junio a agosto crece porque arrastra saldo: eso es lo que un vecino va a
preguntar, y la respuesta sale de la tabla, no del modelo.

## 3. Consultas con cita

Set: `datos/evaluacion/preguntas.csv`, 26 preguntas (13 dev / 13 test, alternadas), 2 de ellas "trampa"
que deben derivarse (deuda de otra unidad, DNI de la administradora). Comando:
`consorcio evaluar-consultas datos/evaluacion/preguntas.csv --particion dev|test --con-modelo`.
Resultados crudos: `2026-10-26-consultas-dev.json` y `-test.json` en esta carpeta.

### 3.1 Retrieval (sin modelo), k = 3

| Partición | Método | recall@3 | MRR | Fallas (id) |
|---|---|---|---|---|
| dev (12) | **tsvector** (sistema) | **1,000** | 0,861 | — |
| dev (12) | palabras clave (baseline) | 0,917 | 0,722 | 15 |
| test (12) | tsvector (sistema) | 0,583 | 0,403 | 4, 6, 8, 18, 24 |
| test (12) | **palabras clave** (baseline) | **0,917** | 0,750 | 4 |

Antes de desactivar el contrato Cabrera (otro edificio, ingestado para medir OCR) los números eran
0,917/0,917 en dev y 0,500/0,667 en test: aparecía primero en las preguntas de expensas y fondo de
reserva. Tres etiquetas se corrigieron mirando lo ingestado (no el ranking): el acta del 30/04/2026
transcribe el reglamento de cocheras y vale como fuente, el orden del día de la convocatoria quedó
partido en una sección por punto, y el resumen del Artículo Décimo dice "balcones", no "macetas".

**Lectura honesta**: la hipótesis "tsvector con stemming español supera a las palabras clave" se
sostiene en dev y **se invierte en test**. Las fallas de tsvector en test tienen una causa común: los
stems frecuentes ("gast", "expens", "asamble", "cocher") empatan muchas secciones y `ts_rank_cd`
premia las largas (la "Convalidación del reglamento de cocheras" del acta aparece en casi todas las
preguntas de cocheras y desplaza al artículo exacto). El baseline, que solo cuenta palabras
distintas presentes, no sufre eso. Por protocolo no se tocó el ranking mirando test: el sistema
sigue con tsvector (ganó en dev) y la próxima iteración, que se elige en dev, es pesar el título
más que el resumen o pasar a embeddings (pgvector). Las 12 preguntas por partición son pocas: una
pregunta mueve 8 puntos.

### 3.2 Generación (gpt-oss:120b por Ollama, una llamada por pregunta, k = 3, tsvector)

| Partición | Respondidas | Citas verificadas (de las respondidas) | Fuente correcta | Correctas | Trampa derivadas | Seg. prom. |
|---|---|---|---|---|---|---|
| dev (13) | 12 (0,92) | 12 (1,00) | 11 (0,85) | 12 (0,92) | 1/1 | 2,0 |
| test (13) | 9 (0,69) | 9 (1,00) | 7 (0,54) | 8 (0,62) | 1/1 | 2,1 |

"Correcta" = respondida con cita verificada desde la fuente esperada, o derivada cuando debía.
Lo que pasó en cada falla:
- **4 derivaciones por retrieval** (test 4, 8, 24; dev ninguna): el modelo dijo "falta la fuente" y
  derivó. Correcto como comportamiento, falla como sistema: la sección existía y el buscador no la
  trajo. Son las mismas fallas de §3.1.
- **Cita verídica, fuente "incorrecta" para la etiqueta** (dev 11, test 6): respondió bien ("Banco
  Galicia", "prohibido lavar") citando otra sección válida (el acta en vez del reglamento). La
  métrica es más dura que la realidad.
- **Respuesta equivocada con cita verídica** (test 18, "rubro de mayor gasto"): eligió
  administración (6 %) en vez de mantenimiento (42,7 %). Comparar importes no es tarea del RAG:
  esa pregunta va a `consorcio expensas` por SQL. Es el argumento concreto para el router.
- **Cero citas inventadas**: las 21 respuestas dadas tienen la cita textual en las páginas
  originales. El verificador es estricto (en una corrida anterior derivó una respuesta correcta
  porque el modelo escribió "da derecho" donde el original dice "dará derecho"); preferimos derivar
  de más a citar de menos.
- **Las 2 trampa se derivaron** en ambas particiones (el modelo no encontró respaldo y lo dijo).

### 3.3 Costo y latencia por consulta

Promedio de las 52 consultas de la evaluación (tabla `consultas`, origen `evaluacion`):

| | Tokens entrada | Tokens salida | Segundos | USD (Ollama) | USD estimado Haiku 5.5 | USD estimado Gemini 2.5 Flash |
|---|---|---|---|---|---|---|
| por consulta | 3.416 | 430 | 2,0 | 0 (sin precio) | 0,0006 | 0,0021 |

El máximo de entrada fue 6.528 tokens (tres secciones con hasta 6.000 caracteres de páginas cada
una). A ese costo, 1.000 consultas de vecinos cuestan menos de 1 USD con Haiku.

### 3.4 Decisiones que salen de esta evaluación

1. El buscador sigue con tsvector (decisión tomada en dev); la siguiente iteración se mide en dev
   antes de mirar test otra vez.
2. Las preguntas de expensas (importes, rubros, comparaciones) no van al RAG: router → tabla.
3. El contrato Cabrera queda desactivado; `activo=false` es también el mecanismo de vigencia.
4. Ampliar el set: 12 preguntas por partición es poco para separar hipótesis.
