# Fuentes de datos del sistema

Basado en la planilla "Archivos y fuentes que usará el agente" del Drive.

**Cómo lo lee el sistema:**
- **Buscador:** encuentra el párrafo relevante dentro de un documento.
- **Tabla:** consulta directa a una base de datos, que trae el dato exacto.
- **Extracción:** un script con LLM lee un PDF una vez y lo pasa a tabla.

## Datos del edificio

| Fuente | Qué contiene | Origen | Cómo lo lee el sistema | Quién lo carga |
|---|---|---|---|---|
| Reglamentos, actas y asambleas | Reglamento del edificio, actas, convocatorias | Real (resúmenes de asamblea pueden ser sintéticos) | Buscador | Administración |
| Normativa general | Código Civil y Comercial (propiedad horizontal), leyes de CABA | Pública | Buscador | Grupo (carga inicial) |
| Unidades | Depto, piso, porcentual, m², cocheras | Inventado, basado en edificio real | Tabla | Administración |
| Propietarios | Qué unidad tiene cada uno, desde cuándo, contacto | Inventado | Tabla | Administración |
| Inquilinos | Unidad, contacto, fechas de contrato | Inventado | Tabla | Administración o propietario |
| Personal del edificio | Encargado: contacto, horarios, tareas | Inventado | Tabla | Administración |
| Espacios comunes | SUM, terraza: reglas y reservas | Inventado | Tabla (agenda) + buscador (reglas) | Administración |

## Dinero

| Fuente | Qué contiene | Origen | Cómo lo lee el sistema | Quién lo carga |
|---|---|---|---|---|
| Liquidaciones de expensas | Período, rubros, montos, gastos particulares | Real anonimizado | Extracción → tabla | Administración |
| Pagos y deuda por unidad | Qué unidad pagó, cuándo, saldo | Inventado | Tabla | Administración |
| Facturas y certificados | Proveedor, fecha, monto, qué trabajo cubre | Real anonimizado | Extracción → tabla | Administración o proveedor |
| Presupuestos | Proveedor, trabajo, monto, validez | Real + inventado | Extracción → tabla | Proveedor o administración |

Los pagos y la deuda por unidad son un dato sensible: el sistema no se los muestra a otros vecinos.

## Proveedores y mantenimiento

| Fuente | Qué contiene | Origen | Cómo lo lee el sistema | Quién lo carga |
|---|---|---|---|---|
| Proveedores | Nombre, rubro, contacto, si es habitual | Inventado | Tabla | Administración |
| Contratos con proveedores | Vigencia, cláusulas de ajuste | Inventado | Extracción → tabla + buscador | Administración |
| Obligaciones técnicas | Tanques, matafuegos, ascensores: periodicidad y vencimientos | Inventado, basado en normativa CABA | Tabla | Administración |

## Lo que genera el propio sistema

| Fuente | Qué contiene | Para qué sirve |
|---|---|---|
| Reclamos y casos | Descripción, unidad, estado, proveedor, resolución | Seguimiento de reclamos durante días |
| Mensajes entrantes | Reclamos, mails de proveedores, documentos subidos | Disparan el trabajo del sistema |
| Acciones propuestas | Qué propuso el sistema y si la administración lo aprobó, editó o rechazó | Medir la revisión humana |
| Precedentes | Cómo se resolvió cada caso cerrado | Consultar casos parecidos en el futuro |
| Registro de uso | Llamadas al modelo, costo y tiempo | Sección de costos del informe |
| Preguntas de prueba | Casos con su respuesta correcta | Evaluar el sistema |
