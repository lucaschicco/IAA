"""Lo que el modelo devuelve al responder una consulta."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..ingesta.esquemas import _nulo_si_vacio


class RespuestaConCita(BaseModel):
    puede_responder: bool = Field(
        description="true solo si el texto de las fuentes alcanza para responder con seguridad"
    )
    respuesta: str = Field(description="Respuesta en 1 a 4 oraciones, en español, sin inventar")
    fuente: int | None = Field(None, description="Número de la fuente usada ([Fuente N]); null si no se puede responder")
    pagina: int | None = Field(None, description="Página marcada como === Página N === de donde sale la cita")
    cita: str | None = Field(
        None,
        description="Copia textual exacta de un fragmento del 'Texto de las páginas' (15 a 300 caracteres) "
        "que respalda la respuesta. Nunca del resumen, nunca parafraseada.",
    )

    _limpiar = field_validator("cita", mode="before")(_nulo_si_vacio)
