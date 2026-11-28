# Expensas

Las 8 liquidaciones mensuales de Mercedes 274 (Drive: Docu/expensas). El nombre del archivo es la
fecha de emisión; cada una liquida el mes anterior (`2026-03-12-...` es Febrero 2026).

Se ingestan con `consorcio ingestar` como cualquier PDF: el clasificador las manda al camino "texto",
el modelo extrae encabezado, rubros y gastos (sección `liquidacion` del skill) y el prorrateo por unidad
(~140 filas) lo parsea una expresión regular. Quedan en Postgres (`liquidaciones`, `liquidacion_rubros`,
`liquidacion_gastos`, `liquidacion_unidades`) y se consultan con `consorcio expensas` por SQL.
