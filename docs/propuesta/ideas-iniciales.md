**App para Consorcios** 

**1\. Responder consultas sobre el reglamento del consorcio y actas (incluye cita)**

Responde preguntas sobre el reglamento y las actas citando la fuente exacta.

* **Por qué LLM:** la pregunta llega parafraseada de diversas formas y la respuesta puede combinar más de un documento. Un método determinístico que busque palabras claves no resuelve el problema de los sinínimos ni detecta que un acta deroga tácitamente un artículo del reglamento.  
* **Datos:** Reglamento y actas de uno o más consorcios. **CONSEGUIR**  
* **Cómo medimos:** recall@k (métrica que se usa para evaluar sistemas de recuperación de información) contra fragmentos anotados, exactitud de respuesta, tasa de citación correcta. **Hipótesis:** el sistema con RAG y verificación supera al baseline en ambas métricas.  
* **Mayor riesgo:** citar una norma desactualizada o que haya sido derogada.  
* **Plan B**: utilización de un umbral de confianza antes de dar la respuesta, verificación programática de que la cita existe en lo recuperado. Si el umbral no se supera, mostrar el fragmento del reglamento o escalar con un humano.  
* **Fuera de alcance:** no da opinión legal más allá de citar la norma, no resuelve casos ambiguos  
* **Costo:** tokens de entrada y salida por consulta, multiplicados por volumen mensual estimado. Costo fijo de indexación inicial del corpus y sus posteriores modificaciones.

**2\. Vencimientos y cláusulas de contratos con proveedores**

Revisa periódicamente los contratos vigentes y avisa antes de que venza uno o se active una cláusula de ajuste de precio (cita la cláusula).

* **Por qué LLM:** cada contrato redacta la misma cláusula con estructura de oración distinta. Un detector por reglas (regex) se rompe con cada proveedor o estudio jurídico nuevo.  
* **Datos:** contratos con proveedores. Contratos sintéticos. **CONSEGUIR**  
* **Cómo medimos:** recall de vencimientos y cláusulas detectados (prioritario), precisión de fecha y tipo extraído, contra contratos anotados a mano. **Baseline:** regex sobre palabras clave y patrones de fecha. **Hipótesis:** el LLM detecta más casos que el regex, especialmente los redactados de forma no estándar.  
* **Mayor riesgo:** no detectar un vencimiento (falso negativo) es más grave que una falsa alarma.  
* **Plan B:** alertas con margen amplio de anticipación. Revisión humana de la primera pasada sobre cada contrato antes de confiar en el sistema.  
* **Fuera de alcance:** no negocia ni renueva el contrato, no gestiona el pago al proveedor, no decide si conviene renovar.  
* **Costo:** procesamiento por contrato y re-escaneo periódico (mensual).  
     
  **3\. Resumir la actividad semanal para el administrador**

Reporte automático semanal: qué se consultó más, qué quedó escalado, qué preguntas no se pudieron responder por falta de información.

* **Por qué LLM:** agrupar consultas por sentido, no por coincidencia de texto. Un conteo determinístico podría tratar dos preguntas que apunten a lo mismo como diferentes por la forma en la que están escritas.  
* **Datos:** consume la información que genera las demás funciones  
* **Cómo medimos:** calidad del agrupamiento temático contra anotación humana, precisión de aquellas consultas que no encontraron respuesta por falta de información. **Baseline:** agrupamiento por texto exacto o palabra más frecuente (determinístico). **Hipótesis:** el LLM agrupa mejor y detecta preguntas sin respuesta.  
* **Mayor riesgo:** que el resumen invente un patrón que no está en los datos.  
* **Plan B**: cada afirmación del resumen se ancla a IDs de trazas reales (verificable), Marcelo revisa antes de que el resumen se considere definitivo.  
* **Fuera de alcance:** no envía comunicados a los vecinos  
* **Costo:** frecuencia baja (semanal), tokens proporcionales al volumen de interacciones acumuladas esa semana.

**4\. Derivar a un humano con el contexto armado**

Decide cuándo el sistema no debe responder solo, y arma el contexto para que la persona que resuelve el caso ya tenga un contexto previo.

* **Por qué LLM, con salvedad:** no necesita etiquetas previas para arrancar, maneja categorías abiertas de riesgo, y redacta el contexto de la derivación.  
* **Datos:** el mismo corpus de la función 1 (responder preguntas del reglamento) para resolver intención, más un conjunto de consultas etiquetadas "responder vs. escalar" para entrenar el baseline y evaluar.  
* **Cómo medimos:** precisión y recall de "responder vs. escalar", con foco en el error caro (responder cuando correspondía escalar). **Baseline**: clasificador clásico . **Hipótesis**: el LLM es al menos comparable sin requerir el conjunto etiquetado previo que el clasificador sí necesita.  
* **Mayor riesgo :** responder algo que debía escalarse (deuda, conflicto, tema legal). **Plan B**: lista cerrada de temas de escalado obligatorio que fuerzan derivación sin importar el score de confianza.  
* **Fuera de alcance:** no decide el resultado del caso escalado, no reemplaza el juicio del administrador o del Consejo.  
* **Costo:**

**5\. Responder consultas de expensas por unidad**

Responde cuánto tiene que pagar una unidad y por qué varió, trayendo el dato real.

* **Por qué LLM:** Porque resuelve a qué unidad y período se refiere la consulta, y reconoce que dos rubros con nombres distintos en dos meses pueden ser el mismo rubro  
* **Datos:** liquidaciones reales anonimizadas de uno o más consorcios más el corpus de actas de la función 1 para atribuir causas. **CONSEGUIR**  
* **Cómo medimos:** exactitud de la atribución causal contra anotación humana (los montos se verifican al 100% aparte, por venir de la herramienta). Baseline: *diff* entre liquidaciones que solo compara rubros con nombre idéntico letra por letra. Hipótesis: el LLM atribuye correctamente incluso cuando el rubro cambió de nombre entre meses.  
* **Mayor riesgo:** inventar o calcular mal un monto . **Plan B:** el LLM nunca genera un número, siempre lo trae la herramienta. Se verifica que el monto citado coincide exactamente con el dato de la tabla antes de responder.  
* **Fuera de alcance:** no gestiona pagos ni cobranzas, no autoriza planes de pago en cuotas ni descuentos.  
* **Costo:** por consulta, similar a la función 1 más una llamada a la herramienta de datos estructurados donde se guardan los datos de las expensas.

**6\. Asistente de reservas de espacios comunes**

Interpreta un pedido de reserva en lenguaje natural y propone opciones que cumplen disponibilidad y reglamento vigente.

* **Por qué LLM:** Se utiliza una LLM para interpretar un pedido con fecha difusa y restricciones implícitas, y cruzarlo contra reglas dispersas que pueden haber cambiado por acta.  
* **Datos:** mismo corpus de reglamento/actas que la función 1, más una agenda de espacios comunes estructurada **ARMAR/CONSEGUIR**  
* **Cómo medimos:** tasa de propuestas que cumplen el reglamento vigente, tasa de solicitudes válidas rechazadas de más. **Baseline**: formulario con las reglas cargadas una sola vez al inicio, sin enterarse de cambios posteriores. **Hipótesis**: el LLM sigue cumpliendo el reglamento incluso después de que un acta lo modifique, y el formulario no.  
* **Mayor riesgo y plan B:** proponer una reserva que en realidad viola una regla ya modificada. **Plan B**: nunca confirma la reserva de forma automática. Si tiene que hacer alguna excepción, escala.  
* **Fuera de alcance:**  no resuelve conflictos entre dos vecinos que piden el mismo día, no gestiona pagos por uso del espacio.  
* **Costo:**

 

