# Informe – Asistente para consorcios

Una sección por cada cosa que pide la consigna. Lo que dice "Falta" es lo que queda por hacer.

## 1. Problema y usuarios

Consultar la documentación de un consorcio es lento: el reglamento, las actas y la normativa están dispersos, y las respuestas se dan de memoria o tardan días.

Usuarios:
- Propietarios e inquilinos, con preguntas como: ¿puedo tener perro?, ¿quién paga una filtración?, ¿por qué aumentó la expensa?
- La administración, que recibe esas consultas además de cargar gastos y resolver reclamos.

Entrevistamos a un administrador. Lo que más le cuesta:
- la carga de facturas, que es la tarea más repetitiva;
- seguir los reclamos de los vecinos, que se pierden en WhatsApp.

Falta: decidir si apuntamos a consorcios chicos o a administraciones profesionales (los datos que tenemos son de un edificio grande).

## 2. Por qué hace falta un modelo de lenguaje

- Las actas cambian lo que dice el reglamento, así que hay que leer varios documentos juntos.
- Los vecinos preguntan con otras palabras que las del reglamento ("parrilla" vs. "artefactos de combustión").
- Los montos de expensas no los inventa el modelo: salen de una tabla.

## 3. Antecedentes

Mercado: hay productos como CONSO, Adminia y PropietarIA (ver planilla "Competidores" en el Drive). Ninguno responde citando el reglamento real del edificio.

Falta: completar la comparación y buscar algunos papers sobre el tema.

## 4. Datos

Tenemos, de un consorcio real:
- 4 actas de asamblea y 2 convocatorias
- reglamento de cocheras
- 8 liquidaciones de expensas (febrero a septiembre 2026)
- unas 30 facturas y comprobantes
- comunicados de la administración

Y normativa pública: modelo de reglamento, manual de prácticas consorciales, leyes 941 y 3254.

Falta:
- conseguir el reglamento de copropiedad del edificio;
- pasar las actas a texto y las liquidaciones a una tabla;
- sacar los datos personales.

## 5. Cómo funciona el sistema

Un asistente al que le escriben los vecinos y la administración. Según lo que llega, hace una de estas cosas:

- **Consultas de reglamento:** busca en los documentos y responde citando la fuente.
- **Consultas de expensas:** trae el dato real de la tabla y explica por qué varió.
- **Carga de facturas y documentos:** la administración sube una factura o una foto; el sistema extrae los datos y la administración los revisa antes de guardarlos.
- **Reclamos:** registra el reclamo, lo asocia a una unidad y permite consultar en qué estado está.
- **Proveedores:** sugiere a quién llamar según el rubro y compara presupuestos.

Las acciones importantes (guardar una factura, contactar a un proveedor) pasan por una bandeja donde la administración aprueba, edita o rechaza. Cuando el sistema no puede verificar una respuesta, deriva a la administración.

Las fuentes de datos que usa cada parte están en `docs/fuentes.md`.

Falta: diagrama, elección de modelos, y definir el orden en que construimos cada parte.

## 6. Búsqueda en reglamento y actas

Falta: todo (se completa al construirlo).

## 7. Consulta de expensas

Falta: todo (se completa al construirlo).

## 8. Evaluación

Idea: un conjunto de preguntas con su respuesta correcta, separado en una parte para desarrollar y otra para el test final. Comparamos el sistema contra alternativas más simples:
- búsqueda por palabras clave, contra nuestra búsqueda;
- un modelo sin acceso a los documentos, contra nuestro sistema.

Falta: escribir las preguntas y correr las pruebas.

## 9. Costos

Medir cuánto cuesta y cuánto tarda cada consulta, y compararlo con el tiempo que hoy le lleva a una persona.

Falta: medirlo.

## 10. Alcance y límites

No hace: asesoramiento legal, pagos ni cobranzas.

Falta: limitaciones y próximos pasos, al final.
