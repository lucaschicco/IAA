# Observabilidad: qué guarda MLflow y quién lo ve

Punto 5 del status del equipo: "qué se guarda y cómo se accede (p. ej. traza completa de la conversación
del usuario)". Este documento describe lo que hay al 19/11/2026, verificado en el código y en la
documentación de MLflow, y deja planteada una decisión de privacidad que tiene que tomar el grupo.

## Qué se guarda

MLflow es opcional: sin `MLFLOW_TRACKING_URI`, o con el servidor caído, el sistema anda igual y no
guarda nada (`src/consorcio/observabilidad.py`). Con MLflow prendido, cada corrida deja dos cosas en el
experimento `consorcio-ingesta`:

- **Un run** (pestaña "Model training"): etiquetas y números, sin texto libre.
- **Una traza** (pestaña "Traces", hecha por `mlflow.langchain.autolog()`): un árbol de spans, uno por
  nodo de LangGraph y uno por llamada al modelo. Cada span guarda **sus entradas y sus salidas
  completas**.

| Corrida | Run: etiquetas y métricas | Traza: texto que queda guardado |
|---|---|---|
| Turno del chat (`agente`) | canal, origen, consorcio, rol, proveedor, modelo, versión del skill. Las métricas del turno no van acá: están en la tabla `turnos` de Postgres | El mensaje del vecino; la identidad (número de usuario, consorcio, rol, unidades); los últimos 6 mensajes de la conversación (memoria del router); el prompt y la salida del router (en un reclamo, título y detalle); la respuesta |
| Consulta de reglamento (`consulta`, anidada en el turno) | método de búsqueda, k, origen, consorcio; secciones encontradas, si se verificó la cita o se derivó, tokens, costo y segundos | La pregunta; **el texto de las páginas recuperadas de los documentos**; la respuesta y la cita |
| Ingesta de un PDF (`ingesta`) | método (texto, Tesseract o visión), tipo de documento, páginas, tokens, costo, segundos, cuántas entidades se extrajeron | **El texto de cada página** y lo que extrajo el modelo. Además el run sube como archivos `texto.md` y `extraccion.json`, con las personas, DNI, CUIT y teléfonos que tenga el documento |

Desde el 18/11, la traza de cada turno guarda también de qué conversación es (la "sesión" de MLflow) y
el número interno del usuario, nunca su nombre ni su mail. Con eso MLflow junta todos los mensajes de una
charla.

**Dónde queda:** en la carpeta `mlflow/` de la máquina que corre `docker compose` (`mlflow.db` y
`mlartifacts/`). Está en `.gitignore`, así que nunca se sube al repo, pero cada integrante que ingestó
documentos reales con MLflow prendido tiene una copia de esos textos en su máquina.

## Cómo se accede

1. Abrir `http://localhost:5000`. En un Codespace, desde la pestaña **Puertos**, puerto 5000. Hace falta
   un `docker-compose.override.yml` local que sume `*.app.github.dev` a `MLFLOW_SERVER_ALLOWED_HOSTS` y
   `https://*.app.github.dev` a los orígenes CORS.
2. **Experiments → consorcio-ingesta → Traces**. La columna **Session** muestra la conversación de cada
   traza. Filtrar o agrupar por sesión muestra la conversación completa, un mensaje por traza.
3. Desde un turno: `turnos.mlflow_traza_id` es el **Trace ID** de su traza. Verificado el 18/11 con dos
   mensajes de la web: cada fila de `turnos` coincide con la traza de ese mensaje.
4. Desde Python, dentro del contenedor (`docker compose exec -T app python -`):

   ```python
   import mlflow
   from consorcio.config import config
   exp = mlflow.get_experiment_by_name(config.mlflow_experiment)
   trazas = mlflow.search_traces(locations=[exp.experiment_id], include_spans=False, return_type="list")
   for t in sorted(trazas, key=lambda t: t.info.request_time, reverse=True)[:3]:
       md = t.info.trace_metadata
       print(t.info.trace_id, md.get("mlflow.trace.session"), md.get("mlflow.trace.user"))
   ```

## El problema: quién lo puede ver

- **Sin login.** El servidor MLflow de `docker-compose.yml` no pide usuario ni contraseña. Hasta el
  20/11 el puerto estaba publicado en todas las interfaces (`"5000:5000"`), así que en una notebook
  conectada a una red compartida cualquiera de esa red podía abrirlo. Ahora se publica solo en
  `127.0.0.1`, como SeaweedFS, que también corre sin autenticación, y solo se abre desde la propia
  máquina (o desde la pestaña Puertos del Codespace, que es privada).
- **Cruza consorcios.** MLflow no sabe de consorcios: quien entra ve las trazas de todos.
- **Contradice el CRM.** El CRM cruza consorcios y por eso muestra solo agregados: `turnos` no guarda
  texto y `crm.py` no importa `Mensaje`. MLflow guarda justamente ese texto, y más: las páginas de los
  documentos, que en la base real tienen datos personales de Mercedes 274.

Con los datos de `sembrar-demo` no hay nada sensible. El problema aparece con datos reales.

## Opciones

Verificado en la documentación de MLflow:

- **Tapar datos:** se registra una función con `mlflow.tracing.configure(span_processors=[...])`. MLflow
  la aplica a cada span, también a los del autolog de LangChain, y puede reescribir sus entradas y
  salidas (`span.set_inputs`, `span.set_outputs`). Corre del lado de la aplicación, antes de mandar el
  span al servidor, así que el dato tapado nunca llega a MLflow
  ([Redacting Sensitive Data from Traces](https://mlflow.org/docs/latest/genai/tracing/observe-with-traces/masking/)).
- **Login:** `mlflow server --app-name basic-auth` pide usuario y contraseña. La contraseña del
  administrador se fija con `MLFLOW_AUTH_ADMIN_PASSWORD`, porque no hay una por defecto, y el servidor
  necesita `MLFLOW_FLASK_SERVER_SECRET_KEY`. El permiso por defecto de cada usuario es `READ` sobre todo,
  y se cambia en el archivo de configuración. La aplicación se identifica con
  `MLFLOW_TRACKING_USERNAME` y `MLFLOW_TRACKING_PASSWORD`
  ([Authentication with Username and Password](https://mlflow.org/docs/latest/self-hosting/security/basic-http-auth/)).

| | Qué queda en la traza | A favor | En contra |
|---|---|---|---|
| **A. Todo, con acceso cerrado** | Todo, como hoy. MLflow solo en `127.0.0.1` y, si alguna vez se publica, con `basic-auth` | Depurar es fácil: se ve el prompt exacto de cada paso | Las conversaciones y los documentos quedan en un segundo lugar, sin separación por consorcio. Borrar los datos de un vecino obliga a buscar sus trazas a mano |
| **B. Texto con datos tapados** (recomendada) | El mensaje y la respuesta, con DNI, CUIT, teléfonos y mails reemplazados, y sin la identidad completa. Más el acceso cerrado de A | Se puede seguir una conversación y ver por qué el router eligió un camino, sin datos personales | Una regla automática no detecta todo, por ejemplo los nombres en un acta. Hay que probar que también tapa las salidas y el resumen de la traza |
| **C. Sin texto** | Solo la estructura: pasos, tiempos, tokens, etiquetas y sesión. El texto se lee en la web, que ya respeta el login y el consorcio | Sigue la misma regla que el CRM. No hay riesgo de datos personales | No se puede depurar un prompt desde MLflow: hay que cruzar con Postgres |

En las tres, MLflow se publica solo en `127.0.0.1:5000`, como SeaweedFS. Ese cambio no dependía de la
decisión y ya está hecho (20/11).

## Para decidir en el grupo

1. ¿A, B o C para las trazas del chat?
2. ¿Quién entra a MLflow? ¿Solo el equipo de plataforma, como en el CRM, o también la administración?
3. Ingesta: ¿se siguen subiendo `texto.md` y `extraccion.json` a MLflow? Ya están en
   `datos/procesados/`, y en MLflow son una copia más de los datos personales.
4. ¿Se borran las trazas viejas con datos reales de Mercedes 274 que haya en la carpeta `mlflow/` de
   cada uno?

## Pendiente

- Implementar la opción elegida, con un test contra MLflow real como los de `tests/test_observabilidad.py`.
  Hay que comprobar en la versión que usamos si `span_processors` también tapa las salidas y el resumen
  de la traza (la columna Input de la lista), porque la documentación solo muestra entradas y salidas de
  cada span.
- `docker-compose.yml` usa `ghcr.io/mlflow/mlflow:latest`, que hoy es la 3.18.0. Los dos arreglos de las
  trazas (17/11 y 18/11) dependen de cómo se comporta esa versión, así que conviene fijarla.
- Usar MLflow como fuente de costo y latencia por tarea en el informe.
