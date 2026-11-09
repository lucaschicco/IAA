"""Lo que el modelo devuelve al clasificar un mensaje.

Plano y sin uniones discriminadas: algunos proveedores no las soportan. No hay campo de consorcio,
usuario ni rol: eso sale de la identidad, y lo que el modelo agregue de más se descarta al validar."""
from __future__ import annotations

from typing import Literal, get_args

from pydantic import BaseModel, Field, field_validator

from ..ingesta.esquemas import _nulo_si_vacio

Intencion = Literal["reglamento", "expensas", "documentos", "reclamo", "derivar", "fuera_de_alcance"]
INTENCIONES: tuple[str, ...] = get_args(Intencion)


class SalidaRouter(BaseModel):
    intencion: Intencion
    pregunta_reescrita: str = Field("", description="El mensaje como pregunta autónoma, resuelto con el historial")
    que: Literal["resumen", "rubros", "gastos", "unidades"] | None = None
    periodo: str | None = Field(None, pattern=r"^\d{4}-\d{2}$", description="AAAA-MM")
    # Con tope de dígitos: `solicitudes.unidad` es varchar(20) y `liquidacion_unidades.uf` es un entero.
    # Una unidad imposible invalida la salida, y eso deriva el pedido entero a la administración.
    unidad: str | None = Field(None, pattern=r"^(\d{1,6}-[A-Za-z]|[Cc][Oo][Cc][Hh]-\d{1,6}|\d{1,6})$",
                               description="Piso-depto (5-D, COCH-16) o número de UF")
    propia: bool = Field(False, description="true si pregunta por su propia unidad")
    texto: str | None = Field(None, max_length=80, description="Filtro de gastos: rubro, proveedor o concepto")
    titulo: str | None = Field(None, max_length=120, description="Solo reclamos: resumen corto")
    detalle: str | None = Field(None, max_length=2000, description="Solo reclamos: descripción")

    _limpiar = field_validator("que", "periodo", "unidad", "texto", "titulo", "detalle", mode="before")(_nulo_si_vacio)
