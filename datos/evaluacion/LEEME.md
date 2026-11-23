# Evaluación

`preguntas.csv`: preguntas de prueba para `consorcio evaluar-consultas`. Columnas:

- `id`, `particion` (`dev` para iterar, `test` solo para el número final; se asignan alternadas y no se mueven).
- `pregunta`: tal como la escribiría un vecino.
- `documento`: fragmento del nombre del archivo del que debería salir la respuesta; varias alternativas se separan con `|` (el acta del 30/04/2026 transcribe el reglamento de cocheras, así que ambos valen).
- `seccion_clave`: palabra o frase que tiene que aparecer en el título o resumen de la sección correcta (sin acentos; se compara normalizado). Vacía: alcanza con acertar el documento (p. ej. el orden del día de una convocatoria, que quedó partido en una sección por punto).
- `debe_derivar`: `si` para preguntas que el sistema NO debe responder (deuda de otra unidad, datos personales); no cuentan para el recall, sí para la tasa de respuestas correctas.
- `respuesta_esperada`: texto libre para revisión humana.

Las 26 preguntas se escribieron mirando las secciones que la ingesta dejó en Postgres (26/10/2026), así que
miden si el buscador y el modelo encuentran lo que está, no lo que debería estar: 7 del reglamento de
cocheras, 7 de las actas, 2 de las convocatorias, 2 de la liquidación de febrero de 2026, 6 del reglamento
de copropiedad y 2 "trampa". Ampliar cuando se revisen a mano las transcripciones del reglamento y las actas.

Las preguntas son de Mercedes 274: `evaluar-consultas` corre sobre el consorcio `CONSORCIO_POR_DEFECTO` (`mercedes-274`) salvo que se pase `--consorcio`. Si se agregan preguntas de otro consorcio, agregar una columna `consorcio` al CSV.

## Mensajes del agente

`mensajes_agente.csv`: mensajes de vecinos para evaluar el agente completo (`atender`). `preguntas.csv` mide
solo `preguntar`, el camino de reglamento, y el router nunca se evaluó. Este set mide si cada mensaje va al
camino correcto y termina como corresponde. Está escrito sobre los datos inventados de `consorcio
sembrar-demo` (`src/consorcio/agente/semilla.py`), así que no tiene datos personales y cualquiera lo puede
reproducir sin `datos/` ni el volcado de la base. Columnas:

- `id` y `particion`: `dev` para iterar, `test` solo para el número final. En cada partición, cada camino
  tiene mensajes directos e indirectos.
- `consorcio` y `usuario`: `consorcio` es el edificio y `usuario`, el `sujeto_externo` del usuario demo que
  escribe. El rol y las unidades salen de la semilla: Pablo (`demo-prop-palmeras`) es propietario de la UF 3
  de Las Palmeras, Ana (`demo-admin-palmeras`) es su administradora, Rita (`demo-prop-robles`) es propietaria
  de la UF 1 de Los Robles y Marta (`demo-multi`) es propietaria en Las Palmeras y administradora en Los
  Robles. La demo no tiene usuarios de consejo ni inquilinos, así que esos roles no se miden.
- `conversacion`: vacía, el mensaje va en una conversación nueva. Con un valor (`d1`), los mensajes que lo
  comparten van en la misma conversación, en orden de `id`: así se mide la memoria del router.
- `mensaje`: tal como lo escribiría un vecino.
- `intencion_esperada`: uno de los seis caminos del router, `reglamento`, `expensas`, `documentos`,
  `reclamo`, `derivar` y `fuera_de_alcance`.
- `resultado_esperado`: cómo debería terminar el turno, con el vocabulario de `turnos.resultado`
  (`respondida`, `derivada`, `reclamo_pendiente`, `fuera_de_alcance`, `sin_datos`). Se mide aparte de la
  intención: un pedido que el router manda a reglamento puede terminar bien derivado igual.
- `debe_contener`: texto que tiene que aparecer en la respuesta, con la cita incluida (se compara sin
  acentos ni mayúsculas). Los importes van como los escribe la respuesta (`110.000,00`) y salen de la
  semilla, no de una corrida. Vacío: alcanza con el resultado.
- `estilo`: `directo` si nombra el tema con la palabra más obvia (expensas, reclamo, documentos) e
  `indirecto` si dice lo mismo sin ella. Un router por palabras clave debería acertar los directos y fallar
  los indirectos. Por eso se informan por separado.
- `nota`: por qué está el caso, para quien revise las etiquetas.

Las etiquetas se fijan y se revisan a mano antes de correr con un modelo real, y no se cambian mirando los
resultados de `test`. Los mensajes de `test` no se prueban antes de la corrida final, ni siquiera a mano en
la web: el 21/11 se probaron así los 12 primeros, también los 6 de `test`, y por eso pasaron todos a `dev`.
Hay 36 mensajes, 18 en `dev` y 18 en `test` (3 por camino). Jess revisó las etiquetas de los 36 el 23/11:
desde ahí, etiquetas y partición quedan fijas.
Además de los seis caminos, el set cubre una conversación de dos mensajes ("¿y en julio?"), un saludo, la
administración consultando otra unidad y casos en los que los datos no deben cruzar de consorcio: la
`nota` dice qué respuesta delataría el cruce. El comando que lo corre todavía no existe.
