# Respaldo

Volcados de Postgres (`make respaldar`) para pasar la base ya cargada a otro integrante sin
reingestar: `make restaurar` carga el más reciente (o `DUMP=...`). Incluyen datos personales del
consorcio: por eso los `.sql` no van al repo y se comparten por fuera.
