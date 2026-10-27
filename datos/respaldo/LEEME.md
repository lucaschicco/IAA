# Respaldo

Volcados de Postgres (`make respaldar`) para pasar la base ya cargada a otro integrante sin
reingestar: `make restaurar` carga el más reciente (o `DUMP=...`). Incluyen datos personales del
consorcio: el repo es privado por eso mismo. Neo4j no se respalda (solo lo usa `consorcio grafo`;
se rearma reingestando).
