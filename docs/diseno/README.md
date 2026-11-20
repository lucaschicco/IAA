# Diseño de la web: mockups y especificación de UX/UI

Estado al 20/11/2026, rama `feat/diseno-web`. Este documento fija cómo se ve y cómo se usa cada pantalla de la
web, qué se tomó de los mockups y qué quedó afuera. Los mockups están en [`mockups/`](mockups/) (HTML de un lienzo
de diseño; se abren en el navegador, con datos inventados y la marca como `[marca]` porque el nombre todavía no está
definido). La implementación vive en `src/consorcio/web/plantillas/` y `src/consorcio/web/estaticos/`.

## 1. Referencias y decisiones

- **Referencia visual:** una app de cursos de idiomas ("toko"): fondo casi negro, un recuadro marrón con un
  titular enorme en Inter Tight, cartas crema y naranja inclinadas con dibujos de línea, píldoras naranjas,
  tarjetas oscuras con listas en filas e íconos en círculo de borde fino. Se evita el "estilo IA" genérico
  (bloques naranjas con bordes punteados, degradés, sombras de colores).
- **Referencia de tablero (CRM):** un dashboard claro de métricas: menú a la izquierda, filtros arriba, hasta
  5–6 tarjetas por vista, KPIs con variación, un gráfico principal y tablas.
- **Dos temas según quién usa la pantalla:**
  - **Oscuro** (lo del vecino): ingresar, crear cuenta, consorcios, error, chat, documentos, mis solicitudes.
  - **Claro** (administración y plataforma): bandeja, revisar respuestas, CRM. Misma paleta invertida: fondo
    claro, y negro, naranja, verde y rojo para contornos, letras y gráficos.
- **Datos:** los mockups usan datos de ejemplo. Nada de datos reales en mockups ni capturas.

## 2. Restricciones técnicas (no negociables)

La CSP es `default-src 'self'` y no hay JavaScript propio (solo htmx con `allowEval:false`):

- Sin `style=`, `<style>` ni `on*=` en el HTML: todo va por clases en `web.css` / `crm.css`.
- Sin recursos externos: las fuentes **Inter** e **Inter Tight** se sirven desde `/estaticos/fuentes/`
  (variables, latin, licencia OFL en `OFL.txt`). El `@font-face` va en `web.css` porque `crm.css` no puede tener
  `url(` (lo verifica un test).
- Tamaños que dependen de datos: atributos SVG (`height`, `stroke-dasharray` con `pathLength="100"`) o `<progress>`.
- Interacción sin scripts: el menú se abre con una casilla (`#menu`) y CSS; los paneles plegables son
  `<details>`; lo condicional se resuelve con `:has()` (ej.: los motivos de error se ocultan si el veredicto
  es "Correcta").
- Los estáticos se sirven con `Cache-Control: no-cache` (revalidan por ETag): sin eso el navegador seguía con el
  CSS viejo después de un cambio.

## 3. Tokens

Definidos como variables en `web.css`; cada tema es una clase en `<body>` (`tema-oscuro` / `tema-claro`, bloque
`tema` de `base.html`).

| Token | Oscuro | Claro | Uso |
|---|---|---|---|
| `--fondo` | `#0e0e0e` | `#f3eee7` | fondo de página |
| `--panel` | `#151515` | transparente | panel redondeado del contenido |
| `--superficie` | `#1f1f1f` | `#fffdfa` | tarjetas |
| `--superficie-2` | `#262626` | `#f3eee7` | cajas dentro de tarjetas |
| `--borde` | `#2e2e2e` | `#e3dbd0` | bordes finos |
| `--texto` / `--suave` | `#f5f1eb` / `#a8a29b` | `#141414` / `#5f574e` | texto / secundario |
| `--enlace` | `#ff8a5c` | `#c2410c` | enlaces (naranja oscuro en claro, por contraste) |
| `--verde`, `--ambar`, `--rojo` | `#7fd49b`, `#f2b45c`, `#ff9480` (con fondos `#1f3527`, `#3a2a14`, `#3a1c16`) | `#15803d`, `#c2410c`, `#b91c1c` | estados |

Fijos: naranja `#ff5b24` (solo formas y botones; en claro nunca para texto chico), crema `#f6f2ec`,
tinta `#141414`, marrón `#3a2016` con texto `#f2c9b6`.

Tipografía: Inter 15px para el cuerpo; Inter Tight 700–900 con `letter-spacing` negativo para títulos. Los
titulares de portada usan `clamp()` (2–4rem). Radios: 18px tarjetas, 28px paneles grandes, 99px píldoras.

## 4. Componentes

- **Armazón** (`base.html`): menú lateral agrupado (Consorcio · Administración · Plataforma), con Consorcios y
  Salir abajo. Ítem activo: fondo `--menu-activo` e ícono naranja (en claro, además, una línea naranja a la
  izquierda). Barra superior con el consorcio y el rol en píldora y un avatar. En escritorio la casilla reduce
  el menú a íconos; en ≤900px lo despliega encima con un velo.
- **Sin sesión:** pantalla partida: recuadro marrón de bienvenida (titular + tres cartas inclinadas) y el
  formulario a la derecha. Cada pantalla puede reemplazar el recuadro (bloque `bienvenida`).
- **Botones:** píldoras. Oscuro: naranja con texto negro. Claro: negro con texto claro. Variantes `secundario`
  (contorno), `peligro` (contorno rojo), `enlace`.
- **Chips / estados:** píldoras chicas. En oscuro, fondo de color tenue; en claro, solo contorno del color.
- **Pestañas** (`.pestanas`): grupo de píldoras dentro de un contorno; la activa, rellena.
- **Lista y detalle** (`.dos-paneles`): lista de 300–420px a la izquierda, detalle a la derecha; la selección va
  en la URL (`?solicitud=`, `?turno=`) y por defecto se elige la primera. En claro la lista es una sola tarjeta
  con filas separadas por líneas.
- **Íconos:** SVG de línea en `_iconos.html` (macro `icono`), trazo 1.8, `currentColor`.
- **Selector de consorcio** (`.contexto-menu`, `base.html`): con más de una membresía o si administra alguna, la
  píldora de la barra es el `<summary>` de un `<details>` (sin JS; teclado nativo, texto "Cambiar de consorcio"
  para lectores de pantalla) con un chevrón que gira al abrir. La lista (`.contexto-lista`) cae bajo la píldora,
  alineada a la derecha, de hasta 340px: "Tus consorcios", el actual primero con tilde naranja y `aria-current`,
  después el resto por nombre (máx. 8), cada uno con rol y, donde administra, "N pendientes". Cada fila es un
  botón de un form `POST /consorcios` con `consorcio_id` y `destino` = la pantalla actual sin query; si con el rol
  nuevo esa pantalla daría 403 (`/bandeja`, `/revisar`), el servidor manda a `/chat`. Pie: "Panel" y "Nuevo
  consorcio" (si administra) y "Ver todos". Filas de 44px. En ≤900px la lista pasa a `position: fixed` a ancho
  completo bajo la barra (10px de margen) con scroll propio. Un vecino de un solo consorcio ve la píldora como
  texto, como antes.
- **Pasar a la administración** (`_acciones_turno.html`): bajo cada respuesta del asistente, después del voto, un
  `<details class="elevar">` con línea fina arriba y summary chico en color de enlace (ícono de flecha arriba).
  Abierto: "Le mandamos tu pregunta y la respuesta del asistente", `textarea` opcional (máx. 500) y botón
  secundario "Enviar a la administración". htmx (`hx-target="closest details"`) lo reemplaza por `.elevado`: tilde
  verde y "Lo pasaste a la administración (n.º N). Ver en Mis solicitudes". Aparece en `respondida`, `sin_datos`,
  `fuera_de_alcance`, `tope_excedido` y `error`; un derivado o un reclamo muestran solo "Ver en Mis solicitudes".
  Igual al reabrir una conversación (también el voto, o el "Gracias…" si ya votó).
- **Escribirle a la administración** (`.escribir`, Mis solicitudes): tarjeta plegada bajo el encabezado, con
  asunto (3–120) y mensaje (10–2000) y el botón primario. Si falla la validación o el cupo, vuelve abierta con la
  alerta y lo escrito.

## 5. Pantallas

Para cada una: mockup de referencia, qué hace la pantalla y cómo responde en celular.

### Ingresar — `/entrar` · [Login](mockups/Login.dc.html) · oscuro
Recuadro marrón "Preguntá lo que quieras sobre tu consorcio" con tres cartas de ejemplo (mascotas, expensas,
portón). Formulario: mail, contraseña, error en una alerta con ícono, botón "Entrar →". Debajo, cómo entrar con
una invitación. En modo desarrollo, los usuarios demo como botones. Celular: el recuadro queda arriba sin cartas.
No hay "Olvidé mi contraseña" (necesita envío de mails).

### Crear cuenta — `/registro?token=` · [Registro](mockups/Registro.dc.html) · oscuro
El recuadro muestra la invitación como carta crema (consorcio, rol, para quién) y explica que con un mail que ya
tiene cuenta se usa la contraseña de siempre. Si el token no sirve: "Esta invitación ya no sirve" y un botón a
Ingresar. Texto neutro en género ("Sumate a tu consorcio").

### Elegir consorcio — `/consorcios` · [Consorcios](mockups/Consorcios.dc.html) · oscuro
Titular centrado y una carta por consorcio (naranja, crema, oscura, inclinadas), con dibujo de edificio, nombre y
rol; marca el actual. Sin consorcios: alerta para pedir acceso.

### Error — [Error](mockups/Error.dc.html) · oscuro
"Esta puerta no lleva a ningún lado", el mensaje del error y un botón para volver; a la derecha, una carta
naranja con una puerta. Sin sesión usa la pantalla partida. Por htmx se devuelve solo el fragmento `_error.html`.

### Chat — `/chat` · [Chat](mockups/Chat.dc.html) · oscuro
- Cabecera "¿En qué te ayudo hoy?" y "Nueva conversación". Burbujas: del vecino en naranja a la derecha, del
  asistente oscuras a la izquierda. Voto con pulgares (con `aria-label` "Me sirvió" / "No me sirvió").
  Debajo, "Pasar a la administración" (ver Componentes).
- Vacío: explicación corta y tres sugerencias que se envían con un clic (form htmx, sin JS propio). La
  bienvenida desaparece con el primer mensaje (`:only-child`).
- Caja de envío en píldora con botón redondo; indicador "Pensando…".
- Columna derecha (≥1180px): últimas 3 solicitudes del vecino y últimos 3 documentos. La tarjeta de expensas del
  mockup no está: necesita datos por unidad.
- Celular: el chat ocupa toda la pantalla, sin el panel redondeado.

### Documentos — `/documentos` · [Documentos](mockups/Documentos.dc.html) · oscuro
- Portada marrón con "N documentos que el asistente puede consultar", botón "Preguntar en el chat" y los tres
  últimos documentos activos como cartas inclinadas numeradas 01–03.
- Panel oscuro con la lista en filas: ícono por tipo en círculo, título, archivo, tipo y páginas. Filtro por tipo
  (`?tipo=`) con píldoras grises; la activa en crema.
- **Administración:** una sección plegable por cada consorcio que administra (el de la sesión primero, marcado
  "actual"; las de más de 6 documentos arrancan cerradas). Solo los que administra: donde es vecina ve solo el de
  la sesión. La carga va al consorcio de la sesión, y la tarjeta lo dice.
- Costado: tarjeta "Subir un PDF" (solo administración; hasta 20 MB, sin duplicar) y "Cómo se usan" en tres pasos.
  Al cargar, htmx refresca solo la lista del consorcio actual (`#lista-documentos`).
- Celular: sin cartas ni columna de tipo; el costado pasa abajo.
- Primera versión descartada: grilla de tarjetas con zona de carga naranja de borde punteado ("muy IA").

### Mis solicitudes — `/mis-solicitudes?solicitud=` · [Solicitudes](mockups/Solicitudes.dc.html) · oscuro
Lista (tipo, fecha, título, estado) y detalle con el seguimiento en tres pasos según el estado: lo pediste → la
administración lo está viendo → respuesta. La respuesta va en una caja crema. Arriba, "Escribirle a la administración"
(mensaje directo, sin modelo). El primer paso dice cómo llegó: desde el chat, desde una respuesta elevada o
escrito directo; en una elevada, el detalle muestra la pregunta y, en una caja, lo que había respondido el asistente.

### Bandeja — `/bandeja?ver=pendientes|resueltas&solicitud=` · [Bandeja](mockups/Bandeja.dc.html) · claro
Pestañas con conteo. Detalle: autor con inicial, lo que armó el asistente al lado del mensaje original, y las
decisiones: derivación → "Responder y resolver"; reclamo → "Aprobar", y plegados "Editar y aprobar" y "Rechazar"
(motivo obligatorio). Nada se ejecuta sin una decisión.

### Revisar respuestas — `/revisar?ver=&turno=` · [Revisar](mockups/Revisar.dc.html) · claro
Cola a la izquierda (lo más dudoso primero: voto negativo, certeza baja, derivadas), foco a la derecha: chips de
resultado, certeza y prioridad; la pregunta y la respuesta como burbujas. Veredicto con tres botones grandes
(Correcta / Parcial / Incorrecta); los motivos son chips y se ocultan si es "Correcta". "Guardar y seguir" vuelve a
la cola y toma la siguiente. No muestra quién preguntó, solo el rol.

### CRM — `/crm?vista=resumen|calidad|uso&dias=&consorcio=` · [Resumen](mockups/Main.dc.html), [Calidad](mockups/Calidad.dc.html), [Uso](mockups/Uso.dc.html) · claro
Tres vistas de hasta 6 tarjetas, con filtros de período y consorcio arriba que se conservan al cambiar de vista
(subítems del menú y pestañas).
- **Resumen:** 4 KPIs (preguntas, activos en 7 días, elevaciones, documentos) con variación contra el período
  previo en píldora verde o roja (más elevaciones es rojo); actividad por día (barras negras y naranjas); cómo
  terminaron; por consorcio; funcionalidades; tope mensual (global).
- **Calidad:** anillos de acertadas (cita verificada, feedback, exactitud en evaluación; no se promedian),
  revisión de la administración, calibración del medidor de certeza, por intención, motivos de error, elevaciones.
- **Uso y costos:** tope mensual, costos por funcionalidad (y modelos sin precio), usuarios y uso por rol, uso
  por usuario (por id), documentos y contextos, reclamos.
Solo agregados: no muestra textos ni nombres. Del mockup faltan series semanales, costo por día y "últimas
revisiones", porque esos datos todavía no se calculan.

### Vecinos y unidades · [Vecinos](mockups/Vecinos.dc.html) · claro — **no implementada**
Alta de unidades, invitaciones y roles por consorcio. Necesita backend nuevo.

## 6. Responsive

Cortes: 1180px (se va la columna derecha del chat; CRM de 4 a 2 KPIs), 900px (menú desplegable, paneles a una
columna, pantallas sin sesión apiladas), 640px y 480/420px (burbujas al 92%, filtros a ancho completo, KPIs en
una columna). Áreas táctiles de 44px o más. Movimiento reducido con `prefers-reduced-motion`.
Recorrido hecho el 20/11 con cada tipo de usuario (vecino, multi, administración de uno y de dos consorcios,
plataforma) en 1366, 820 y 390px: sin scroll horizontal en ninguna pantalla. Ajustes que salieron de ahí:
- Lista + detalle (bandeja, revisar, mis solicitudes): por debajo de 900px, con un ítem elegido (`?solicitud=`/`?turno=`)
  se oculta la lista y el detalle lleva "Volver a la lista" (`.con-eleccion`, `.volver-lista`).
- Píldora de contexto en una línea: se recorta el nombre del consorcio con elipsis y el rol queda entero, con tilde.
- Pantallas sin sesión apiladas: las filas no se estiran a toda la altura (`align-content: flex-start`).
- `pointer: coarse`: pestañas, filtros de documentos, desplegables y subítems del menú con 44px.
- Bandeja: el motivo se muestra en palabras ("sin respaldo en los documentos"), no el código.

### Nuevo consorcio (`/consorcios/nuevo`)

Solo para quien administra al menos un consorcio. En Consorcios aparece como una carta más, transparente con borde fino
y un "+" naranja (`.opcion-nueva`), en lugar de la nota de la invitación. El formulario (nombre, dirección opcional) usa
el mismo estilo que Ingresar. Al crear, entra a Documentos, que con el consorcio vacío dice "Todavía no hay documentos en
…" y lleva a la carga.

## 7. Pendientes

- Vecinos y unidades; "Olvidé mi contraseña"; etapas de progreso al cargar un PDF; tarjeta de expensas en el chat.
- Datos para las tarjetas faltantes del CRM (series semanales, costo por día, últimas revisiones).
- Nombre y logo definitivos (hoy "Consorcio" con un edificio de línea).
- Usuario demo `demo-admin-dos` (administra los dos consorcios demo) para probar la vista por consorcio.
