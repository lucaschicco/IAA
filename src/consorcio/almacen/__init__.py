"""Persistencia.

- `postgres.py`: datos estructurados (documentos, páginas, personas, direcciones,
  eventos, unidades, montos, secciones, datos sensibles, llamadas al modelo).
- `grafo_neo4j.py`: grafo de contexto (documentos ↔ entidades ↔ temas), sin datos
  sensibles, pensado para navegar y después alimentar el buscador.
"""
