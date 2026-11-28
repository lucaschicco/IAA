# Spec: tapar los datos personales en las trazas de MLflow (27/11/2026)

El grupo eligió el 27/11 la opción B de `docs/observabilidad.md`: las trazas guardan el texto, pero con
los datos personales reemplazados y sin la identidad completa. Jess sumó al aprobarla: también se tapan
nombres y direcciones; la ingesta sigue subiendo `texto.md` y `extraccion.json` al run; las trazas
viejas con datos reales se borran; quién entra a MLflow no se define en este trabajo. Esta spec se
escribe y se aprueba antes del código; los tests de la sección "Casos" son la definición de terminado.

## Cómo lo hace MLflow (verificado en la 3.18)

- Se registra una función con `mlflow.tracing.configure(span_processors=[funcion])`
  ([Redacting Sensitive Data from Traces](https://mlflow.org/docs/latest/genai/tracing/observe-with-traces/masking/)).
  Recibe cada span y lo cambia en el lugar con `span.set_inputs` y `span.set_outputs`.
- En el código de la 3.18 (`LiveSpan.end`, que llama a `apply_span_processors`), la función corre al
  cerrar cada span, cuando ya tiene la entrada y la salida, y antes de mandarlo al servidor: el dato
  tapado nunca llega a MLflow. Vale también para los spans del autolog de LangChain.
- El resumen de la traza (las columnas Input y Output de la lista) se arma después, en
  `set_request_response_preview`, con la entrada y la salida del span raíz ya tapadas.
- Si la función lanza una excepción, MLflow solo avisa y manda el span **sin tapar**. Por eso la
  función nuestra no puede fallar: si algo sale mal, borra el texto en vez de dejarlo pasar.
- Para borrar: `MlflowClient().delete_traces(experiment_id, max_timestamp_millis=...)` borra las trazas
  anteriores a un momento y devuelve cuántas borró. No toca los runs ni sus archivos.

## Qué se tapa

| Dato | Cómo se detecta | Queda |
|---|---|---|
| Mails | Patrón | `[MAIL]` |
| CUIT y CUIL | 11 dígitos que empiezan con 20, 23, 24, 27, 30, 33 o 34, con o sin guiones | `[CUIT]` |
| Teléfonos | Con separadores (con o sin `+54`, `9`, código de área o `15`) y celulares de 10 dígitos seguidos | `[TELEFONO]` |
| DNI | 7 u 8 dígitos, con o sin puntos, salvo que sea un importe: después de `$` o seguido de coma y decimales | `[DNI]` |
| Direcciones | Patrón: `calle`, `av.`, `avenida`, `pasaje`, `diagonal` o `domicilio en`, seguido de un nombre y un número. Y las conocidas (abajo) | `[DIRECCION]` |
| Nombres | Patrón: después de `Sr.`, `Sra.`, `señor`, `señora`, `Dr.`, `Dra.`, `don`, `doña`, `vecino`, `vecina`, `propietario`, `propietaria`, `inquilino` o `inquilina`, hasta tres palabras con mayúscula (la palabra de antes queda). Y los conocidos (abajo) | `[NOMBRE]` |
| Identidad del turno | La clave `identidad` de la entrada de cada span. Consorcio y rol ya están en las etiquetas de la traza, y el usuario, por su número, en la metadata | `[identidad]` |

**Conocidos.** Son los nombres y direcciones que el sistema ya sacó de los documentos y de la web:
`personas.nombre`, `personas.domicilio`, `direcciones.texto`, `liquidacion_unidades.propietario` y
`usuarios.nombre`, de todos los consorcios. Se buscan sin distinguir mayúsculas ni tildes, como palabras
enteras, y solo los de 5 letras o más. Se leen de Postgres y se guardan 5 minutos en memoria; si
Postgres no contesta, se sigue con la última lista leída y con los patrones.

## Qué cambia

1. **`src/consorcio/datos_personales.py`** (nuevo):
   - `tapar(texto, conocidos=None) -> str`: aplica la tabla de arriba.
   - `tapar_valor(valor, conocidos=None)`: recorre diccionarios, listas y tuplas y tapa cada texto. No
     cambia claves ni números.
   - `cargar_conocidos() -> Conocidos`: lee los nombres y direcciones de Postgres.
2. **`src/consorcio/observabilidad.py`**:
   - `_tapar_span(span)`: tapa la entrada y la salida de cada span y reemplaza la identidad. Si algo
     falla, deja `"[no se pudo tapar]"` en la entrada y en la salida.
   - `_configurar` lo registra una vez por proceso, junto con el servidor y el experimento. Vale para
     todas las trazas: chat, consulta, ingesta y evaluación.
3. **`consorcio borrar-trazas --hasta AAAA-MM-DD [--confirmar]`** (y `make borrar-trazas HASTA=...`):
   sin `--confirmar` solo cuenta las trazas anteriores a esa fecha (00:00 UTC) en el experimento
   configurado; con `--confirmar` las borra y dice cuántas. Cada integrante lo corre en su máquina, con
   la fecha en que empezó a tapar.
4. **Documentación**: `docs/observabilidad.md` (qué queda en la traza y qué no), `src/LEEME.md`, el
   README (el comando nuevo) y la bitácora.

## Fuera de alcance

- Un nombre que no está en la base y no viene después de una de esas palabras no se tapa, por ejemplo
  "le pregunté a Gómez". Tampoco el apellido solo de un conocido ("Pérez" si se conoce "Juan Pérez").
  Queda escrito en `docs/observabilidad.md`.
- En la ingesta, los nombres del documento que se está cargando todavía no están en la base: en esa
  traza se tapan solo por patrón. Además, `texto.md` y `extraccion.json` se siguen subiendo al run
  completos, como decidió el grupo.
- No tapa fechas de nacimiento: con una regla automática taparía también vencimientos y fechas de actas.
- No toca Postgres ni la web: el texto completo sigue ahí, detrás del login y separado por consorcio.
- No cambia quién entra a MLflow: sigue publicado solo en `127.0.0.1` (hecho el 20/11).

## Casos

En `tests/test_datos_personales.py`:

1. Tapa `ana.perez@ejemplo.com`, `20-12345678-3`, `20123456783`, `DNI 12.345.678`, `12345678`,
   `11 4567-8901`, `+54 9 11 4567-8901`, `15-4567-8901`, `4567-8901` y `1145678901`, y deja el texto de
   alrededor.
2. No tapa importes, períodos ni unidades: `$ 110.000,00`, `1.100.000,00`, `$ 1.100.000`, `2026-08`,
   `vence 2026-08-10`, `UF 3`, `2-A`, `de 22 a 8`, `4,5 %`.
3. Por patrón: `Av. Rivadavia 1234` y `calle Mercedes 274` quedan `[DIRECCION]`; `la Sra. Ana Pérez` queda
   `la Sra. [NOMBRE]`; `el vecino del 3B` no cambia.
4. Con conocidos: `JUAN CARLOS PEREZ` conocido tapa `Juan Carlos Pérez` en el texto, y una dirección
   conocida tapa esa dirección; no tapa partes de otras palabras ni los de menos de 5 letras.
5. `tapar_valor` tapa dentro de un diccionario anidado con listas y deja igual las claves y los números.
6. `cargar_conocidos`, con la base de la demo, trae los nombres de sus usuarios.

En `tests/test_observabilidad.py`, contra MLflow real, como los demás:

7. Un turno con un mail, un DNI, un teléfono y un nombre en la entrada y en la salida: ningún span ni el
   resumen de la traza tiene esos valores, y sí `[MAIL]`, `[DNI]`, `[TELEFONO]` y `[NOMBRE]`, con el
   resto del texto.
8. La identidad no aparece en ningún span.
9. Si tapar falla, el span se guarda con `"[no se pudo tapar]"`, nunca con el texto original.
10. `borrar-trazas`: sin `--confirmar` cuenta y no borra; con `--confirmar` borra las anteriores a la
    fecha y deja las posteriores y los runs.

## Verificación

`make test && make lint` en el Codespace. En la web, un mensaje con un mail y un nombre inventados: en
MLflow, la traza de ese turno muestra `[MAIL]` y `[NOMBRE]`. Después, `make borrar-trazas HASTA=<fecha>`
en el Codespace y el aviso al grupo para que lo corran en sus máquinas.

Partes: 1) patrones (casos 1 a 3 y 5); 2) conocidos (4 y 6); 3) aplicarlo a las trazas (7 a 9);
4) borrar las trazas viejas (10).
