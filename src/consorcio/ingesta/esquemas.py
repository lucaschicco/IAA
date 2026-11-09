"""Esquemas (pydantic) de lo que extraemos de un documento.

Son el contrato entre el modelo y la base: el modelo devuelve exactamente esto
(salida estructurada) y `almacen/` lo persiste. Los datos de tipo sensible se
guardan completos en Postgres con la marca `sensible=true`.
"""
from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field, field_validator

TipoDocumento = Literal[
    "reglamento_copropiedad", "reglamento_interno", "acta_asamblea", "convocatoria",
    "liquidacion_expensas", "factura", "presupuesto", "contrato", "ley_o_normativa",
    "comunicado", "otro",
]
RolPersona = Literal[
    "propietario", "inquilino", "administrador", "escribano", "encargado", "consejo",
    "fiduciario", "apoderado", "proveedor", "otro",
]
TipoEvento = Literal[
    "fecha_documento", "escritura", "asamblea", "vencimiento", "inicio_vigencia",
    "fin_vigencia", "pago", "nacimiento", "inscripcion_registral", "otro",
]
TipoDireccion = Literal["inmueble_del_consorcio", "domicilio_persona", "sede_organizacion", "otro"]
TipoDatoSensible = Literal["dni", "cuit", "cuil", "telefono", "email", "fecha_nacimiento", "matricula", "otro"]


_VACIOS = {"", "null", "none", "<unknown>", "unknown", "desconocido", "n/a", "s/d"}


def _nulo_si_vacio(v):
    """Algunos modelos devuelven '<UNKNOWN>' o '' en vez de null."""
    if isinstance(v, str) and v.strip().lower() in _VACIOS:
        return None
    return v


def _otro_si_invalido(literal):
    """Los modelos abiertos (gpt-oss, Gemma) a veces inventan un valor fuera del enum
    ("vecino", "comprador"): antes de tirar el lote entero, lo mapeamos a "otro"."""
    validos = set(literal.__args__)

    def validar(v):
        if isinstance(v, str):
            v2 = v.strip().lower().replace(" ", "_")
            return v2 if v2 in validos else "otro"
        return v

    return validar


class Ubicable(BaseModel):
    pagina: int | None = Field(None, description="Página del PDF (1-indexada) donde aparece")
    fragmento: str | None = Field(
        None, description="Cita textual corta (<= 200 caracteres) que respalda el dato"
    )


class DatoSensible(BaseModel):
    tipo: TipoDatoSensible
    valor: str = Field(description="Valor tal como aparece; si está enmascarado (XX.XXX.XXX) copiarlo así")

    _tipo = field_validator("tipo", mode="before")(_otro_si_invalido(TipoDatoSensible))


class Persona(Ubicable):
    nombre: str = Field(description="Nombre completo tal como figura")
    rol: RolPersona = "otro"
    nacionalidad: str | None = None
    estado_civil: str | None = None
    domicilio: str | None = Field(None, description="Domicilio declarado, como texto")
    datos_sensibles: list[DatoSensible] = Field(default_factory=list)

    _limpiar = field_validator("nacionalidad", "estado_civil", "domicilio", mode="before")(_nulo_si_vacio)
    _rol = field_validator("rol", mode="before")(_otro_si_invalido(RolPersona))


class Organizacion(Ubicable):
    nombre: str
    tipo: str | None = Field(None, description="S.R.L., S.A., consorcio, fideicomiso, organismo público, etc.")
    rol: str | None = Field(None, description="Qué papel cumple en el documento")
    domicilio: str | None = None
    datos_sensibles: list[DatoSensible] = Field(default_factory=list)


class Direccion(Ubicable):
    texto: str = Field(description="Dirección completa como aparece")
    calle: str | None = None
    numero: str | None = None
    piso: str | None = None
    departamento: str | None = None
    localidad: str | None = None
    provincia: str | None = None
    tipo: TipoDireccion = "otro"

    _tipo = field_validator("tipo", mode="before")(_otro_si_invalido(TipoDireccion))


class Evento(Ubicable):
    fecha: str = Field(
        description="Fecha en ISO 8601 (AAAA-MM-DD). Si falta el día, AAAA-MM; si falta el mes, AAAA"
    )
    tipo: TipoEvento = "otro"
    descripcion: str = Field(description="Qué pasó o pasa en esa fecha")

    _tipo = field_validator("tipo", mode="before")(_otro_si_invalido(TipoEvento))


class Unidad(Ubicable):
    identificador: str = Field(description="Ej.: 'UF 12', '3° B', 'cochera 7'")
    piso: str | None = None
    destino: str | None = Field(None, description="vivienda, cochera, local, baulera...")
    superficie_m2: float | None = None
    porcentual: float | None = Field(None, description="Porcentaje sobre el total del edificio")


class Monto(Ubicable):
    valor: float = Field(description="Importe numérico. Si el texto dice 'el doble' o 'la mitad' sin importe, NO es un monto")
    moneda: str = Field("ARS", description="Código de moneda: ARS, USD, EUR. Nunca una descripción", max_length=10)
    concepto: str


class Seccion(BaseModel):
    """Una porción temática del documento: un artículo del reglamento, un punto
    del orden del día, un rubro de la liquidación. Es lo que va al grafo de
    contexto y lo que después se cita al responder."""

    titulo: str
    tema: str = Field(
        description="Tema en una o dos palabras: mascotas, expensas, asambleas, cocheras, "
        "administración, obras, ruidos, etc."
    )
    resumen: str = Field(description="Resumen fiel en 1 a 3 oraciones, sin inventar")
    pagina_inicio: int | None = None
    pagina_fin: int | None = None


class ExtraccionParcial(BaseModel):
    """Lo que el modelo devuelve por cada lote de páginas."""

    personas: list[Persona] = Field(default_factory=list)
    organizaciones: list[Organizacion] = Field(default_factory=list)
    direcciones: list[Direccion] = Field(default_factory=list)
    eventos: list[Evento] = Field(default_factory=list)
    unidades: list[Unidad] = Field(default_factory=list)
    montos: list[Monto] = Field(default_factory=list)
    secciones: list[Seccion] = Field(default_factory=list)

    @field_validator("eventos", mode="before")
    @classmethod
    def _sin_eventos_sin_fecha(cls, v):
        """gpt-oss manda eventos con `fecha: null`; se descartan en vez de tirar el lote entero."""
        if isinstance(v, list):
            return [e for e in v if not (isinstance(e, dict) and e.get("fecha") is None)]
        return v


class MetadatosDocumento(BaseModel):
    """Lo que el modelo devuelve una vez, mirando el inicio del documento."""

    tipo_documento: TipoDocumento = "otro"
    titulo: str = Field(description="Título corto y descriptivo")
    fecha_documento: str | None = Field(None, description="ISO 8601 si se puede determinar; null si no figura")
    consorcio: str | None = Field(None, description="Nombre o dirección del edificio al que refiere")
    resumen: str = Field(description="Resumen del documento en 3 a 5 oraciones")

    _limpiar = field_validator("fecha_documento", "consorcio", mode="before")(_nulo_si_vacio)
    _tipo = field_validator("tipo_documento", mode="before")(_otro_si_invalido(TipoDocumento))


class DocumentoExtraido(BaseModel):
    """Resultado final de la ingesta de un PDF: metadatos + entidades fusionadas."""

    metadatos: MetadatosDocumento
    personas: list[Persona] = Field(default_factory=list)
    organizaciones: list[Organizacion] = Field(default_factory=list)
    direcciones: list[Direccion] = Field(default_factory=list)
    eventos: list[Evento] = Field(default_factory=list)
    unidades: list[Unidad] = Field(default_factory=list)
    montos: list[Monto] = Field(default_factory=list)
    secciones: list[Seccion] = Field(default_factory=list)

    COLECCIONES: ClassVar[tuple[str, ...]] = (
        "personas", "organizaciones", "direcciones", "eventos", "unidades", "montos", "secciones",
    )

    def conteo(self) -> dict[str, int]:
        return {k: len(getattr(self, k)) for k in self.COLECCIONES}
