# Prueba de punta a punta con Manzanares (24/11/2026)

Consorcio `manzanares`, en la base `consorcio` (hoy de pruebas). Hay dos usuarios, uno de administración y una
inquilina, y se usan desde la web local (puerto 8001) con un script que hace lo mismo que un navegador.
Los PDFs, el set de preguntas y los resultados están en `datos/entrada/doc-manzanares/`. Esa carpeta está ignorada
por git porque tiene documentos reales.

## 1. Qué se cambió antes de probar

| Cambio | Dónde | Test |
|---|---|---|
| Consulta en dos pasos: `extraer` (hasta 3 citas textuales) → `verificar` (descarta las que no están en el texto) → `redactar` (ve solo las citas) | `consulta/grafo.py`, `consulta/esquemas.py`, `skills/consulta/SKILL.md` 0.3.0 | `test_agente.py`, `test_consulta.py` |
| El vecino ve la respuesta corta y, debajo, cada cita con su archivo y página | `canales/formato.py` | `test_agente.py` |
| Montos que no son de la liquidación (presupuesto, obra, cuota extraordinaria) → documentos | `skills/agente/SKILL.md` 0.1.2, `agente/nodos.py` | `test_agente.py` (expensas sin liquidación busca en documentos) |
| Carga de hasta 10 PDFs juntos; si uno es inválido no se guarda ninguno | `web/rutas.py` | `test_web.py` |
| Carga en segundo plano con cola de a uno y lista que se refresca sola | `web/cargas.py`, `_carga.html` | `test_web.py` (carga lenta + polling) |
| Consultas de la administración, en una pantalla propia (`/consultar`, k=5, sin solicitud) | `web/rutas.py`, `consultar.html` | `test_web.py` |
| Un documento que quedó sin secciones porque falló el modelo se marca `incompleto`, y subirlo de nuevo lo reprocesa | `ingesta/grafo.py`, `almacen/postgres.py` | `test_duplicados.py` |

Suite completa: 902 passed, 36 skipped antes del último cambio (`incompleto`). Después de ese cambio pasaron
`test_duplicados.py` y `test_crudos.py` con S3 (67 passed, 0 skipped) y `test_ingesta`, `test_web` y `test_aislamiento`
(186 passed). La suite completa con S3 quedó pendiente de correr de nuevo.

## 2. Set de pruebas (QA)

Un subagente con rol de QA de IA armó `evaluacion/preguntas.csv` con 47 preguntas (35 de la inquilina y 12 de la
administración) y `evaluacion/criterios.md`, leyendo los 8 PDFs reales. Cada pregunta lleva su categoría, la
respuesta esperada, el documento y la página fuente, y el comportamiento esperado:

| Comportamiento esperado | Preguntas |
|---|---|
| Responder con cita | 29 |
| Expensas (SQL) | 6 |
| Derivar (no está en los documentos) | 5 |
| Reclamo | 3 |
| Saludo | 2 |
| Fuera de alcance | 2 |

Los documentos reales no cubrían todo, así que se generaron tres PDFs sintéticos con el prefijo `sintetic-`:

- un reglamento de copropiedad;
- una liquidación de expensas de 2025-10, con 28 unidades y en el formato que lee el parser;
- una circular de corte de agua y fumigación.

Observaciones del QA:

- las actas están manuscritas;
- el encabezado del acta del 29/08/2023 dice "29 de Marzo";
- no hay actas posteriores a enero de 2024.

## 3. Carga masiva

| | Ronda 1 | Ronda 2 |
|---|---|---|
| 11 PDFs en un envío | 422, como se esperaba (el máximo es 10) | 422 |
| Lote de 10 / lote de 1 | 200 en 3,4 s / 3,0 s | 200 en 3,2 s / 3,0 s |
| Hasta terminar la cola | 993 s | 445 s |
| Resultado | 7 procesados, 2 duplicados, 4 con error | 4 reprocesados, 7 duplicados |

La web respondió en unos 3 s aunque la ingesta tardara minutos, y la lista se actualizó sola. Los 4 errores de la
ronda 1 vinieron de la cuota del modelo, no de la carga (ver §4).

Tres documentos quedaron guardados con 0 secciones:

- el reglamento sintético;
- el acta y la convocatoria del 12/12/2023.

Les falló `estructurar` por la cuota, y la web los informaba como "Listo", así que volver a subirlos daba
"duplicado". Por eso se agregó el estado `incompleto`. Los tres ya están marcados así y se van a reprocesar en la
próxima subida.

## 4. Preguntas

### Rondas 1 y 2 (24/11): sin cuota

Las dos rondas corrieron con Gemini gratuito y la cuota diaria se agotó en las dos:

- **Ronda 1, con `gemini-2.5-flash`.** La cuota de 20 pedidos por día se terminó durante la carga.
- **Ronda 2, con `gemini-3.5-flash`.** Se cambió el modelo solo para el proceso web y tiene la misma cuota.

Los 35 mensajes de la inquilina derivaron por `GoogleRateLimitError` y las 12 consultas de la administración dieron
500. Esas rondas no sirven para medir. Las 70 derivaciones que dejaron se borraron el 25/11 (hay respaldo en
`datos/respaldo/postgres-2026-11-25-antes-borrar-derivaciones.sql`).

### Rondas 3 a 5 (25/11): Claude Haiku 5.5

Se usó una clave de Anthropic pasada solo al proceso web, sin tocar `.env`. Las rondas sirvieron para lo siguiente:

- **Ronda 3:** encontró tres fallas, que se corrigieron antes de seguir (ver §5).
- **Ronda 4:** las verificó, y en esa ronda el acta del 12/12/2023 se volvió a subir y quedó completa.
- **Ronda 5:** se corrió con los 11 documentos completos.

Un subagente QA (Sonnet) calificó las rondas 3 y 5 contra `preguntas.csv` y `criterios.md`. El detalle está en
`evaluacion/calificacion-ronda{3,5}.json`.

| Calificación | Ronda 3 | Ronda 5 |
|---|---|---|
| Correcta | 21 | **31** (25 de la inquilina, 6 de la administración) |
| Parcial | 4 | 3 |
| Derivó cuando debía responder | 21 (4 por errores técnicos) | 12 |
| Incorrecta / afirma algo falso | 0 | 0 |
| Dato de prueba inconsistente | 1 | 1 |

El dato inconsistente es el mismo en las dos rondas: la inquilina está cargada en el 7-F, que no existe en la
liquidación sintética, y el sistema no deja reclamar otra unidad desde el texto del mensaje.

Ninguna respuesta afirmó algo falso. Lo más cercano es una pregunta de la administración sobre la portería. La cita
copia la fecha mal leída del acta manuscrita ("29/03/2023") y la respuesta no da el monto adeudado, así que se
calificó parcial.

**Costo y latencia, ronda 5:** unos 2 s y 0,001 USD por consulta. En las tres rondas fueron 97 consultas por unos
0,10 USD.

### Dónde sigue derivando (ronda 5, 12 casos)

- **Actas de asamblea (8 de 12):** resúmenes, temas tratados y hora de inicio. Las actas son escaneos manuscritos
  y la búsqueda o las citas no alcanzan. Para preguntar "de qué trató el acta" conviene recuperar el documento
  entero y no secciones sueltas.
- **Vocabulario del reglamento:** "perros" no encuentra "animales", y "no puedo ir" no encuentra "carta poder".
  Va en la misma línea que la tarea pendiente de embeddings o sinónimos.
- **Mismo dato, otra forma de preguntar:** "presupuesto de la obra" deriva y "cuánto salió" responde. La
  liquidación de una unidad pedida desde `/consultar` también deriva, porque esa pantalla no pasa por SQL de
  expensas.

## 5. Hallazgos

Arreglados el 25/11, cada uno con su test:

- **Haiku omitía `puede_responder` cuando sí traía citas.** Pasó en 11 consultas de la ronda 3, que dieron
  `ValueError`, o 500 en `/consultar`. Ahora el campo tiene default `true`, y sin citas igual se deriva.
  Test: `test_consulta.py`.
- **El control de palabras rechazaba respuestas correctas.** Contaba como "ajenas" las palabras que retomaban la
  pregunta y frases como "las citas no muestran…". Ahora acepta las palabras de la pregunta (no sus números) y el
  vocabulario con el que `redactar` habla de sus citas. La cuota sigue en 25 %. Test: `test_consulta.py`.
- **Haiku mandaba una lista como texto JSON** (`"personas": "[{...}]"`), y por eso el acta del 12/12 quedó sin
  secciones. `llm._reparar_listas_como_texto` decodifica ese texto, solo en campos que no son de texto, y valida
  de nuevo. Test: `test_llm.py`.
- **Saludos y "gracias" recibían un rechazo** ("Eso queda fuera de lo que puedo resolver"). Ahora reciben un
  mensaje de bienvenida que dice en qué puede ayudar (skill del agente 0.1.3).

Del 24/11:


1. **La cuota gratuita no alcanza para una evaluación.** Son 20 pedidos por día y por modelo. Una consulta usa 3
   llamadas (router, extraer y redactar), así que 47 preguntas necesitan unas 140, y una carga de 11 PDFs gasta
   más de 20. Hace falta una clave paga, o Ollama cloud como en la ingesta del 26/10.
2. **Un 429 de cuota diaria se reintenta unos 37 s antes de fallar.** Un límite diario no se resuelve
   reintentando: conviene cortar enseguida.
3. **Un error del proveedor crea una solicitud de derivación.** Las rondas 1 y 2 dejaron 70 en la Bandeja de Manzanares.
   Se borraron el 25/11. Sigue pendiente que un error del proveedor no cree una solicitud.
4. **La carga decía "Listo" para un documento sin secciones.** Corregido: ahora la web dice que quedó guardado
   sin secciones, sin mostrar el detalle del proveedor, y que hay que volver a subirlo.
5. **La cola de cargas vive en el proceso web** (`shortcut:` en `web/cargas.py`). Un reinicio pierde lo pendiente.
   Sirve para una sola instancia.
6. **La cookie de sesión es `Secure`.** Un cliente HTTP en `http://127.0.0.1` no la manda, aunque un navegador sí.
   El script e2e usa una política de cookies propia.

## 6. Pendientes

1. **Actas escaneadas:** cómo recuperarlas para preguntas de resumen (documento completo en vez de secciones).
2. **Búsqueda:** sinónimos o embeddings. Sumar como regresión las preguntas de perros y de carta poder.
3. **Errores del proveedor:** un 429 de cuota diaria tiene que cortar enseguida y no crear una derivación.
4. **Datos de prueba:** las rondas 3 a 5 dejaron en Manzanares 38 derivaciones y 9 reclamos. Hay que decidir si se
   borran.
5. **Script e2e:** está fuera del repo, en el scratchpad de la sesión. Conviene moverlo a `scripts/` para repetir
   la prueba:

   ```
   E2E_CLAVE=... python -u e2e_manzanares.py rondaN [--sin-carga]
   ```
6. **Tests:** pasa la suite completa, con Postgres de test y S3. Son 943 tests y ninguno salteado (25/11).
