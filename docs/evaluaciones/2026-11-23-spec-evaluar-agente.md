# Spec: `consorcio evaluar-agente` (23/11/2026)

Corre los mensajes de `datos/evaluacion/mensajes_agente.csv` por el agente completo (`atender`) con dos
routers, el del modelo y uno por palabras clave, y mide lo que pide el plan
(`2026-11-21-plan-agente.md`). Esta spec se escribe y se aprueba antes del código; los tests de la
sección "Casos" son la definición de terminado.

## Interfaz

```
consorcio evaluar-agente datos/evaluacion/mensajes_agente.csv [--particion dev|test] [--router modelo|palabras|ambos] [--test-final] [--sin-detalle]
make evaluar-agente [PARTICION=dev] [ROUTER=ambos]
```

- Por defecto: `--particion dev` y `--router ambos`.
- `--particion test` sin `--test-final` no corre nada y lo dice: el test se corre una vez y a propósito
  (regla 5 del plan).
- Antes de empezar comprueba que estén los consorcios y usuarios de la demo. Si faltan, corta y pide
  `consorcio sembrar-demo`. No siembra sola: en una base real agregaría dos edificios inventados.
- Imprime un JSON con el resumen y, salvo `--sin-detalle`, el detalle por mensaje. Con un modelo real
  (no `fake`) además lo guarda en `docs/evaluaciones/<fecha>-agente-<particion>.json`. El detalle no
  tiene datos personales: son los datos inventados de la demo.

## Qué hace con cada mensaje

1. Arma la identidad con `resolver_identidad(usuario, consorcio)`.
2. Llama a `atender(mensaje, identidad, conversacion, canal="cli", origen="evaluacion")`, en una
   conversación nueva, salvo que la fila comparta `conversacion` con una anterior.
3. Lee de su fila en `turnos` la intención, el resultado, la solicitud, los tokens, el costo, los segundos
   y la traza de MLflow. Si MLflow está prendido, también la duración del paso `router` en esa traza.
4. La compara con las etiquetas: intención, resultado, `debe_contener` (sin tildes ni mayúsculas, con
   `normalizar`) y derivación de más o de menos.

## Router por palabras clave (baseline)

- Función nueva con la misma salida que el router del modelo (`SalidaRouter`). Parte de las reglas de
  `guion_fake._intencion` y suma meses escritos con palabras (el último mes con ese nombre, contando el
  actual) y frases sobre la unidad propia ("tengo que pagar", "pagué", "me vino", "mi", "debo").
- No tiene memoria: "¿Y en julio?" no trae ninguna palabra clave. Eso también se mide.
- Se enchufa solo durante la corrida: reemplaza el nodo `router` del grafo dentro de un bloque que lo
  restaura al salir. El resto del grafo es el mismo y `grafo.py` no cambia.
- Sus reglas se ajustan solo mirando dev, igual que el prompt del router.

## Lo que devuelve, por router

- Acierto de intención y de resultado (n de N), separados en directos e indirectos y por camino.
- Contiene el dato: n de N filas con `debe_contener`.
- Derivaciones de más y de menos.
- Tokens, costo y segundos por mensaje: mediana y p95 en total; por camino, mediana y máximo. Si el
  modelo no tiene precio (Ollama), el costo se estima con los precios de `config.py` para Haiku 5.5 y
  Gemini 2.5 Flash.
- Con `ambos`: la tabla de pares de la intención (aciertan los dos, solo el modelo, solo las palabras
  clave, ninguno).
- Un run en MLflow (`evaluacion-agente`) con las métricas, como `evaluar-consultas`.

## Fuera de alcance

- No borra las solicitudes que dejan los reclamos y las derivaciones: quedan pendientes en la bandeja de
  la demo, y volver a sembrar no las borra.
- No mide la redacción más allá de `debe_contener`, ni la búsqueda (eso lo mide `evaluar-consultas`).
- No corre sobre datos reales ni sobre otro set. No usa un modelo como juez.

## Archivos

- Nuevos: `src/consorcio/agente/evaluar.py` (lectura del set, corrida, métricas y router por palabras
  clave) y `tests/test_evaluar_agente.py`.
- Compartidos, solo con agregados: `src/consorcio/cli.py` (el subcomando), `Makefile` (el target),
  `src/LEEME.md` (el módulo nuevo) y `README.md` ("Cómo correrlo").

## Casos (tests con el modelo de mentira y la base de prueba sembrada)

1. Router por palabras clave: "¿Qué documentos hay cargados?" va a documentos; "Pasame una receta de
   empanadas" va a reglamento, por descarte; "¿Cuánto tengo que pagar de expensas en agosto?" va a
   expensas, con período `AAAA-08` y unidad propia.
2. Una corrida de tres filas (expensas, documentos y un reclamo) con `--router palabras`: cuenta bien los
   aciertos, encuentra `debe_contener`, y el reclamo deja una solicitud que no cuenta como derivación de
   más.
3. Dos filas con la misma `conversacion` van a la misma conversación.
4. `--particion test` sin `--test-final` no crea ningún turno.
5. Sin la demo sembrada, corta con el mensaje que pide `consorcio sembrar-demo`.
6. Los turnos quedan con `origen='evaluacion'`, así que el CRM no los cuenta.
7. Con el modelo de mentira no se guarda nada en `docs/evaluaciones/`.

## Verificación

En el Codespace: `make test && make lint` en verde, con los tests nuevos incluidos. Después,
`make evaluar-agente` con el modelo de mentira imprime sobre dev el resumen de los dos routers.
