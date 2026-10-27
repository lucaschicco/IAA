# Prueba de concepto: cómo funciona el agente

Estado al 26/10/2026. Este documento describe lo que el agente **hace hoy**, paso a paso, con los
números medidos. Lo que todavía no existe está al final, en "Qué no hace todavía". El diseño completo
previsto está en el README y en `docs/informe.md` §5; las decisiones y los tropiezos, en `docs/bitacora.md`.

## 1. Qué es

Un asistente para la administración de un consorcio al que se le habla por Telegram (o por línea de
comandos). Hace tres cosas:

1. **Carga documentos.** Recibe un PDF (reglamento, acta, convocatoria, liquidación de expensas), decide
   cómo leerlo, lo transcribe, extrae su estructura y lo guarda.
2. **Responde consultas citando.** Busca en lo cargado, responde con una cita textual del documento y
   comprueba por código que la cita exista. Si no puede, deriva a la administración.
3. **Consulta expensas.** Los importes de las liquidaciones se leen por SQL; el modelo nunca los genera.

Cada consorcio tiene su propia base, y cada chat de Telegram usa la suya: un chat nuevo arranca vacío
y responde solo con lo que se le sube.

## 2. Vista general

```mermaid
flowchart LR
    subgraph Canales
        TG["Bot de Telegram"]
        CLI["Línea de comandos"]
    end
    subgraph Agente["Agente · grafos de LangGraph"]
        ING["Ingesta de PDF"]
        CON["Consulta con cita"]
        EXP["Expensas por SQL"]
    end
    subgraph Modelo["Modelo · Ollama cloud"]
        GPT["gpt-oss:120b · texto"]
        GEM["gemma4:31b · visión"]
    end
    TG --> ING & CON & EXP
    CLI --> ING & CON & EXP
    ING --> GPT & GEM
    CON --> GPT
    ING --> PG[("Postgres · una base por consorcio")]
    ING --> NEO[("Neo4j · grafo sin datos sensibles")]
    CON --> PG
    EXP --> PG
    ING -.-> ML["MLflow · runs y trazas"]
    CON -.-> ML
```

| Pieza | Dónde está | Qué decide |
|---|---|---|
| Bot de Telegram | `src/consorcio/canales/telegram.py` | Qué herramienta usar según el tipo de mensaje y qué base usa el chat |
| Ingesta | `src/consorcio/ingesta/` | Cómo leer cada página y qué extraer |
| Consulta | `src/consorcio/consulta/` | Qué secciones buscar, si se responde o se deriva |
| Expensas | `consultar_expensas` en `src/consorcio/almacen/postgres.py` | Nada: es SQL directo |
| Instrucciones del modelo | `skills/ingesta/SKILL.md`, `skills/consulta/SKILL.md` | Los prompts, versionados, fuera del código |

## 3. Qué pasa cuando llega una petición

El bot no usa el modelo para decidir qué hacer: lo decide el **tipo de mensaje**. Es el lugar donde
irá el router con modelo cuando haya más flujos (reclamos, facturas, proveedores).

```mermaid
flowchart TD
    U["Mensaje en Telegram"] --> A{"¿El chat está autorizado?"}
    A -- no --> X["Responde el id del chat y no procesa nada"]
    A -- sí --> B["Busca la base del chat<br>si es nuevo, crea una vacía"]
    B --> C{"¿Qué tipo de mensaje es?"}
    C -- "PDF adjunto" --> I["Ingesta · sección 4"]
    C -- "texto o /preguntar" --> Q["Consulta con cita · sección 5"]
    C -- "/expensas" --> E["SQL sobre liquidaciones · sección 6"]
    C -- "/documentos" --> L["Lista los documentos de la base"]
    C -- "/start" --> S["Ayuda, base del chat y cantidad de documentos"]
    I & Q & E --> K{"¿Hay otro pedido en curso?"}
    K -- sí --> W["Avisa y espera su turno"]
    K -- no --> R["Ejecuta y contesta en el chat"]
    W --> R
```

- **Autorización.** `TELEGRAM_CHATS_PERMITIDOS` lista los chats que pueden usarlo; vacío significa
  cualquiera, solo para probar. La base tiene datos personales.
- **Una base por chat.** El primer mensaje de un número crea el consorcio `chat-<id>` (o `grupo-<id>`).
  Varios números pueden compartir un consorcio: `make asignar CHAT=<id> A=<consorcio>`.
- **De a un pedido.** El plan gratuito de Ollama acepta una llamada a la vez, así que el bot atiende en
  fila. La ingesta corre en un hilo aparte para que el bot siga recibiendo mensajes.

## 4. Ingesta de un PDF

```mermaid
flowchart TD
    A["PDF recibido"] --> B{"¿Cada página tiene capa de texto?<br>al menos 200 caracteres"}
    B -- "sí, todas" --> T["Texto directo · gratis"]
    B -- "hay escaneadas" --> C["Tesseract sobre 3 páginas de muestra"]
    C --> D{"¿Confianza media ≥ 75?"}
    D -- sí --> TS["Tesseract página por página · gratis"]
    D -- no --> V["Visión del modelo página por página<br>gemma4:31b"]
    TS --> E{"¿Página con confianza baja?"}
    E -- sí --> V
    E -- no --> M
    V --> F{"¿La transcripción entró en bucle?"}
    F -- sí --> TB["Tesseract como respaldo<br>queda marcado en el texto"]
    F -- no --> M
    TB --> M
    T --> M["Metadatos · 1 llamada<br>tipo, título, fecha, consorcio, resumen"]
    M --> G{"¿Es una liquidación de expensas?"}
    G -- sí --> L["Modelo: encabezado, rubros y gastos<br>Regex: prorrateo de cada unidad"]
    G -- no --> S["Estructurar en lotes de 5 páginas<br>personas, organizaciones, direcciones,<br>fechas, unidades, montos, secciones"]
    L --> P["Persistir"]
    S --> P
    P --> PG[("Postgres<br>todo, sensibles marcados")]
    P --> NEO[("Neo4j<br>sin DNI, CUIT, teléfonos ni emails")]
    P --> AR["datos/procesados<br>texto.md y extraccion.json"]
```

| Paso | Usa modelo | Qué produce | Por qué así |
|---|---|---|---|
| Clasificar | No | Método por página y su motivo | Solo se pagan tokens de visión donde Tesseract no alcanza |
| Extraer texto | Solo visión | Texto de cada página | La capa de texto y Tesseract son gratis |
| Freno a bucles | No | Tesseract si la visión devuelve basura repetida | Una página devolvió 261 mil tokens en bucle; Ollama ignora el tope de salida |
| Metadatos | 1 llamada | Tipo y resumen del documento | Decide si es liquidación |
| Estructurar | 1 llamada cada 5 páginas | Entidades y secciones con página de origen | Las secciones son lo que después busca la consulta |
| Liquidación | 1 llamada + regex | Rubros, gastos y unas 140 filas de prorrateo | El modelo no copia 140 filas de importes; la regex sí, y cuenta las que no matchean |
| Persistir | No | Postgres, Neo4j y archivos revisables | Los datos sensibles no salen de Postgres |

Una ingesta repetida del mismo archivo en la misma base reemplaza a la anterior (el documento se
identifica por consorcio y hash). `--reusar-texto` reaprovecha las páginas ya transcriptas para volver
a estructurar sin pagar la visión otra vez.

## 5. Consulta con cita

```mermaid
flowchart TD
    Q["Pregunta del vecino"] --> B["buscar · sin modelo<br>texto completo en español sobre título, tema y<br>resumen de cada sección · 3 mejores<br>solo documentos activos de la base del chat"]
    B --> H{"¿Encontró secciones?"}
    H -- no --> D["Derivar a la administración"]
    H -- sí --> C["Armar contexto<br>texto original de las páginas de cada sección"]
    C --> R["responder · 1 llamada<br>devuelve JSON: puede responder, respuesta,<br>número de fuente, página, cita textual"]
    R --> P{"¿El modelo dice que puede responder?"}
    P -- no --> D
    P -- sí --> V{"verificar · sin modelo<br>¿la cita aparece textual en las páginas<br>de esa fuente? mínimo 15 caracteres"}
    V -- no --> D
    V -- sí --> OK["Responder con la cita y la fuente"]
    OK --> G[("Tabla consultas y traza en MLflow")]
    D --> G
```

Tres caminos llevan a derivar, y los tres quedan registrados con su motivo:

1. La búsqueda no encontró ninguna sección relacionada.
2. El modelo leyó las fuentes y dijo que no alcanzan.
3. El modelo respondió, pero su cita no está en el texto original. Se compara contra las páginas, no
   contra el resumen que escribió el modelo al ingestar.

**Ejemplo real** (traza de MLflow del 26/10, base de Mercedes 274):

| Paso | Tiempo | Qué pasó |
|---|---|---|
| buscar | 0,18 s | Recuperó 3 secciones; la 3.ª era el Artículo 3 del reglamento de cocheras |
| responder | 2,08 s | 1 llamada a gpt-oss:120b, 3.425 tokens. Devolvió: puede responder, "como máximo dos vehículos", fuente 3, página 5, cita "Dentro de su espacio se permiten hasta 2 vehículos." |
| verificar | 1 ms | La cita está en la página: respondió con cita y fuente |

Pregunta: "¿Cuántos vehículos puedo tener en la cochera?". La misma pregunta en un chat sin documentos
se deriva por el camino 1.

## 6. Expensas

`/expensas` (o `consorcio expensas`) consulta las tablas que llenó la ingesta de liquidaciones:
resumen por período, rubros, gastos filtrados por texto, y lo que paga cada unidad. No hay modelo en
este camino. Ejemplos: `/expensas rubros 2026-07`, `/expensas 2026-07 5-D`, `/expensas gastos limpieza`.

Si un vecino pregunta por expensas en lenguaje natural, hoy va al camino de consulta con cita, que
sirve para "qué dice la liquidación" pero no para comparar importes (ver sección 9).

## 7. Modelo y proveedor

| | Hoy | Por qué |
|---|---|---|
| Proveedor | Ollama cloud, plan gratuito (`https://ollama.com/v1`) | La clave de Anthropic venció; AI Studio gratuito da 20 pedidos por día |
| Texto | `gpt-oss:120b` | Modelo de pesos abiertos publicado por OpenAI; no es GPT-4 ni GPT-5 |
| Visión | `gemma4:31b` | Solo para páginas escaneadas que Tesseract no lee bien |
| Cliente | `ChatOpenAI` de LangChain | Ollama expone la API compatible con OpenAI; por eso la traza muestra ese nombre |

El proveedor se cambia en `.env` (`LLM_PROVIDER`, `LLM_MODEL`) sin tocar código. Cada run y cada traza
de MLflow llevan las etiquetas `proveedor`, `modelo` y `endpoint`. Con una clave paga, el candidato es
Claude Haiku 5.5: fue el de mejor relación calidad/costo en `docs/evaluaciones/`.

## 8. Qué queda registrado

| Dónde | Qué | Cómo verlo |
|---|---|---|
| Tabla `consultas` | Cada pregunta: canal, base, respuesta, cita, si se verificó o derivó, motivo, tokens, segundos | `make consultas ORIGEN=telegram` |
| Tabla `llamadas_modelo` | Cada llamada de una ingesta: nodo, modelo, tokens, segundos, costo | `make psql` |
| MLflow, vista Model training | Un run por consulta o ingesta con métricas y costo | http://localhost:5000, experimento consorcio-ingesta |
| MLflow, vista GenAI | Una traza por petición con los pasos del grafo y el prompt y la respuesta del modelo | Pestaña Traces del mismo experimento |
| Log del bot | PDFs recibidos, preguntas, resultado | `make bot-logs` |
| `datos/procesados/` | Transcripción y extracción de cada documento | Archivos `texto.md` y `extraccion.json` |

## 9. Resultados medidos

Detalle en `docs/evaluaciones/2026-10-26-consultas-y-expensas.md`. Set de 26 preguntas sobre Mercedes 274,
partido en desarrollo y prueba, con 2 preguntas trampa que deben derivarse.

| | Desarrollo | Prueba |
|---|---|---|
| Búsqueda: la sección correcta entre las 3 primeras, sistema | 1,000 | 0,583 |
| Búsqueda: lo mismo con palabras clave, línea de base | 0,917 | 0,917 |
| Respuestas correctas | 12 de 13 | 8 de 13 |
| Citas inventadas | 0 | 0 |
| Preguntas trampa derivadas | 1 de 1 | 1 de 1 |

| Costo y tiempo | Valor |
|---|---|
| Tokens por consulta, promedio | 3.416 de entrada y 430 de salida |
| Segundos por consulta | 2 |
| USD por consulta con Haiku 5.5, estimado | 0,0006 |
| Ingesta con capa de texto, 2 páginas | menos de 1 minuto |
| Ingesta escaneo legible, 42 páginas, por Tesseract | 3 minutos, 10 llamadas |
| Ingesta escaneo difícil, 60 páginas, por visión con Ollama gratuito | casi 1 hora, 73 llamadas |

Lectura: el agente no inventa citas y deriva cuando debe. Sus fallas son de búsqueda: en la partición
de prueba la línea de base por palabras clave encontró más que el sistema, y eso queda como hipótesis
abierta para la próxima iteración, que se decide mirando solo la partición de desarrollo.

## 10. Qué no hace todavía

- **Router con modelo.** El camino lo elige el tipo de mensaje. Una pregunta sobre expensas en lenguaje
  natural va al buscador y no a SQL; "¿cuál fue el rubro de mayor gasto?" se respondió mal con una cita
  verídica.
- **Revisión humana.** No hay bandeja de aprobación; los documentos quedan en estado `extraido` sin
  revisar.
- **Facturas, reclamos, proveedores, calendario y email.** No existen.
- **WhatsApp.** Pide número de negocio verificado y un servidor público; se dejó para después.
- **Vigencia.** Un acta que modifica el reglamento no anula el artículo viejo: los dos se pueden recuperar.
- **Neo4j no se usa para responder** y comparte personas y direcciones entre consorcios por nombre.
- **Cuota gratuita.** Una llamada a la vez; un escaneo grande por visión tarda horas.
- **Muestra sanitizada.** Los datos son reales; falta la versión anonimizada que pide la consigna.

## 11. Cómo probarlo

Con la base ya cargada, desde el repo: `make restaurar`, `make test`, `make preguntar Q="..."`. Por
Telegram: `make bot`, escribirle a `@con_adm_bot`, seguir el guion del README ("Probarlo por Telegram":
preguntar, ver que deriva, subir el reglamento de cocheras, volver a preguntar). Los grafos se pueden
redibujar desde el código con `consorcio dibujar-grafo ingesta` y `consorcio dibujar-grafo consulta`.
