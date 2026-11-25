"""Lo que el modelo devuelve al responder una consulta, en dos pasos: primero extrae citas textuales (que se
verifican en código) y después redacta la respuesta viendo solo las citas verificadas."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..ingesta.esquemas import _nulo_si_vacio

MAX_CITAS = 3


class CitaTextual(BaseModel):
    fuente: int = Field(description="Número de la fuente ([Fuente N]) de donde sale la cita")
    pagina: int | None = Field(None, description="Página marcada como === Página N === de donde sale la cita")
    cita: str = Field(
        description="Copia textual exacta de un fragmento contiguo del 'Texto de las páginas' (15 a 300 caracteres). "
        "Nunca del resumen, nunca parafraseada, nunca uniendo renglones de lugares distintos."
    )


class CitasExtraidas(BaseModel):
    # Con default: Haiku 5.5 a veces lo omite cuando sí trae citas (Manzanares, 25/11). Sin citas igual se deriva.
    puede_responder: bool = Field(True, description="true si el texto de las fuentes tiene algo que responda o aclare la pregunta")
    citas: list[CitaTextual] = Field(
        default_factory=list, description=f"De 1 a {MAX_CITAS} citas textuales; vacía si no se puede responder"
    )
    falta: str | None = Field(None, description="Si no se puede responder: qué faltaría, en una oración")

    _limpiar = field_validator("falta", mode="before")(_nulo_si_vacio)


class RespuestaRedactada(BaseModel):
    respuesta: str = Field(description="Respuesta en 1 a 4 oraciones, en español, solo con lo que dicen las citas")
