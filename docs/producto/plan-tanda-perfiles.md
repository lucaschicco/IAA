# Plan de la tanda "perfiles": secciones sanas, métricas, elevar y cambiar de consorcio

Estado al 20/11/2026, sobre `feat/diseno-web` (5d01e6f). Documento de producto: qué se construye, cómo se acepta y qué
queda afuera. Perfiles: **vecino** (propietario, inquilino, consejo), **administración** (uno o varios consorcios) y
**plataforma** (`usuarios.plataforma`, solo CRM). Decidido antes: tabla `administraciones` + `consorcios.administracion_id`
(0010); un consorcio creado desde la web hereda la Administración de quien lo crea; el PDF de vecino con aprobación
queda para después.

## 0. Administración como entidad (base de B y D)

- `administraciones(id, nombre, creado_en)`; `consorcios.administracion_id` **nullable**, con FK. Nullable a propósito:
  Telegram crea `chat-<id>` (`consorcio_de_chat`) y está congelado, y `auth/alta.py` crea consorcios por terminal.
  En el CRM esos van a una fila "Sin administración", nunca se pierden del total.
- Sin backfill automático (no hay forma segura de saber qué consorcios son de la misma empresa). La base real se
  asigna a mano con un subcomando nuevo `consorcio administraciones --crear N` / `--asignar CONSORCIO --a ID`; la
  semilla demo crea "Administración Demo" con `demo-palmeras` y `demo-robles`.
- Herencia en `crear_consorcio` (`identidad.py`): si todos los consorcios que la persona administra tienen la misma
  Administración, se usa esa; si tiene más de una, el formulario de `/consorcios/nuevo` muestra un `<select>` con solo
  esas (validado en el servidor); si ninguna tiene, queda NULL. Nunca se puede elegir una Administración ajena.
- La Administración **no** da permisos: el alcance sigue saliendo de `membresias` por consorcio (`Identidad`). Es solo
  una dimensión de métricas y de agrupación visual.

## A. Ninguna sección rota, por ningún canal

**Definición operativa.** Una sección está rota si, para algún usuario demo y algún consorcio suyo, pasa cualquiera de:

1. Un GET a una ruta propia responde 500 (hoy no hay handler para excepciones no HTTP: sale el texto plano de
   Starlette, sin menú ni salida).
2. Un link propio (`href`, `hx-get`, `action`, `destino`) da 404, o da 403 **y estaba visible** para ese usuario. Ej. que
   D introduce: cambiar de consorcio estando en `/bandeja` hacia uno donde se es propietario.
3. Un formulario o botón que no hace nada: `<button type=submit>` fuera de un form, form sin `action` ni `hx-*`, form
   cuyo método+ruta no existe en `app.routes`, o POST sin campo `csrf`/`X-CSRF` (daría 403 siempre).
4. Pantalla sin salida: página con sesión sin menú lateral ni link a otra pantalla. Incluye `error.html` con sesión:
   hoy `manejar_error` no pasa `sesion` y la página de error sale sin menú; tiene que ofrecer al menos "Elegir
   consorcio" (403 por membresía revocada con la cookie vieja) y "Volver".
5. CLI: un subcomando cuyo `--help` falla, o que importa (import perezoso dentro de `main`) un nombre que no existe;
   hoy eso solo falla al correr ese comando.
6. Bot: un handler registrado en `correr_bot` que referencia una función o import inexistente, o que se rompe por el
   esquema nuevo (ej. `consorcio_de_chat` sin `administracion_id`). El bot no cambia de comportamiento.
7. API de login (`auth/app.py`, `make api`): una ruta del OpenAPI que da 500 con un token válido del rol correcto.

**Verificación automática** (`tests/test_recorrido.py`, corre en `make test` con `sembrar-demo` + `FAKE_GUIONADO`):

- *Crawler web por usuario demo* (`demo-prop-palmeras`, `demo-prop-robles`, `demo-multi`, `demo-admin-palmeras`,
  `demo-admin-dos`, `demo-plataforma`): entra por el selector, y por **cada membresía** hace `POST /consorcios` y un BFS
  de GET sobre los links del HTML (mismo origen, sin `/salir`, tope de 200 URLs). Cada respuesta: 200 o 303 a una ruta
  propia. Cada página con sesión: tiene `nav.menu-principal`. Cada form: método+ruta en `app.routes` y lleva CSRF.
  Antes del crawl manda un mensaje de chat y crea una solicitud, para que haya `?solicitud=` y `?turno=` que seguir.
- *Cobertura de rutas*: todo GET sin parámetro de path de `app.routes` lo visita al menos un usuario, o está en una
  lista explícita de excepciones (`/salud`, `/registro`). Una ruta nueva sin link rompe el test.
- *Negativos*: lo que un usuario no ve linkeado tiene que responder lo esperado si lo escribe a mano: `/crm` → 404 para
  todos menos plataforma; `/bandeja`, `/revisar`, `POST /documentos` → 403 para vecinos; `/consorcios/nuevo` → 403
  para quien no administra. Y nunca 500.
- *500 forzado*: una ruta de prueba que lanza `RuntimeError` devuelve la página de error con menú (handler genérico).
- *CLI*: `consorcio --help` y `consorcio <sub> --help` para cada subparser salen 0; un test recorre el AST de
  `cli.py` y `canales/telegram.py` y verifica que cada `from .x import y` perezoso resuelve.
- *Bot*: además de lo anterior, `test_telegram.py` suma un caso de `consorcio_de_chat` con la 0010 aplicada. Nada más.
- *API de login*: `TestClient(auth.app)` recorre su OpenAPI con un token de administración y uno de propietario;
  espera 2xx/403, nunca 500.

## B. Métricas de uso y consumo

Reglas comunes (ya vigentes en `crm.py`): se excluye `turnos.origen='evaluacion'` y las solicitudes creadas por esos
turnos; período `desde <= creado_en < hasta` (7/30/90 días) con `periodo_anterior` del mismo largo; días UTC; `None` y no
0 cuando no hay casos; nunca se lee texto (`mensajes`, `solicitudes.mensaje/titulo/detalle/respuesta`,
`revisiones.respuesta_correcta`, nombres). **Dimensiones**: Administración (join `consorcios.administracion_id`, NULL =
"Sin administración") → consorcio → canal (`turnos.canal`; fuera del agente, `consultas.origen`: cli/telegram/web) →
rol (`turnos.rol`, el rol al momento del turno). En el CRM se suma el filtro `?administracion=` y el de consorcio pasa a
listar solo los de esa Administración.

Estado: **E** existe en `crm.panel`; **F** falta; **P** existe en `agente/panel.py`.

| # | Métrica | Fórmula | Dimensiones | Pantalla | Estado |
|---|---|---|---|---|---|
| U1 | Preguntas | `count(turnos)` del período | adm, consorcio, rol, resultado | CRM Resumen; panel (7 días) | E (`preguntas`, `uso.por_rol`, `uso.por_consorcio`); P; F por adm |
| U2 | Activos 5 min / 1 / 7 / 30 días | `count(usuarios)` con `ultima_actividad >= now()-v` | global; con filtro, miembros del consorcio | CRM Resumen | E (`usuarios`). No es por consorcio ni canal: solo la web lo escribe |
| U3 | Usuarios que preguntaron | `count(distinct turnos.usuario_id)` | adm, consorcio, rol | CRM Uso; panel ("vecinos que usaron") | F |
| U4 | Preguntas por usuario | U1 / U3 | adm, consorcio | CRM Uso | F |
| U5 | Por funcionalidad / intención | `count(turnos) group by funcionalidad` / `intencion` (NULL=`sin_clasificar`) | adm, consorcio | CRM Resumen / Calidad | E |
| U6 | Tasa de derivación | `count(resultado='derivada')` / U1 | adm, consorcio, día | CRM Resumen | E por día (`actividad`); F como KPI |
| U7 | Actividad diaria | U1 y derivadas por día UTC, días vacíos en 0 | adm, consorcio | CRM Resumen | E |
| U8 | Uso por usuario | top 20 `count(turnos) group by usuario_id` (solo id) | consorcio | CRM Uso | E |
| U9 | Elevaciones | `count(solicitudes tipo='derivacion')` por `motivo`, `estado`; mediana `resuelta_en-creado_en` | adm, consorcio, motivo | CRM Calidad; panel | E (`elevaciones`); F por adm y los motivos nuevos de C |
| U10 | Pendientes hoy y antigüedad | `count(estado='pendiente')` **sin** filtro de período; `now()-min(creado_en)` | consorcio | panel; CRM Uso | P (pendientes); F antigüedad en CRM (hoy `pendientes` es solo del período) |
| U11 | Reclamos y efecto de la revisión | por `estado`; tasa de edición = editadas / (aprobadas+editadas+rechazadas) | adm, consorcio | CRM Uso | E conteos; F tasa |
| U12 | Elevadas después de una respuesta | `count(motivo='elevada_usuario')` / `count(turnos resultado='respondida')` | adm, consorcio, funcionalidad | CRM Calidad | F (nace con C) |
| U13 | Documentos | activos/inactivos, por tipo, por estado (estado de hoy) | consorcio | CRM Uso | E |
| U14 | Cargas | `count(cargas) group by estado` del período; duplicados evitados = estado en (`duplicado`,`periodo_duplicado`) | adm, consorcio | CRM Uso | E duplicados; F por estado |
| C1 | Costo por funcionalidad | `sum(turnos.costo_usd)`, `count` | adm, consorcio | CRM Uso | E |
| C2 | Ingesta y otros | `sum(gastos_modelo.costo_usd) − sum(turnos.costo_usd)` (todos los orígenes), ≥ 0 | adm, consorcio | CRM Uso | E |
| C3 | Tope mensual | `estado_tope()`, mes UTC, global; `modelos_sin_precio` | global | CRM Resumen y Uso | E |
| C4 | Costo por Administración | `sum(gastos_modelo.costo_usd)` join `consorcios` group by `administracion_id`; `consorcio_id` NULL → "sin consorcio" | adm | CRM Resumen (tabla "por Administración") | F (0010) |
| C5 | Costo por consorcio | ídem group by `consorcio_id` | consorcio | CRM Uso | F (hoy solo filtra) |
| C6 | Costo por día | `sum(gastos_modelo.costo_usd)` por día UTC | adm, consorcio | CRM Uso | F (tarjeta del mockup) |
| C7 | Costo por pregunta | `sum(turnos.costo_usd) / count(turnos con costo_usd not null)` | adm, funcionalidad | CRM Uso | F |
| C8 | Tokens | `sum(turnos.tokens_entrada)`, `sum(tokens_salida)` | funcionalidad, modelo | CRM Uso | F |
| C9 | Latencia | p50 y p95 de `turnos.segundos` (`percentile_cont`) | funcionalidad | CRM Calidad | F |
| C10 | Costo por canal | web = `sum(turnos.costo_usd)`; cli/telegram = `sum(consultas.costo_usd) group by origen` (sin `evaluacion`) | canal | CRM Uso | F. No agrega columna a `gastos_modelo` |
| C11 | Costo de ingesta por documento | `documentos.costo` (JSON) y `segundos_total`, promedio por `metodo_extraccion` | consorcio | CRM Uso | F |
| Q1–Q4 | Cita verificada, feedback, revisión y calibración, exactitud de evaluación | las de `_cita_verificada`, `_feedback_positivo`, `_calidad`, `_exactitud_evaluacion` | adm, consorcio | CRM Calidad | E (sin cambios) |

Panel de la administración (solo sus consorcios, con una `Identidad` por consorcio): suma U3 (vecinos que usaron en 7
días, de `turnos`, no de `ultima_actividad`, que es global), U10 con antigüedad, mediana de resolución propia y votos
negativos de la semana. **Sin USD**: el costo es de plataforma y no hay precio por cliente; queda en el CRM.

Aceptación de B: cada métrica F tiene un test en `test_crm.py` con datos inventados que fija el número exacto,
incluido un turno de evaluación que no debe contar; `crm.py` sigue sin importar `Mensaje` (test existente) y tampoco
importa columnas de texto de `solicitudes` (test nuevo por AST); con `?administracion=X` ningún número incluye
consorcios de otra Administración.

## C. Elevar una consulta a la administración

**Evaluación.** El camino por router (`pedido_usuario`) no alcanza: el vecino tiene que adivinar la frase, el router
falló en 1 de 16 mensajes con modelo real, y el caso más importante, una respuesta "verificada" pero que no le sirve,
hoy solo deja un 👎 que va a Revisar (calidad), no a la Bandeja (atención). Además, con el tope excedido o el agente
caído no hay forma de llegar a una persona. Se hacen dos caminos explícitos y se mantiene el del router.

**C1. Botón en la respuesta del chat.**
- Qué ve: bajo cada respuesta del asistente, junto al voto, un `<details>` "Pasar a la administración". Al abrirlo:
  texto "Le mandamos tu pregunta y la respuesta del asistente", un `textarea` opcional "¿Querés agregar algo?"
  (máx. 500) y el botón "Enviar a la administración".
- Dónde aparece: resultados `respondida`, `sin_datos`, `fuera_de_alcance`, `tope_excedido`, `error`. No en `derivada` ni
  `reclamo_pendiente` (ya tienen solicitud: muestran "Ver en Mis solicitudes"), ni en `mensaje_invalido` /
  `conversacion_invalida`. También en conversaciones reabiertas: `memoria.historial` tiene que devolver el `turno_id`
  de cada mensaje del asistente (join por `turnos.mensaje_asistente_id`); hoy no lo hace y por eso el voto tampoco
  aparece al volver a una conversación.
- Al tocar: `hx-post /chat/elevar` (`POST_MIEMBRO`: identidad + límite + CSRF) con `turno_id` y `nota`. En
  `bandeja.elevar_turno(identidad, turno_id, nota)`: el turno tiene que ser del usuario y del consorcio de la sesión
  (si no, `TurnoAjeno` → 404, como el feedback); se crea `Solicitud(tipo='derivacion', motivo='elevada_usuario')` con
  `mensaje` = texto del mensaje del vecino (+ nota), `titulo` = primeros 120 caracteres de la pregunta y `detalle` = la
  respuesta del asistente; y en la misma transacción `UPDATE turnos SET solicitud_id=:s WHERE id=:t AND solicitud_id IS NULL`.
  Si no actualiza, se hace rollback y se devuelve la solicitud ya existente (doble clic = una sola solicitud). No se toca
  `turnos.resultado`. Respuesta: fragmento "Listo: la administración lo va a ver (n.º N). Ver en Mis solicitudes".
- Migración: `motivo` suma `elevada_usuario` y `pedido_directo` al check `ck_solicitudes_motivo` (en la 0010, o 0011
  si la 0010 ya se aplicó). La bandeja los rotula "lo elevó el vecino desde una respuesta" / "mensaje directo".

**C2. Mensaje directo desde Mis solicitudes.**
- Qué ve: el botón actual "Nueva desde el chat" se acompaña de "Escribirle a la administración", que abre un form
  (asunto 3–120, mensaje 10–2000). Sin modelo: funciona con el tope excedido.
- Al tocar: `POST /mis-solicitudes` (`POST_MIEMBRO`) → `crear_solicitud(identidad, 'derivacion', 'pedido_directo', …)`,
  redirige a `/mis-solicitudes?solicitud=N`. No crea turno: no cuenta en U1 y sí en U9.

**Criterios de aceptación.** (1) Desde una respuesta `respondida`, un clic crea exactamente una derivación pendiente
visible en `/bandeja` del mismo consorcio y en Mis solicitudes del autor; dos POST seguidos dejan una sola. (2) Un
`turno_id` de otro usuario, de otro consorcio o inexistente da 404 igual en los tres casos y no crea nada. (3) Sin CSRF
→ 403 y no crea nada. (4) Un turno `derivada` no muestra el botón y su POST manual devuelve la solicitud existente.
(5) El botón aparece en una conversación reabierta. (6) La administración responde con "Responder y resolver" y la
respuesta aparece en Mis solicitudes (flujo actual). (7) El CRM cuenta `elevada_usuario` y `pedido_directo` en
`elevaciones.por_motivo` y U12, sin leer ningún texto. (8) El crawler de A cubre los dos forms.

**Casos borde.** demo-multi elevando en Palmeras (propietaria) no la ve en la Bandeja de Robles (donde administra):
la solicitud es del consorcio de la sesión. Una administración que eleva su propia pregunta la ve en su bandeja y la
puede resolver (misma regla que con los reclamos). Membresía revocada entre la respuesta y el clic → 403 con salida.
Cupo anti-spam: máx. 10 elevaciones + directos por usuario y día; pasado eso, mensaje claro y 429. Privacidad: el texto
solo vive en `solicitudes` y `mensajes` del consorcio; el CRM solo ve `motivo`, `estado` y fechas.

## D. Cambiar de consorcio sin pasar por /consorcios

**Flujo.**
- La píldora de contexto de la barra superior (`base.html`, "Consorcio · rol") pasa a ser un `<details>` (sin JS) cuando
  la persona tiene más de una membresía o administra alguna. Al abrirla: lista de sus consorcios (el actual primero, con
  tilde; después por Administración y nombre), cada uno con rol y, si administra ahí, el número de pendientes; al pie
  "Ver todos" (`/consorcios`) y, si administra, "Nuevo consorcio". En ≤900px la lista se abre a ancho completo bajo la
  barra, con filas de 44px.
- Cada fila es un form `POST /consorcios` con `consorcio_id`, CSRF y `destino` = la pantalla actual **sin query**
  (`?solicitud=` o `?turno=` son de otro consorcio). Si el rol en el consorcio elegido no puede ver esa pantalla
  (`/bandeja`, `/revisar` y no es administración), el servidor manda a `/chat` y no a un 403. `/panel` y `/crm` no
  dependen del consorcio: cambiar ahí deja al usuario en la misma pantalla.
- El panel sigue entrando a un consorcio directo a una pantalla (`destino`, ya existe). Plataforma sin membresías no ve
  el selector (el filtro del CRM es otra cosa).
- Con muchos consorcios (> 8) la lista muestra los 8 más usados por la persona en 30 días (`turnos`) y "Ver todos".

**Criterios de aceptación.** (1) Desde cualquier pantalla con sesión, cambiar de consorcio toma 2 clics y deja en la
misma pantalla o en `/chat` si esa no aplica; nunca en 403/404. (2) `consorcio_id` ajeno o inexistente → 403 y la
cookie no cambia (ya es así; test nuevo con el selector). (3) Sin CSRF → 403. (4) `destino` fuera de `DESTINO_VALIDO`
→ `/chat`; `DESTINO_VALIDO` suma `/panel`. (5) La barra muestra el consorcio nuevo en la respuesta siguiente. (6) Un
vecino con un solo consorcio ve la píldora como hoy, sin desplegable. (7) Recorrido en 1366/820/390px sin scroll
horizontal.

**Casos borde.** demo-multi en `/bandeja` de Robles → Palmeras (propietaria) termina en `/chat`. Pendientes por
consorcio: una consulta agregada por las membresías de administración, no N llamadas a `listar_solicitudes`. Dos
pestañas con consorcios distintos: la cookie es una sola, la última elección gana; los POST de la pestaña vieja
validan contra la identidad nueva y como mucho dan 404 (documentarlo, no resolverlo). Membresía revocada: desaparece
de la lista en el pedido siguiente.

## Orden de implementación

1. **Red de A primero**: handler de 500, error con sesión y salida, `test_recorrido.py` (crawler, negativos, CLI,
   imports perezosos, API de login). Corregir lo que encuentre antes de seguir.
2. **0010** (`administraciones`, `consorcios.administracion_id`, motivos nuevos), semilla demo, subcomando de CLI y
   herencia en `crear_consorcio`. `alembic check`, test de Telegram con la 0010.
3. **D**: selector en la barra con destino seguro. Lo cubre el crawler (por cada membresía).
4. **C1 y C2**: `historial` con `turno_id`, `/chat/elevar`, form directo, rótulos en la bandeja.
5. **B**: métricas F en `crm.py` con sus tests, filtro y tabla por Administración, tarjetas nuevas del CRM y del panel.
6. Bitácora, `docs/diseno/README.md` (selector y botón), `src/LEEME.md`, `docs/poc.md`.

## Fuera de esta tanda

- PDF subido por un vecino con aprobación de la administración (decidido, va después).
- Pantalla de Vecinos y unidades, invitaciones desde la web, "Olvidé mi contraseña".
- Cambios de comportamiento de Telegram (sigue congelado: solo se verifica que no se rompa) y canal en `gastos_modelo`.
- Permisos por Administración (un usuario que administra toda la empresa) y facturación por cliente.
- Métricas de retención/cohortes, alertas, exportar CSV y series semanales.
- Pestañas múltiples con consorcios distintos en simultáneo.
