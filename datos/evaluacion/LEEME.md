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
