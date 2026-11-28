# Plan de evaluación del agente (fijado el 21/11/2026)

Qué se mide, contra qué y con qué reglas, escrito **antes** de correr con un modelo. Los resultados van
en otro archivo de esta carpeta. Cubre el agente en las secciones 8 (evaluación) y 9 (costos) del
informe; la búsqueda ya tiene su propia evaluación (`2026-10-26-consultas-y-expensas.md`).

## Qué se mide

El **router**: una llamada al modelo por mensaje que elige el camino (reglamento, expensas,
documentos, reclamo, derivar o fuera de alcance) y saca los datos que ese camino necesita (período,
unidad, título del reclamo). Si elige mal, el sistema contesta otra cosa o le pasa trabajo a la
administración. Hasta ahora solo se probó a mano una vez, el 02/11, con 16 frases y `gpt-oss:120b`
(ver `docs/bitacora.md`): no hay un set con etiquetas ni un baseline. Los tests usan el router de la
demo (`agente/guion_fake.py`), que decide por palabras clave.

## Hipótesis

En la partición test, **el router con modelo acierta la intención en más mensajes que un router por
palabras clave**, y la diferencia está en los mensajes indirectos: los que no usan las palabras del
camino ("¿Qué papeles del edificio tienen subidos?" en vez de "¿Qué documentos hay cargados?").

Se sostiene si, en test, los mensajes que acierta solo el modelo son más que los que acierta solo el
baseline. Con 18 mensajes por partición, un mensaje mueve 5,6 puntos: por eso se informan cantidades
y pares, no solo porcentajes.

El precio de la mejora: el baseline decide sin llamar al modelo y el router con modelo suma una
llamada por mensaje. Se mide cuánto cuesta y cuánto tarda.

## Baseline

Un router por palabras clave con la misma salida que el del modelo (`SalidaRouter`). Parte de las
reglas de `guion_fake._intencion` y se completa en dev para que no sea un rival de paja: por ejemplo,
que entienda "agosto" como período, porque hoy solo entiende `2026-08`. El resto del grafo es igual en
las dos configuraciones (el mismo modelo responde reglamento y las mismas tablas responden expensas),
así que la diferencia mide solo el router.

## Set

`datos/evaluacion/mensajes_agente.csv`. Las columnas se explican en `datos/evaluacion/LEEME.md`. Hoy
tiene 12 mensajes, todos en dev. El 21/11 se probaron a mano en la web, también los 6 que eran de test,
y un mensaje que ya se vio no sirve para el número final. La meta es llegar a unos 36: 18 en dev (los 12
de hoy y 6 nuevos) y 18 en un test nuevo, parejos entre los 6 caminos y entre directos e indirectos.
Faltan:

- conversaciones de varios mensajes (el router ve los últimos 6 de la charla);
- otros roles: administración, consejo e inquilino;
- más casos de aislamiento entre consorcios;
- saludos ("Hola", "Gracias"). El 21/11, con el router de la demo, "Hola" fue a reglamento, no
  encontró respaldo y dejó una solicitud en la bandeja: cada derivación crea una (`nodos._derivar`).
  `skills/agente/SKILL.md` no dice nada de saludos.

Los datos son los de `consorcio sembrar-demo`: inventados, sin datos personales y reproducibles. Como
la demo tiene 4 documentos cortos, este set mide el router y los caminos, no la búsqueda.

Comprobado a mano el 21/11 en la web, con los datos de la demo: lo que tienen que contener las filas
3, 4 y 5 ($ 110.000,00, $ 200.000,00 y acta-demo.pdf) coincide con lo que muestra el sistema.

## Métricas

Por configuración y por partición:

| Métrica | Qué cuenta | De dónde sale |
|---|---|---|
| Acierto de intención | la intención elegida es `intencion_esperada` | `turnos.intencion` |
| Acierto de resultado | el turno terminó como `resultado_esperado` | `turnos.resultado` |
| Contiene el dato | la respuesta incluye `debe_contener`, sin tildes ni mayúsculas | respuesta de `atender` |
| Derivaciones de más | solicitudes creadas en mensajes que no tenían que llegar a la bandeja | `turnos.solicitud_id` |
| Derivaciones de menos | mensajes que tenían que llegar a la administración y no llegaron | `turnos.resultado` |
| Aislamiento | respuestas con datos de otro consorcio | filas marcadas en la nota del set |

Cada una se separa en directos e indirectos y por camino. Para la hipótesis, una tabla de pares:
aciertan los dos, solo el modelo, solo el baseline, ninguno.

Acertar la intención y errar el resultado señala al camino, no al router. Ejemplo: la fila 2 pregunta
por un perro y el reglamento de Los Robles habla de animales. El 02/11 "perro" no encontró "mascotas"
en la búsqueda, así que esa fila puede ir bien a reglamento y terminar derivada.

Las derivaciones de más y de menos miden el costo de la revisión humana: cada solicitud es trabajo
para una persona. El aislamiento no es un porcentaje: una sola falla es un error que hay que arreglar.

## Costo y latencia

Por mensaje, de la tabla `turnos`: tokens de entrada y de salida, segundos y costo. Ollama gratuito no
tiene precio (`costo_usd` queda vacío), así que el costo se estima con los precios de `config.py` para
Haiku 5.5 y Gemini 2.5 Flash, como en la evaluación de consultas.

- Por camino: cantidad de mensajes, mediana y máximo de segundos. Con 3 a 6 mensajes por camino, el
  p95 sería el máximo.
- Por partición: mediana y p95 de segundos, tokens y costo estimado por mensaje.
- Cuánto de eso es el router: la duración del paso `router` en la traza de MLflow de cada mensaje
  (`turnos.mlflow_traza_id`, ver `docs/observabilidad.md`).

## Reglas

1. Las etiquetas y la partición quedan fijas antes de la primera corrida con un modelo, revisadas a
   mano y commiteadas. No se cambian mirando resultados de test.
2. Cada mensaje corre en una conversación nueva, salvo las conversaciones de varios mensajes del set:
   así la memoria de una fila no influye en otra.
3. Las dos configuraciones corren con el mismo modelo, la misma base de demo y en el mismo orden.
4. Dev se corre todas las veces que haga falta. Cada cambio al prompt del router
   (`skills/agente/SKILL.md`) o a las reglas del baseline se anota en `docs/bitacora.md`.
5. Test se corre una vez por configuración, con las versiones finales. Antes no se prueba, ni siquiera a
   mano en la web. Lo que salga se informa, gane quien gane.
6. Los turnos se guardan con `origen='evaluacion'`, así no cuentan en el CRM.
7. El modelo de mentira (`LLM_PROVIDER=fake`) sirve para probar que el comando anda. Sus números no
   son resultados.
8. Los resultados crudos se guardan en JSON en esta carpeta, junto al informe de la corrida.

## Modelo y tope de gasto

`gpt-oss:120b` por Ollama cloud gratuito, el mismo de la evaluación de consultas. Si aparece una clave
paga, se repite con Haiku 5.5. `atender` se bloquea al pasar `TOPE_USD_MENSUAL`: con Ollama no suma
porque no tiene precio; con un modelo con precio, hay que mirar el gasto del mes antes de correr.

## Qué falta para correrla

- Completar el set y revisar sus etiquetas (22 a 24/11).
- El comando `consorcio evaluar-agente` y el baseline, probados con el modelo de mentira (23/11).
- La clave de Ollama: corrida en dev el 28/11 y en test el 29/11.
