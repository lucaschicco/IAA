# Almacén de PDF

Los PDF se guardan en un bucket (MinIO en local, compatible con S3). En Postgres
queda el registro: de qué consorcio es, quién lo subió y su huella (sha256).

## Si se carga dos veces el mismo PDF

No se guarda de nuevo. Se reconoce por el contenido, no por el nombre: el mismo
acta subida como `acta.pdf` y como `acta (1).pdf` es un duplicado. La función
devuelve el registro existente con `duplicado = True`, así la ingesta sabe que
no tiene que volver a procesarlo. El mismo PDF en dos consorcios distintos sí se
guarda dos veces, porque son documentos de edificios distintos.

## Cómo se usa desde la ingesta

```python
from almacen.pdfs import cliente_bucket, guardar_pdf, leer_pdf

r = guardar_pdf(con, cliente_bucket(), consorcio_id, "acta.pdf", contenido, usuario_id)
if not r.duplicado:
    procesar(r.id)   # OCR, extracción, etc.
```

## Funciones HTTP

- `POST /archivos`: sube un PDF (solo la administración del consorcio).
- `GET /archivos?consorcio_id=1`: lista los PDF del consorcio (administración y consejo).
