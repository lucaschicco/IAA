"""Las funcionalidades del agente. `construir_grafo` agrega un nodo por cada una y el CRM las lista
con sus ceros: agregar una funcionalidad es agregarla acá."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import nodos


@dataclass(frozen=True)
class Funcionalidad:
    nombre: str
    intencion: str  # la del router que la dispara
    descripcion: str
    usa_modelo: bool  # además del router
    requiere_aprobacion: bool
    nodo: Callable[[nodos.EstadoAgente], dict]


FUNCIONALIDADES: dict[str, Funcionalidad] = {
    f.nombre: f
    for f in (
        Funcionalidad("reglamento", "reglamento", "Responde con los documentos del consorcio, con cita verificada",
                      True, False, nodos.nodo_reglamento),
        Funcionalidad("expensas", "expensas", "Importes de las liquidaciones, por SQL", False, False, nodos.nodo_expensas),
        Funcionalidad("documentos", "documentos", "Lista los documentos cargados", False, False, nodos.nodo_documentos),
        Funcionalidad("reclamo", "reclamo", "Deja un reclamo pendiente de aprobación", False, True, nodos.nodo_reclamo),
        Funcionalidad("derivar", "derivar", "Pasa el pedido a la administración", False, False, nodos.nodo_derivar),
    )
}
