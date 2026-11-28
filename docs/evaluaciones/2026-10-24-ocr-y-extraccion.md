# Evaluación 24/10/2026: OCR y extracción con modelos reales

Primera corrida de punta a punta de la tool de ingesta con modelos reales. Objetivo: ver si el
pipeline funciona, comparar proveedores en fidelidad y costo, y juntar fallas para iterar el skill.
Todo se corrió dentro de Docker con `consorcio evaluar-ocr` y `consorcio ingestar`; los artefactos
quedaron en `datos/procesados/` (una carpeta por documento, páginas, proveedor y modelo) y las
corridas en MLflow (experimento `consorcio-ingesta`).

## 1. Transcripción de escaneado: contrato Cabrera, páginas 1 a 3

Referencia: `datos/documentos/contrato edificio cabrera.md` (transcripción revisada a mano, 866
palabras en esas 3 páginas). Escaneo de buena calidad, tipografía de escritura notarial con sellos.
El clasificador lo manda a Tesseract (confianza 92); acá forzamos cada método para comparar.

| Método | Skill | Similitud de caracteres | Error de palabras | Tokens entrada | Tokens salida | Segundos | USD (3 págs.) |
|---|---|---|---|---|---|---|---|
| Tesseract (local) | — | 0,977 | 5,8 % | 0 | 0 | ~2 | 0 |
| Claude Haiku 5.5 (visión) | 0.1.0 | **0,987** | **1,0 %** | 15.420 | 2.440 | 10,3 | 0,0028 |
| Claude Sonnet 5.5 (visión) | 0.1.0 | 0,986 | **1,0 %** | 15.420 | 2.427 | 17,2 | 0,0551 |
| Gemini 2.5 Flash (visión) | 0.1.0 | 0,983 | 4,6 % | 1.494 | 2.952 | 21,0 | 0,0078 |
| Gemini 2.5 Flash (visión) | 0.1.1 | 0,983 | 3,5 % | 1.629 | 4.515 | 27,2 | 0,0118 |
| Gemma 4 31B vía Ollama cloud (visión) | 0.1.0 | 0,978 | 5,3 % | 1.590 | 1.601 | 19,4 | sin precio |

Lecturas:
- En un escaneo bueno, **Tesseract queda a 5 puntos de error de los mejores modelos sin gastar
  nada**. Para 42 páginas: Haiku costaría ~0,04 USD, Sonnet ~0,77 USD, Tesseract 0.
- **Haiku rinde igual que Sonnet** en transcripción a un vigésimo del costo. Para OCR no hay razón
  para usar Sonnet.
- Casi todo el error extra de Gemini y Gemma es el mismo: **dejan partidas las palabras cortadas
  con guion al final de renglón** ("administra ción", "em prendimientos"). Claude las une solo.
  El skill 0.1.1 agrega la instrucción explícita: Gemini bajó de 4,6 % a 3,5 % pero sigue
  partiendo palabras. Candidato a resolverse en código (unir en el posprocesado), no en el prompt,
  que es la pregunta que deja la práctica ("¿qué parte del skill convendría convertir en código?").
- **Gemma 4 cambió dígitos de un CUIL y confundió CUIL con CUIT**: para datos sensibles es
  inaceptable aunque la similitud global sea alta. La métrica de caracteres no lo captura; hace
  falta una métrica específica de números e identificadores.
- Los tokens de entrada de Anthropic (~5.000 por página) son 10 veces los de Gemini: cada
  proveedor cuenta las imágenes distinto, así que **comparar costo en USD, no en tokens**.
- Ollama cloud devolvió `429 timed out waiting for a concurrent request slot` en la segunda
  corrida: el tier gratuito no admite corridas en paralelo.

## 2. Ingesta completa: acta de asamblea 25/07/2024 (2 páginas, manuscrita)

El acta está **escrita a mano** en el libro de actas (ver `paginas_png/` en la carpeta de salida).
Tesseract da confianza 41, el clasificador manda a visión. Sin referencia a mano; se comparó entre
modelos y contra la imagen.

| Modelo | Llamadas | Tokens ent./sal. | Segundos | USD | Personas | Eventos | Unidades | Secciones | Observaciones |
|---|---|---|---|---|---|---|---|---|---|
| Claude Haiku 5.5 | 4 | 17.660 / 7.853 | 36 | 0,0057 | 9 | 4 | 9 | 6 | Leyó "Ricciolí" (es Miccioli); duplicó "Sr. De Marco" y "Ernesto De Marco" |
| Claude Sonnet 5.5 | 4 | 18.046 / 6.690 | 44 | 0,103 | 9 | 5 | 0 | 6 | Nombres correctos; no extrajo unidades |
| Gemini 2.5 Flash | 4 | 3.774 / 13.313 | 80 | 0,0344 | 9 | 6 | 13 | 6 | "De Marca" (es De Marco); el más caro en tokens de salida (razonamiento) |
| Gemma 4 31B (Ollama) | — | — | — | — | — | — | — | — | No terminó: límite de concurrencia del tier gratuito |

Los tres coinciden en tipo (`acta_asamblea`), fecha (2024-07-25), consorcio y los 6 puntos del orden
del día, con resúmenes fieles. Discrepan en apellidos leídos de la letra manuscrita y en cuántas
unidades funcionales listan. **Esa discrepancia es el argumento concreto para la bandeja de
revisión humana**: el sistema puede marcar como "a confirmar" los nombres propios en los que dos
corridas no coinciden.

## 3. Ingesta completa: reglamento interno de cocheras (2 páginas, con capa de texto)

| Modelo | Llamadas | USD | Secciones | Observaciones |
|---|---|---|---|---|
| Claude Haiku 5.5 | 2 | 0,0025 | 13 (= 13 artículos) | Devolvió `<UNKNOWN>` como fecha (se agregó un validador que lo vuelve null); inventó un monto con valor 0 a partir de "el doble de expensa" |
| Gemini 2.5 Flash | 2 | 0,0108 | 13 (= 13 artículos) | Primer intento falló: puso "expensa de cochera" en `moneda` (columna de 10 caracteres). Se ensanchó la columna y se endureció la descripción del campo; segundo intento limpio |

Los dos extrajeron un artículo por sección con tema y resumen correctos, la dirección del inmueble y
el consorcio como organización. Esto ya alcanza para empezar el buscador con cita.

## 4. Costo de referencia

Con Haiku, ingestar todo `datos/documentos/` (reglamento de copropiedad 60 págs. escaneado, 4 actas,
reglamento de cocheras, 2 convocatorias, contrato Cabrera 42 págs.) costaría del orden de 0,5 USD
si todo fuera por visión, y menos si el clasificador manda el contrato Cabrera a Tesseract.

## 5. Decisiones y pendientes que salen de acá

- **Haiku 5.5 como modelo por defecto para OCR y estructuración**; Sonnet no mejora la
  transcripción y en el acta fue 18 veces más caro.
- Mantener Tesseract para escaneos buenos (el clasificador ya lo hace) y medir en el reglamento de
  copropiedad completo si el umbral 75 está bien puesto.
- Agregar al evaluador una métrica de **números e identificadores** (DNI, CUIT, fechas, importes)
  además de similitud y error de palabras.
- Mejorar la deduplicación de personas ("Sr. De Marco" vs "Ernesto De Marco") y marcar para
  revisión los nombres en que los modelos no coinciden.
- Unir palabras partidas en posprocesado si se usa Gemini.
