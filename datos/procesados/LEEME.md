# Procesados

Salida de la ingesta (`consorcio ingestar`). Una carpeta por documento, `<slug>-<hash8>/`:

- `texto.md`: transcripción página por página, con el método usado (texto / tesseract / vision) y la confianza.
- `extraccion.json`: metadatos, entidades extraídas, decisión del clasificador, llamadas al modelo y costo.
- `paginas_png/`: páginas rasterizadas de los escaneados (no se commitea).

Contiene datos personales igual que el resto de `datos/`: el repo sigue siendo privado.
