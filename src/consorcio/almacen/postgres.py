"""Modelo relacional (SQLAlchemy 2.0) y guardado de un documento extraído.

Regla de datos sensibles: DNI, CUIT/CUIL, teléfonos, emails y fechas de nacimiento
van a `datos_sensibles` con `sensible=true`. El resto de las tablas no los contiene,
así `enmascarado()` puede servir para generar la muestra sanitizada del repo.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..config import config
from ..ingesta.esquemas import DocumentoExtraido
from ..texto import consorcio_de_chat_id, id_documento

log = logging.getLogger("consorcio.postgres")

_engine = None


def engine():
    """Con tope de conexión: el default de psycopg son 130 s, y el libro de gastos y el tope no pueden
    colgar una respuesta o una corrida solo de disco si Postgres no está."""
    global _engine
    if _engine is None:
        _engine = create_engine(config.postgres_url, pool_pre_ping=True, connect_args={"connect_timeout": 5})
    return _engine


class Base(DeclarativeBase):
    pass


def _ahora() -> datetime:
    return datetime.now(UTC)


def exigir_consorcio(consorcio_id: str | None) -> str:
    """Toda operación sobre datos de un consorcio lo recibe explícito: acá no hay valor por defecto.
    Solo la CLI y la evaluación resuelven `CONSORCIO_POR_DEFECTO`, y lo hacen a la vista."""
    if not consorcio_id:
        raise ValueError("falta consorcio_id: hay que indicar sobre qué consorcio se opera")
    return consorcio_id


class Administracion(Base):
    """La empresa o persona que administra uno o varios consorcios (0011). No da permisos, que salen de
    `membresias`: es solo una dimensión de métricas y de agrupación visual."""

    __tablename__ = "administraciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class Consorcio(Base):
    """Una base por consorcio: todo lo que se ingesta y se consulta lleva `consorcio_id`.
    Por ahora cada número/grupo de Telegram tiene el suyo (`chat-<id>`); la tabla `chats` permite
    después asignar varios números a un mismo consorcio. `administracion_id` NULL: creado por Telegram o
    por terminal sin Administración; el CRM lo muestra en "Sin administración"."""

    __tablename__ = "consorcios"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # slug: mercedes-274, chat-123
    nombre: Mapped[str] = mapped_column(Text)
    direccion: Mapped[str | None] = mapped_column(Text)
    administracion_id: Mapped[int | None] = mapped_column(ForeignKey("administraciones.id"), index=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class Chat(Base):
    """Qué consorcio usa cada chat de Telegram (número o grupo)."""

    __tablename__ = "chats"
    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    descripcion: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class Documento(Base):
    __tablename__ = "documentos"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)  # <consorcio>:<hash del archivo>
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"), index=True)
    nombre_archivo: Mapped[str] = mapped_column(String(300))
    ruta: Mapped[str] = mapped_column(Text)
    tipo_documento: Mapped[str] = mapped_column(String(50))
    titulo: Mapped[str] = mapped_column(Text)
    fecha_documento: Mapped[str | None] = mapped_column(Text)  # tal como la devolvió el modelo
    consorcio: Mapped[str | None] = mapped_column(Text)  # nombre del consorcio tal como lo leyó el modelo
    resumen: Mapped[str] = mapped_column(Text)
    total_paginas: Mapped[int] = mapped_column(Integer)
    metodo_extraccion: Mapped[str] = mapped_column(String(20))
    confianza_clasificador: Mapped[float] = mapped_column(Float)
    clasificacion: Mapped[dict] = mapped_column(JSON)
    costo: Mapped[dict] = mapped_column(JSON)
    segundos_total: Mapped[float] = mapped_column(Float)
    # extraido → (futuro) pendiente_revision → aprobado / rechazado
    estado: Mapped[str] = mapped_column(String(30), default="extraido")
    # False: el documento queda guardado pero el buscador de consultas no lo usa
    # (p. ej. el contrato Cabrera, que es de otro edificio y se ingestó para evaluar OCR)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)
    sha256: Mapped[str | None] = mapped_column(String(64))  # del archivo; NULL en documentos históricos
    crudo_clave: Mapped[str | None] = mapped_column(Text)  # clave del PDF en el bucket; NULL si no se subió
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)

    paginas: Mapped[list[Pagina]] = relationship(cascade="all, delete-orphan")
    personas: Mapped[list[Persona]] = relationship(cascade="all, delete-orphan")
    organizaciones: Mapped[list[Organizacion]] = relationship(cascade="all, delete-orphan")
    direcciones: Mapped[list[Direccion]] = relationship(cascade="all, delete-orphan")
    eventos: Mapped[list[Evento]] = relationship(cascade="all, delete-orphan")
    unidades: Mapped[list[Unidad]] = relationship(cascade="all, delete-orphan")
    montos: Mapped[list[Monto]] = relationship(cascade="all, delete-orphan")
    secciones: Mapped[list[Seccion]] = relationship(cascade="all, delete-orphan")
    datos_sensibles: Mapped[list[DatoSensible]] = relationship(cascade="all, delete-orphan")
    llamadas: Mapped[list[LlamadaModelo]] = relationship(cascade="all, delete-orphan")
    liquidacion: Mapped[Liquidacion | None] = relationship(cascade="all, delete-orphan")


class Pagina(Base):
    __tablename__ = "paginas"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    numero: Mapped[int] = mapped_column(Integer)
    metodo: Mapped[str] = mapped_column(String(20))
    confianza: Mapped[float | None] = mapped_column(Float)
    chars: Mapped[int] = mapped_column(Integer)
    texto: Mapped[str] = mapped_column(Text)


class Persona(Base):
    __tablename__ = "personas"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    nombre: Mapped[str] = mapped_column(Text)
    rol: Mapped[str] = mapped_column(String(30))
    nacionalidad: Mapped[str | None] = mapped_column(String(60))
    estado_civil: Mapped[str | None] = mapped_column(String(60))
    domicilio: Mapped[str | None] = mapped_column(Text)
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)
    datos_sensibles: Mapped[list[DatoSensible]] = relationship(cascade="all, delete-orphan")


class Organizacion(Base):
    __tablename__ = "organizaciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    nombre: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str | None] = mapped_column(String(80))
    rol: Mapped[str | None] = mapped_column(Text)
    domicilio: Mapped[str | None] = mapped_column(Text)
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)
    datos_sensibles: Mapped[list[DatoSensible]] = relationship(cascade="all, delete-orphan")


class Direccion(Base):
    __tablename__ = "direcciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    texto: Mapped[str] = mapped_column(Text)
    calle: Mapped[str | None] = mapped_column(Text)
    numero: Mapped[str | None] = mapped_column(String(80))
    piso: Mapped[str | None] = mapped_column(String(80))
    departamento: Mapped[str | None] = mapped_column(String(80))
    localidad: Mapped[str | None] = mapped_column(String(120))
    provincia: Mapped[str | None] = mapped_column(String(120))
    tipo: Mapped[str] = mapped_column(String(40))
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)


class Evento(Base):
    __tablename__ = "eventos"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    fecha: Mapped[str] = mapped_column(String(40))
    tipo: Mapped[str] = mapped_column(String(40))
    descripcion: Mapped[str] = mapped_column(Text)
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)


class Unidad(Base):
    __tablename__ = "unidades"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    identificador: Mapped[str] = mapped_column(String(80))
    piso: Mapped[str | None] = mapped_column(String(80))
    destino: Mapped[str | None] = mapped_column(String(60))
    superficie_m2: Mapped[float | None] = mapped_column(Float)
    porcentual: Mapped[float | None] = mapped_column(Float)
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)


class Monto(Base):
    __tablename__ = "montos"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    valor: Mapped[float] = mapped_column(Float)
    moneda: Mapped[str] = mapped_column(String(40))
    concepto: Mapped[str] = mapped_column(Text)
    pagina: Mapped[int | None] = mapped_column(Integer)
    fragmento: Mapped[str | None] = mapped_column(Text)


class Seccion(Base):
    __tablename__ = "secciones"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    titulo: Mapped[str] = mapped_column(Text)
    tema: Mapped[str] = mapped_column(String(80))
    resumen: Mapped[str] = mapped_column(Text)
    pagina_inicio: Mapped[int | None] = mapped_column(Integer)
    pagina_fin: Mapped[int | None] = mapped_column(Integer)


class DatoSensible(Base):
    __tablename__ = "datos_sensibles"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    persona_id: Mapped[int | None] = mapped_column(ForeignKey("personas.id"))
    organizacion_id: Mapped[int | None] = mapped_column(ForeignKey("organizaciones.id"))
    tipo: Mapped[str] = mapped_column(String(30))
    valor: Mapped[str] = mapped_column(Text)
    sensible: Mapped[bool] = mapped_column(Boolean, default=True)
    pagina: Mapped[int | None] = mapped_column(Integer)


class LlamadaModelo(Base):
    __tablename__ = "llamadas_modelo"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"))
    nodo: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(80))
    tokens_entrada: Mapped[int] = mapped_column(Integer)
    tokens_salida: Mapped[int] = mapped_column(Integer)
    segundos: Mapped[float] = mapped_column(Float)
    costo_usd: Mapped[float | None] = mapped_column(Float)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


# ---- Liquidaciones de expensas (ver ingesta/liquidacion.py). Los importes se consultan por SQL,
# nunca los genera el modelo. `liquidacion_unidades.propietario` es un nombre real: dato personal,
# se trata como sensible al generar la muestra sanitizada.


class Liquidacion(Base):
    __tablename__ = "liquidaciones"
    documento_id: Mapped[str] = mapped_column(ForeignKey("documentos.id"), primary_key=True)
    periodo: Mapped[str] = mapped_column(Text)  # AAAA-MM; si el modelo no lo normalizó, lo que devolvió
    primer_vencimiento: Mapped[str | None] = mapped_column(Text)
    segundo_vencimiento: Mapped[str | None] = mapped_column(Text)
    consorcio: Mapped[str | None] = mapped_column(Text)
    administracion: Mapped[str | None] = mapped_column(Text)
    total_gastos: Mapped[float | None] = mapped_column(Float)
    filas_prorrateo: Mapped[int] = mapped_column(Integer, default=0)
    filas_no_parseadas: Mapped[int] = mapped_column(Integer, default=0)
    rubros: Mapped[list[LiquidacionRubro]] = relationship(cascade="all, delete-orphan")
    gastos: Mapped[list[LiquidacionGasto]] = relationship(cascade="all, delete-orphan")
    unidades: Mapped[list[LiquidacionUnidad]] = relationship(cascade="all, delete-orphan")


class LiquidacionRubro(Base):
    __tablename__ = "liquidacion_rubros"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("liquidaciones.documento_id"))
    numero: Mapped[int | None] = mapped_column(Integer)
    nombre: Mapped[str] = mapped_column(Text)
    total: Mapped[float | None] = mapped_column(Float)
    porcentaje: Mapped[float | None] = mapped_column(Float)


class LiquidacionGasto(Base):
    __tablename__ = "liquidacion_gastos"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("liquidaciones.documento_id"))
    rubro: Mapped[str] = mapped_column(Text)
    proveedor: Mapped[str | None] = mapped_column(Text)
    concepto: Mapped[str] = mapped_column(Text)
    importe: Mapped[float] = mapped_column(Float)
    fecha: Mapped[str | None] = mapped_column(String(40))
    comprobante: Mapped[str | None] = mapped_column(String(80))
    pagina: Mapped[int | None] = mapped_column(Integer)


class LiquidacionUnidad(Base):
    __tablename__ = "liquidacion_unidades"
    id: Mapped[int] = mapped_column(primary_key=True)
    documento_id: Mapped[str] = mapped_column(ForeignKey("liquidaciones.documento_id"))
    uf: Mapped[int] = mapped_column(Integer)
    piso_depto: Mapped[str] = mapped_column(String(40))
    propietario: Mapped[str] = mapped_column(Text)
    porcentual: Mapped[float] = mapped_column(Float)
    expensas_ordinarias: Mapped[float | None] = mapped_column(Float)
    expensa_extraordinaria: Mapped[float | None] = mapped_column(Float)
    prevision_gastos: Mapped[float | None] = mapped_column(Float)
    total_expensa: Mapped[float | None] = mapped_column(Float)
    gasto_particular: Mapped[float | None] = mapped_column(Float)
    redondeo: Mapped[float | None] = mapped_column(Float)
    a_pagar: Mapped[float | None] = mapped_column(Float)
    saldo_anterior: Mapped[float | None] = mapped_column(Float)
    importes_previos: Mapped[list] = mapped_column(JSON, default=list)
    otros_conceptos: Mapped[list] = mapped_column(JSON, default=list)  # pares porcentaje/importe extra
    pagina: Mapped[int | None] = mapped_column(Integer)


class Consulta(Base):
    """Cada pregunta respondida por `consorcio preguntar`: qué se contestó, con qué cita,
    si la cita se verificó y cuánto costó. Alimenta la sección de costos y la evaluación."""

    __tablename__ = "consultas"
    id: Mapped[int] = mapped_column(primary_key=True)
    pregunta: Mapped[str] = mapped_column(Text)
    respuesta: Mapped[str | None] = mapped_column(Text)
    cita: Mapped[str | None] = mapped_column(Text)
    consorcio_id: Mapped[str | None] = mapped_column(String(60), index=True)
    documento_id: Mapped[str | None] = mapped_column(String(120))
    seccion_id: Mapped[int | None] = mapped_column(Integer)
    pagina: Mapped[int | None] = mapped_column(Integer)
    verificada: Mapped[bool] = mapped_column(Boolean, default=False)
    derivada: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo: Mapped[str | None] = mapped_column(Text)
    metodo_busqueda: Mapped[str] = mapped_column(String(20))
    k: Mapped[int] = mapped_column(Integer)
    proveedor: Mapped[str] = mapped_column(String(30))
    modelo: Mapped[str] = mapped_column(String(80))
    skill_version: Mapped[str] = mapped_column(String(20))
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0)
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0)
    costo_usd: Mapped[float | None] = mapped_column(Float)
    segundos: Mapped[float] = mapped_column(Float)
    origen: Mapped[str] = mapped_column(String(20), default="cli")  # cli | evaluacion | telegram | web
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))  # None: sin identidad (CLI, Telegram)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


# ---- Usuarios (ver identidad.py) y login (ver auth/). El resto del sistema solo usa `sujeto_externo`.

ROLES = ("administracion", "consejo", "propietario", "inquilino")
ROLES_CON_UNIDAD = ("propietario", "inquilino")


class Usuario(Base):
    """Quien entra al sistema. Mail y contraseña son del login: los usuarios demo y los de plataforma pueden no
    tenerlos. La contraseña nunca se guarda, solo su hash (Argon2)."""

    __tablename__ = "usuarios"
    __table_args__ = (
        UniqueConstraint("sujeto_externo", name="uq_usuarios_sujeto_externo"),
        Index("uq_usuarios_email", text("lower(email)"), unique=True),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    sujeto_externo: Mapped[str] = mapped_column(Text)  # identificador que entrega el login
    nombre: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    hash_contrasena: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    plataforma: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))  # equipo: ve el CRM
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, server_default=text("now()"))
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ultima_actividad: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UnidadFuncional(Base):
    """Un departamento o cochera del edificio. `uf` es su número en la liquidación: con eso el agente busca sus
    expensas. No es `Unidad`, que son las unidades que se extraen de los documentos."""

    __tablename__ = "unidades_funcionales"
    __table_args__ = (
        UniqueConstraint("consorcio_id", "piso", "depto", name="uq_unidades_funcionales_piso_depto"),
        UniqueConstraint("consorcio_id", "uf", name="uq_unidades_funcionales_uf"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    piso: Mapped[str] = mapped_column(Text)
    depto: Mapped[str] = mapped_column(Text)
    uf: Mapped[int | None] = mapped_column(Integer)
    porcentual: Mapped[float | None] = mapped_column(Numeric(6, 3))


class Membresia(Base):
    """Qué es una persona en un consorcio: una fila por usuario, consorcio, unidad y rol. Alguien puede ser
    propietario de dos unidades y además del consejo. Sin membresía activa no hay acceso a ese consorcio."""

    __tablename__ = "membresias"
    __table_args__ = (
        UniqueConstraint("usuario_id", "consorcio_id", "unidad_id", "rol",
                         name="uq_membresias_usuario_consorcio_unidad_rol", postgresql_nulls_not_distinct=True),
        CheckConstraint(f"rol IN {ROLES}", name="ck_membresias_rol"),
        CheckConstraint(
            "(rol IN ('propietario', 'inquilino') AND unidad_id IS NOT NULL) "
            "OR (rol = 'administracion' AND unidad_id IS NULL) OR rol = 'consejo'",
            name="ck_membresias_unidad",
        ),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    unidad_id: Mapped[int | None] = mapped_column(ForeignKey("unidades_funcionales.id", name="fk_membresias_unidad_id"))
    rol: Mapped[str] = mapped_column(String(20))
    activo: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, server_default=text("now()"))
    unidad: Mapped[UnidadFuncional | None] = relationship()


class Invitacion(Base):
    """Invitación de la administración. Se guarda el hash del token, no el token: quien lea la base no puede
    usar los links."""

    __tablename__ = "invitaciones"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_invitaciones_token_hash"),
        CheckConstraint(f"rol IN {ROLES}", name="ck_invitaciones_rol"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    unidad_id: Mapped[int | None] = mapped_column(ForeignKey("unidades_funcionales.id"))
    email: Mapped[str] = mapped_column(Text)
    rol: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(Text)
    creada_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))  # None: desde la terminal
    creada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, server_default=text("now()"))
    vence_en: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    usada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Acceso(Base):
    """Cada intento de login, exitoso o no: bloquea intentos repetidos y alimenta las métricas de uso."""

    __tablename__ = "accesos"
    __table_args__ = (Index("ix_accesos_email_momento", text("lower(email)"), "momento"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))  # None si el mail no existe
    email: Mapped[str] = mapped_column(Text)
    exitoso: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(Text)
    momento: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, server_default=text("now()"))


# ---- Agente (ver agente/). `turnos` no guarda texto: es lo que lee el CRM. El texto vive en `mensajes`,
# que solo usan la memoria y la pantalla del chat, y en `solicitudes`, que lee la administración.


class Conversacion(Base):
    __tablename__ = "conversaciones"
    __table_args__ = (Index("ix_conversaciones_usuario_consorcio", "usuario_id", "consorcio_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # uuid4 hex
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    actualizada_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class Mensaje(Base):
    __tablename__ = "mensajes"
    __table_args__ = (
        CheckConstraint("rol IN ('usuario', 'asistente')", name="ck_mensajes_rol"),
        Index("ix_mensajes_conversacion_id", "conversacion_id", "id"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    conversacion_id: Mapped[str] = mapped_column(ForeignKey("conversaciones.id", ondelete="CASCADE"))
    rol: Mapped[str] = mapped_column(String(20))
    texto: Mapped[str] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class Solicitud(Base):
    """Lo que espera a un humano: derivaciones y reclamos. `titulo` y `detalle` quedan como los propuso
    el modelo; lo que escribe la administración va a `*_final`, para medir el efecto de editar."""

    __tablename__ = "solicitudes"
    __table_args__ = (
        CheckConstraint("tipo IN ('derivacion', 'reclamo')", name="ck_solicitudes_tipo"),
        CheckConstraint(
            "(tipo = 'derivacion' AND estado IN ('pendiente', 'resuelta')) OR "
            "(tipo = 'reclamo' AND estado IN ('pendiente', 'aprobada', 'editada', 'rechazada'))",
            name="ck_solicitudes_tipo_estado",
        ),
        CheckConstraint(
            "motivo IN ('pedido_usuario', 'sin_respaldo', 'cita_no_verificada', 'router_invalido', "
            "'error_funcionalidad', 'reclamo', 'elevada_usuario', 'pedido_directo')",
            name="ck_solicitudes_motivo",
        ),
        Index("ix_solicitudes_consorcio_estado", "consorcio_id", "estado"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(20))
    estado: Mapped[str] = mapped_column(String(20), default="pendiente", server_default="pendiente")
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    autor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    motivo: Mapped[str] = mapped_column(String(30))
    mensaje: Mapped[str] = mapped_column(Text)  # original del usuario
    titulo: Mapped[str] = mapped_column(Text)
    detalle: Mapped[str] = mapped_column(Text)
    unidad: Mapped[str | None] = mapped_column(String(20))
    titulo_final: Mapped[str | None] = mapped_column(Text)
    detalle_final: Mapped[str | None] = mapped_column(Text)
    respuesta: Mapped[str | None] = mapped_column(Text)
    resuelta_por: Mapped[int | None] = mapped_column(ForeignKey("usuarios.id"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    resuelta_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Turno(Base):
    """Una salida de `atender`. Sin columnas de texto libre: `error` es solo el nombre de la excepción.
    El texto queda en `mensajes`, y el turno solo apunta a él."""

    __tablename__ = "turnos"
    __table_args__ = (
        CheckConstraint(
            "resultado IN ('respondida', 'sin_datos', 'derivada', 'reclamo_pendiente', 'fuera_de_alcance', "
            "'tope_excedido', 'mensaje_invalido', 'conversacion_invalida', 'error')",
            name="ck_turnos_resultado",
        ),
        CheckConstraint("feedback IN (-1, 1)", name="ck_turnos_feedback"),
        Index("ix_turnos_consorcio_creado", "consorcio_id", "creado_en"),
        Index("ix_turnos_creado_en", "creado_en"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    rol: Mapped[str] = mapped_column(String(20))
    canal: Mapped[str] = mapped_column(String(20))  # web | cli | telegram
    origen: Mapped[str] = mapped_column(String(20))  # usuario | evaluacion
    conversacion_id: Mapped[str | None] = mapped_column(ForeignKey("conversaciones.id"))
    intencion: Mapped[str | None] = mapped_column(String(30))  # NULL: el router falló
    funcionalidad: Mapped[str | None] = mapped_column(String(30))
    resultado: Mapped[str] = mapped_column(String(30))
    # Ids de las secciones enviadas al modelo. Sin FK: la reingesta las recrea
    secciones_usadas: Mapped[list] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    seccion_citada: Mapped[int | None] = mapped_column(Integer)
    consulta_id: Mapped[int | None] = mapped_column(ForeignKey("consultas.id"))
    solicitud_id: Mapped[int | None] = mapped_column(ForeignKey("solicitudes.id"))
    tokens_entrada: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tokens_salida: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    costo_usd: Mapped[float | None] = mapped_column(Float)
    segundos: Mapped[float] = mapped_column(Float)
    modelo: Mapped[str] = mapped_column(String(80))
    skill_version: Mapped[str] = mapped_column(String(20))
    feedback: Mapped[int | None] = mapped_column(SmallInteger)
    error: Mapped[str | None] = mapped_column(String(60))
    mlflow_traza_id: Mapped[str | None] = mapped_column(Text)
    # De 0 a 1, solo en lo respondido (ver agente/certeza.py); las señales son números y banderas
    certeza: Mapped[float | None] = mapped_column(Float)
    senales_certeza: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))
    # Para que la administración revise la pregunta y la respuesta. Si se borra la conversación, quedan NULL
    mensaje_usuario_id: Mapped[int | None] = mapped_column(ForeignKey("mensajes.id", ondelete="SET NULL"))
    mensaje_asistente_id: Mapped[int | None] = mapped_column(ForeignKey("mensajes.id", ondelete="SET NULL"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


VEREDICTOS = ("correcta", "parcial", "incorrecta")
MOTIVOS_REVISION = ("dato_incorrecto", "cita_incorrecta", "incompleta", "debia_derivar", "derivo_sin_necesidad",
                    "no_entendio")


class Revision(Base):
    """Lo que dice la administración de una respuesta del agente. Una por turno: revisar de nuevo pisa.

    `respuesta_correcta` es texto libre del consorcio: lo ve solo su administración, nunca el CRM."""

    __tablename__ = "revisiones"
    __table_args__ = (
        CheckConstraint("veredicto IN ('correcta', 'parcial', 'incorrecta')", name="ck_revisiones_veredicto"),
        CheckConstraint(
            "motivo IS NULL OR motivo IN ('dato_incorrecto', 'cita_incorrecta', 'incompleta', 'debia_derivar', "
            "'derivo_sin_necesidad', 'no_entendio')",
            name="ck_revisiones_motivo",
        ),
        CheckConstraint("(veredicto = 'correcta') = (motivo IS NULL)", name="ck_revisiones_motivo_si_hay_error"),
        UniqueConstraint("turno_id", name="uq_revisiones_turno_id"),
        Index("ix_revisiones_consorcio_creado", "consorcio_id", "creado_en"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    turno_id: Mapped[int] = mapped_column(ForeignKey("turnos.id", ondelete="CASCADE"))
    consorcio_id: Mapped[str] = mapped_column(ForeignKey("consorcios.id"))
    revisor_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    veredicto: Mapped[str] = mapped_column(String(12))
    motivo: Mapped[str | None] = mapped_column(String(30))
    respuesta_correcta: Mapped[str | None] = mapped_column(Text)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


class GastoModelo(Base):
    """Libro de gastos, solo-inserción: es lo que suma el tope mensual (ver tope.py). No cuelga de
    `documentos` porque `--forzar` y el reemplazo de período borrarían lo ya pagado.
    `costo_usd` NULL: sin precio o sin uso informado."""

    __tablename__ = "gastos_modelo"
    id: Mapped[int] = mapped_column(primary_key=True)
    consorcio_id: Mapped[str | None] = mapped_column(String(60))  # sin FK: es un registro, no una relación
    nodo: Mapped[str] = mapped_column(String(40))
    modelo: Mapped[str] = mapped_column(String(80))
    tokens_entrada: Mapped[int] = mapped_column(Integer)
    tokens_salida: Mapped[int] = mapped_column(Integer)
    costo_usd: Mapped[float | None] = mapped_column(Float)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, index=True)


class Carga(Base):
    """Cómo terminó cada `ingestar_pdf` que tocó la base, solo-inserción: de acá salen los duplicados evitados."""

    __tablename__ = "cargas"
    id: Mapped[int] = mapped_column(primary_key=True)
    consorcio_id: Mapped[str] = mapped_column(String(60))
    usuario_id: Mapped[int | None] = mapped_column(Integer)
    estado: Mapped[str] = mapped_column(String(30))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora, index=True)


# ------------------------------------------------------------------ operaciones


_COMANDO_ACTUALIZAR = "`consorcio actualizar-base` (`make db-actualizar`)"


def crear_tablas() -> None:
    """Deja el esquema listo o frena: una base vacía sube a head; una con tablas se migra a mano.

    Nunca usa `create_all`: en una base existente crearía `usuarios` y rompería el `upgrade` posterior.
    Sin caché: mira la base en cada llamada. El candado evita que dos procesos migren una base vacía
    a la vez."""
    with engine().connect() as con:
        version = _version_del_esquema(con)
        if version is None:
            if con.scalar(text("SELECT to_regclass('documentos') IS NOT NULL")):
                raise RuntimeError(
                    "La base tiene tablas pero no está marcada para Alembic. "
                    f"Respaldarla y correr: {_COMANDO_ACTUALIZAR}."
                )
            con.execute(text("SELECT pg_advisory_xact_lock(hashtext('consorcio:migraciones'))"))
            if _version_del_esquema(con) is None:  # otro proceso pudo migrar mientras se esperaba
                _subir_a_head()
            return
    if version != _revision_head():
        raise RuntimeError(
            f"La base está en la revisión {version} y el código espera {_revision_head()}. "
            f"Respaldarla y correr: {_COMANDO_ACTUALIZAR}."
        )


def actualizar_esquema() -> str | None:
    """Lleva la base a head desde cualquier punto de partida: es lo que corre `make db-actualizar`.

    Decide la marca mirando las tablas, porque el paso correcto depende de la base: un volcado reciente ya
    trae `alembic_version` y marcarlo en `0001_base` lo haría fallar con `DuplicateTable`. Devuelve la
    revisión que tuvo que marcar, o None. Nunca baja la versión de una base sana."""
    from alembic import command

    with engine().connect() as con:
        con.execute(text("SELECT pg_advisory_xact_lock(hashtext('consorcio:migraciones'))"))
        marca = _marca_necesaria(con)
        if marca:
            log.warning("la base se marca en la revisión %s", marca)
            command.stamp(_config_alembic(), marca)
        _subir_a_head()
    return marca


def _marca_necesaria(con) -> str | None:
    """Revisión que demuestran las tablas, si la base no tiene versión o quedó marcada en `0001_base` por
    un stamp de más. En cualquier otra versión manda lo que dice `alembic_version`."""
    if _version_del_esquema(con) not in (None, "0001_base"):
        return None
    if not con.scalar(text("SELECT to_regclass('documentos') IS NOT NULL")):
        return None  # base vacía: el upgrade la crea de cero
    if not _tiene_columna(con, "documentos", "consorcio_id"):
        raise RuntimeError("La base es anterior al 26/10/2026. Respaldarla, correr `consorcio migrar` y repetir.")
    if _tiene_columna(con, "documentos", "sha256"):
        return "0003_crudos"
    if con.scalar(text("SELECT to_regclass('usuarios') IS NOT NULL")):
        return "0002_usuarios"
    return "0001_base"


def _tiene_columna(con, tabla: str, columna: str) -> bool:
    return bool(con.scalar(
        text("SELECT count(*) FROM information_schema.columns WHERE table_name = :t AND column_name = :c"),
        {"t": tabla, "c": columna},
    ))


def _version_del_esquema(con) -> str | None:
    if not con.scalar(text("SELECT to_regclass('alembic_version') IS NOT NULL")):
        return None
    return con.scalar(text("SELECT version_num FROM alembic_version"))


def _config_alembic():
    from alembic.config import Config as ConfigAlembic

    from ..config import RAIZ_REPO

    cfg = ConfigAlembic(str(RAIZ_REPO / "alembic.ini"))
    cfg.set_main_option("script_location", str(RAIZ_REPO / "migraciones").replace("%", "%%"))
    cfg.attributes["en_proceso"] = True  # env.py no toca el logging de la app
    return cfg


_head = None


def _revision_head() -> str:
    """La revisión que espera el código. Sale de los archivos, no de la base: no cambia en el proceso."""
    global _head
    if _head is None:
        from alembic.script import ScriptDirectory

        _head = ScriptDirectory.from_config(_config_alembic()).get_current_head()
    return _head


def _subir_a_head() -> None:
    from alembic import command

    command.upgrade(_config_alembic(), "head")


def guardar_documento(
    *,
    documento_id: str,
    nombre_archivo: str,
    ruta: str,
    clasificacion: dict,
    paginas: list[dict],
    doc: DocumentoExtraido,
    costo: dict,
    llamadas: list[dict],
    segundos_total: float,
    liquidacion: dict | None = None,
    consorcio_id: str | None = None,
    sha256: str | None = None,
    crudo_clave: str | None = None,
    reemplazar_periodo: bool = False,
) -> dict:
    """Inserta (o reemplaza) el documento y todo lo extraído. Idempotente por consorcio + hash.

    Una sola liquidación por consorcio y período: este es el control autoritativo (el de la ingesta
    evita gastar de más, pero no cubre dos PDF distintos en paralelo). Si otro documento ya tiene el
    período, lanza `PeriodoDuplicado`; con `reemplazar_periodo` lo borra en la misma transacción y lo
    informa en `reemplazado`, para que quien llama borre su crudo."""
    crear_tablas()
    consorcio_id = consorcio_id or documento_id.split(":", 1)[0]
    _asegurar_consorcio_en_paralelo(consorcio_id)
    m = doc.metadatos
    with Session(engine()) as s:
        reemplazado = _resolver_periodo(s, consorcio_id, documento_id, liquidacion, reemplazar_periodo)
        previo = s.get(Documento, documento_id)
        # Reingestar no debe reactivar un documento sacado del buscador (p. ej. Cabrera, 26/10)
        activo = previo.activo if previo else True
        # La clave depende solo del archivo: una reingesta sin bucket no pierde el crudo ya subido
        crudo_clave = crudo_clave or (previo.crudo_clave if previo else None)
        if previo:
            s.delete(previo)
            s.flush()

        d = Documento(
            id=documento_id,
            consorcio_id=consorcio_id,
            nombre_archivo=nombre_archivo,
            ruta=ruta,
            tipo_documento=m.tipo_documento,
            titulo=m.titulo,
            fecha_documento=m.fecha_documento,
            consorcio=m.consorcio,
            resumen=m.resumen,
            total_paginas=clasificacion.get("total_paginas", len(paginas)),
            metodo_extraccion=clasificacion.get("metodo", ""),
            confianza_clasificador=clasificacion.get("confianza", 0.0),
            clasificacion=clasificacion,
            costo=costo,
            segundos_total=segundos_total,
            activo=activo,
            sha256=sha256,
            crudo_clave=crudo_clave,
        )
        for p in paginas:
            d.paginas.append(
                Pagina(numero=p["numero"], metodo=p["metodo"], confianza=p.get("confianza"),
                       chars=len(p["texto"]), texto=p["texto"])
            )
        for p in doc.personas:
            per = Persona(
                nombre=p.nombre, rol=p.rol, nacionalidad=p.nacionalidad, estado_civil=p.estado_civil,
                domicilio=p.domicilio, pagina=p.pagina, fragmento=p.fragmento,
            )
            for ds in p.datos_sensibles:
                per.datos_sensibles.append(
                    DatoSensible(documento_id=documento_id, tipo=ds.tipo, valor=ds.valor, pagina=p.pagina)
                )
            d.personas.append(per)
        for o in doc.organizaciones:
            org = Organizacion(nombre=o.nombre, tipo=o.tipo, rol=o.rol, domicilio=o.domicilio,
                               pagina=o.pagina, fragmento=o.fragmento)
            for ds in o.datos_sensibles:
                org.datos_sensibles.append(
                    DatoSensible(documento_id=documento_id, tipo=ds.tipo, valor=ds.valor, pagina=o.pagina)
                )
            d.organizaciones.append(org)
        for x in doc.direcciones:
            d.direcciones.append(Direccion(**x.model_dump()))
        for x in doc.eventos:
            d.eventos.append(Evento(**x.model_dump()))
        for x in doc.unidades:
            d.unidades.append(Unidad(**x.model_dump()))
        for x in doc.montos:
            d.montos.append(Monto(**x.model_dump()))
        for x in doc.secciones:
            d.secciones.append(Seccion(**x.model_dump()))
        for ll in llamadas:
            d.llamadas.append(LlamadaModelo(**ll))
        if liquidacion:
            d.liquidacion = _liquidacion_desde_dict(documento_id, liquidacion)

        s.add(d)
        s.commit()
        filas_liq = (
            {"rubros": len(d.liquidacion.rubros), "gastos": len(d.liquidacion.gastos),
             "unidades": len(d.liquidacion.unidades)}
            if d.liquidacion else None
        )
        return {
            "liquidacion": filas_liq,
            "documento_id": documento_id,
            "reemplazado": reemplazado,
            "filas": {
                "paginas": len(d.paginas), "personas": len(d.personas),
                "organizaciones": len(d.organizaciones), "direcciones": len(d.direcciones),
                "eventos": len(d.eventos), "unidades": len(d.unidades), "montos": len(d.montos),
                "secciones": len(d.secciones), "llamadas_modelo": len(d.llamadas),
                "datos_sensibles": s.scalar(
                    select(func_count()).select_from(DatoSensible).where(DatoSensible.documento_id == documento_id)
                ),
            },
        }


def func_count():
    from sqlalchemy import func

    return func.count()


def _asegurar_consorcio_en_paralelo(consorcio_id: str) -> None:
    """Dos ingestas de PDF distintos pueden crear el mismo consorcio a la vez: si otra lo creó entre el
    chequeo y el insert de `asegurar_consorcio`, el consorcio ya está y no hay nada que hacer."""
    from sqlalchemy.exc import IntegrityError

    try:
        asegurar_consorcio(consorcio_id)
    except IntegrityError:
        log.debug("el consorcio %s lo creó otra ingesta en paralelo", consorcio_id)


class PeriodoDuplicado(Exception):
    """Otro documento del consorcio ya tiene la liquidación de ese período."""

    def __init__(self, existente: dict):
        super().__init__(f"el período {existente.get('periodo')} ya está en {existente.get('id')}")
        self.existente = existente


def periodo_valido(periodo: str | None) -> bool:
    """Solo un período AAAA-MM entra al control de duplicados: lo que el modelo no normalizó no bloquea."""
    return bool(periodo) and re.fullmatch(r"\d{4}-\d{2}", periodo) is not None


def _clave_candado(texto: str) -> int:
    """Clave bigint de un advisory lock de Postgres."""
    return int.from_bytes(hashlib.sha256(texto.encode()).digest()[:8], "big", signed=True)


@contextmanager
def candado_ingesta(consorcio_id: str, sha256: str) -> Iterator[bool]:
    """Candado de una ingesta: rinde True si se obtuvo y False si otra carga del mismo PDF en el mismo
    consorcio está en curso. La clave incluye el consorcio: si no, `en_proceso` revelaría una carga
    en otro consorcio. Va en una conexión propia y ociosa, porque un advisory lock de sesión vive con
    ella, y se libera antes de devolverla al pool: el pool no resetea estos candados."""
    clave = _clave_candado(f"ingesta:{consorcio_id}:{sha256}")
    con = engine().connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        obtenido = bool(con.scalar(text("SELECT pg_try_advisory_lock(CAST(:k AS bigint))"), {"k": clave}))
        try:
            yield obtenido
        finally:
            if obtenido:
                try:
                    con.scalar(text("SELECT pg_advisory_unlock(CAST(:k AS bigint))"), {"k": clave})
                except Exception as e:  # conexión caída: al descartarla, Postgres suelta el candado
                    log.warning("no se pudo liberar el candado de ingesta (%s); se descarta la conexión", e)
                    con.invalidate()
    finally:
        con.close()


def _ficha_documento(d: Documento, **extra) -> dict:
    """Lo que se le informa a quien carga un duplicado. Sirve para JSON: `creado_en` va como texto."""
    return {"id": d.id, "nombre_archivo": d.nombre_archivo, "titulo": d.titulo,
            "creado_en": d.creado_en.isoformat(timespec="seconds") if d.creado_en else None, **extra}


def buscar_documento(documento_id: str, consorcio_id: str | None = None) -> dict | None:
    """Ficha de un documento del consorcio por id (`<consorcio>:<hash16>`): vale también para las filas
    históricas, con `sha256` NULL. Un id de otro consorcio se trata como inexistente."""
    cid = exigir_consorcio(consorcio_id)
    crear_tablas()
    with Session(engine()) as s:
        d = s.get(Documento, documento_id)
        return _ficha_documento(d) if d and d.consorcio_id == cid else None


def _documentos_del_periodo(s: Session, consorcio_id: str, periodo: str, excluir_id: str) -> list[Documento]:
    return list(s.scalars(
        select(Documento).join(Liquidacion)
        .where(Documento.consorcio_id == consorcio_id, Liquidacion.periodo == periodo, Documento.id != excluir_id)
        .order_by(Documento.creado_en.desc())
    ).all())


def documento_del_periodo(consorcio_id: str, periodo: str, excluir_id: str) -> dict | None:
    """Otro documento del consorcio con la liquidación de ese período, si lo hay."""
    crear_tablas()
    with Session(engine()) as s:
        otros = _documentos_del_periodo(s, consorcio_id, periodo, excluir_id)
        return _ficha_documento(otros[0], periodo=periodo) if otros else None


def _resolver_periodo(
    s: Session, consorcio_id: str, documento_id: str, liquidacion: dict | None, reemplazar: bool
) -> dict | None:
    """Dentro de la transacción del guardado: serializa por consorcio + período y aplica la regla de una
    liquidación por período. El candado se libera solo al terminar la transacción. `reemplazado` lleva
    `otros` por si una base anterior a esta regla ya tenía más de un documento del período."""
    periodo = (liquidacion or {}).get("periodo")
    if not periodo_valido(periodo):
        return None
    clave = _clave_candado(f"periodo:{consorcio_id}:{periodo}")
    s.execute(text("SELECT pg_advisory_xact_lock(CAST(:k AS bigint))"), {"k": clave})
    otros = _documentos_del_periodo(s, consorcio_id, periodo, documento_id)
    if not otros:
        return None
    if not reemplazar:
        raise PeriodoDuplicado(_ficha_documento(otros[0], periodo=periodo))
    fichas = [{"id": d.id, "crudo_clave": d.crudo_clave} for d in otros]
    for d in otros:
        s.delete(d)
    s.flush()
    return {**fichas[0], "otros": fichas[1:]}


def paginas_guardadas(documento_id: str, consorcio_id: str | None = None) -> list[dict]:
    """Páginas de la última corrida de un documento del consorcio, como las produce la ingesta."""
    cid = exigir_consorcio(consorcio_id)
    with Session(engine()) as s:
        filas = s.scalars(
            select(Pagina).join(Documento, Documento.id == Pagina.documento_id)
            .where(Pagina.documento_id == documento_id, Documento.consorcio_id == cid).order_by(Pagina.numero)
        ).all()
        return [{"numero": p.numero, "texto": p.texto, "metodo": p.metodo, "confianza": p.confianza} for p in filas]


def listar_documentos(consorcio_id: str | None = None) -> list[dict]:
    """Documentos de un consorcio."""
    cid = exigir_consorcio(consorcio_id)
    crear_tablas()
    with Session(engine()) as s:
        q = select(Documento).where(Documento.consorcio_id == cid).order_by(Documento.creado_en.desc())
        return [_fila_documento(d) for d in s.scalars(q).all()]


def unidades_de(consorcio_id: str) -> list[dict]:
    """Piso y depto de las unidades funcionales cargadas en el consorcio."""
    cid = exigir_consorcio(consorcio_id)
    crear_tablas()
    with Session(engine()) as s:
        filas = s.execute(select(UnidadFuncional.piso, UnidadFuncional.depto)
                          .where(UnidadFuncional.consorcio_id == cid).order_by(UnidadFuncional.id)).all()
    return [{"piso": p, "depto": d} for p, d in filas]


def listar_documentos_de_todos_los_consorcios() -> list[dict]:
    """Cruza consorcios a propósito (con el suyo en cada fila): solo para la CLI de administración."""
    crear_tablas()
    with Session(engine()) as s:
        return [_fila_documento(d) for d in s.scalars(select(Documento).order_by(Documento.creado_en.desc())).all()]


def _fila_documento(d: Documento) -> dict:
    return {
        "id": d.id, "consorcio": d.consorcio_id, "archivo": d.nombre_archivo, "tipo": d.tipo_documento, "titulo": d.titulo,
        "fecha": d.fecha_documento, "paginas": d.total_paginas, "metodo": d.metodo_extraccion,
        "estado": d.estado, "activo": d.activo, "costo_usd": (d.costo or {}).get("costo_usd"),
    }


def activar_documento(id_o_nombre: str, activo: bool, consorcio_id: str | None = None) -> list[str]:
    """Activa o desactiva para el buscador los documentos del consorcio cuyo id o nombre contenga el texto."""
    cid = exigir_consorcio(consorcio_id)
    with Session(engine()) as s:
        q = select(Documento).where(
            (Documento.id == id_o_nombre) | Documento.nombre_archivo.ilike(f"%{id_o_nombre}%"),
            Documento.consorcio_id == cid,
        )
        docs = s.scalars(q).all()
        for d in docs:
            d.activo = activo
        s.commit()
        return [d.nombre_archivo for d in docs]


def enmascarar(valor: str) -> str:
    """Deja visibles los últimos 3 caracteres: para la muestra sanitizada."""
    if len(valor) <= 3:
        return "*" * len(valor)
    return "*" * (len(valor) - 3) + valor[-3:]


def _liquidacion_desde_dict(documento_id: str, liq: dict) -> Liquidacion:
    fila = Liquidacion(
        documento_id=documento_id,
        periodo=liq.get("periodo", ""),
        primer_vencimiento=liq.get("primer_vencimiento"),
        segundo_vencimiento=liq.get("segundo_vencimiento"),
        consorcio=liq.get("consorcio"),
        administracion=liq.get("administracion"),
        total_gastos=liq.get("total_gastos"),
        filas_prorrateo=liq.get("filas_prorrateo", 0),
        filas_no_parseadas=liq.get("filas_no_parseadas", 0),
    )
    for r in liq.get("rubros", []):
        fila.rubros.append(LiquidacionRubro(documento_id=documento_id, **r))
    for g in liq.get("gastos", []):
        fila.gastos.append(LiquidacionGasto(documento_id=documento_id, **g))
    for u in liq.get("unidades", []):
        fila.unidades.append(LiquidacionUnidad(documento_id=documento_id, **u))
    return fila


def consultar_expensas(
    que: str = "resumen", periodo: str | None = None, unidad: str | None = None, texto: str | None = None,
    consorcio_id: str | None = None,
) -> list[dict]:
    """Consulta por SQL sobre las liquidaciones de un consorcio: sin modelo, los montos salen tal cual del PDF.

    que: resumen | rubros | gastos | unidades.  periodo: AAAA-MM.
    unidad: piso-depto ("5-D", "COCH-16") o número de UF.  texto: filtra gastos por rubro/proveedor/concepto.
    """
    cid = exigir_consorcio(consorcio_id)
    crear_tablas()
    with Session(engine()) as s:
        if que == "resumen":
            q = select(Liquidacion).join(Documento).where(Documento.consorcio_id == cid)
            if periodo:  # como en los otros tres: antes el resumen devolvía todos los períodos sin aviso
                q = q.where(Liquidacion.periodo == periodo)
            filas = s.scalars(q.order_by(Liquidacion.periodo)).all()
            return [
                {"periodo": f.periodo, "primer_vencimiento": f.primer_vencimiento,
                 "segundo_vencimiento": f.segundo_vencimiento, "total_gastos": f.total_gastos,
                 "rubros": len(f.rubros), "gastos": len(f.gastos), "unidades": len(f.unidades),
                 "filas_no_parseadas": f.filas_no_parseadas, "documento_id": f.documento_id}
                for f in filas
            ]
        if que == "rubros":
            q = select(LiquidacionRubro, Liquidacion.periodo).join(Liquidacion).join(Documento).where(Documento.consorcio_id == cid)
            if periodo:
                q = q.where(Liquidacion.periodo == periodo)
            return [
                {"periodo": per, "numero": r.numero, "rubro": r.nombre, "total": r.total, "porcentaje": r.porcentaje}
                for r, per in s.execute(q.order_by(Liquidacion.periodo, LiquidacionRubro.numero)).all()
            ]
        if que == "gastos":
            q = select(LiquidacionGasto, Liquidacion.periodo).join(Liquidacion).join(Documento).where(Documento.consorcio_id == cid)
            if periodo:
                q = q.where(Liquidacion.periodo == periodo)
            if texto:
                patron = f"%{texto}%"
                q = q.where(
                    LiquidacionGasto.rubro.ilike(patron)
                    | LiquidacionGasto.proveedor.ilike(patron)
                    | LiquidacionGasto.concepto.ilike(patron)
                )
            return [
                {"periodo": per, "rubro": g.rubro, "proveedor": g.proveedor, "concepto": g.concepto,
                 "importe": g.importe, "fecha": g.fecha, "comprobante": g.comprobante, "pagina": g.pagina}
                for g, per in s.execute(q.order_by(Liquidacion.periodo, LiquidacionGasto.rubro)).all()
            ]
        if que == "unidades":
            q = select(LiquidacionUnidad, Liquidacion.periodo).join(Liquidacion).join(Documento).where(Documento.consorcio_id == cid)
            if periodo:
                q = q.where(Liquidacion.periodo == periodo)
            if unidad:
                if unidad.isdigit():
                    q = q.where(LiquidacionUnidad.uf == int(unidad))
                else:
                    q = q.where(LiquidacionUnidad.piso_depto.ilike(unidad))
            return [
                {"periodo": per, "uf": u.uf, "piso_depto": u.piso_depto, "porcentual": u.porcentual,
                 "expensas_ordinarias": u.expensas_ordinarias, "expensa_extraordinaria": u.expensa_extraordinaria,
                 "prevision_gastos": u.prevision_gastos, "otros_conceptos": u.otros_conceptos,
                 "total_expensa": u.total_expensa, "gasto_particular": u.gasto_particular,
                 "saldo_anterior": u.saldo_anterior, "a_pagar": u.a_pagar, "pagina": u.pagina}
                for u, per in s.execute(q.order_by(Liquidacion.periodo, LiquidacionUnidad.uf)).all()
            ]
        raise ValueError(f"consulta desconocida: {que!r}")


# ------------------------------------------------------------------ consorcios y chats


def existe_consorcio(consorcio_id: str) -> bool:
    """Para la CLI: un consorcio mal escrito tiene que distinguirse de uno sin datos."""
    if "\x00" in consorcio_id:
        return False
    crear_tablas()
    with Session(engine()) as s:
        return s.get(Consorcio, consorcio_id) is not None


def asegurar_consorcio(consorcio_id: str, nombre: str | None = None) -> None:
    with Session(engine()) as s:
        if not s.get(Consorcio, consorcio_id):
            s.add(Consorcio(id=consorcio_id, nombre=nombre or consorcio_id))
            s.commit()


def consorcio_de_chat(chat_id: int, descripcion: str | None = None) -> str:
    """Consorcio (base) que usa un chat de Telegram. Si el chat es nuevo, se le crea una base propia
    (`chat-<id>` o `grupo-<id>`): así cada número arranca con cero documentos. Para que varios
    números compartan un consorcio: `consorcio consorcios --asignar-chat ID --a CONSORCIO`."""
    crear_tablas()
    with Session(engine()) as s:
        chat = s.get(Chat, chat_id)
        if chat:
            return chat.consorcio_id
    cid = consorcio_de_chat_id(chat_id)
    asegurar_consorcio(cid, descripcion or f"Base del chat {chat_id}")
    with Session(engine()) as s:
        s.add(Chat(chat_id=chat_id, consorcio_id=cid, descripcion=descripcion))
        s.commit()
    return cid


def asignar_chat(chat_id: int, consorcio_id: str, nombre: str | None = None) -> None:
    """Hace que un número/grupo use la base de `consorcio_id` (se crea si no existe)."""
    crear_tablas()
    asegurar_consorcio(consorcio_id, nombre)
    with Session(engine()) as s:
        chat = s.get(Chat, chat_id)
        if chat:
            chat.consorcio_id = consorcio_id
        else:
            s.add(Chat(chat_id=chat_id, consorcio_id=consorcio_id))
        s.commit()


def listar_consorcios() -> list[dict]:
    crear_tablas()
    with Session(engine()) as s:
        filas = []
        for c in s.scalars(select(Consorcio).order_by(Consorcio.creado_en)).all():
            docs = s.scalar(select(func_count()).select_from(Documento).where(Documento.consorcio_id == c.id))
            chats = [ch.chat_id for ch in s.scalars(select(Chat).where(Chat.consorcio_id == c.id)).all()]
            filas.append({"id": c.id, "nombre": c.nombre, "documentos": docs, "chats": chats})
        return filas


class AdministracionNoEncontrada(LookupError):
    """El id de Administración no existe."""


class ConsorcioNoEncontrado(LookupError):
    """El id de consorcio no existe."""


def crear_administracion(nombre: str) -> int:
    """Crea una Administración y devuelve su id. El nombre va de 1 a 120 caracteres, sin espacios de más."""
    nombre = " ".join(nombre.split())
    if not 1 <= len(nombre) <= 120:
        raise ValueError("el nombre de la Administración tiene que tener entre 1 y 120 caracteres")
    crear_tablas()
    with Session(engine()) as s, s.begin():
        fila = Administracion(nombre=nombre)
        s.add(fila)
        s.flush()
        return fila.id


def asignar_administracion(consorcio_id: str, administracion_id: int | None) -> None:
    """Pone el consorcio en esa Administración (None: lo saca de la que tenga). Nunca crea un consorcio."""
    crear_tablas()
    with Session(engine()) as s, s.begin():
        consorcio = s.get(Consorcio, consorcio_id)
        if consorcio is None:
            raise ConsorcioNoEncontrado(consorcio_id)
        if administracion_id is not None and s.get(Administracion, administracion_id) is None:
            raise AdministracionNoEncontrada(administracion_id)
        consorcio.administracion_id = administracion_id


def listar_administraciones() -> list[dict]:
    """Cada Administración con sus consorcios, y al final los consorcios sin Administración."""
    crear_tablas()
    with Session(engine()) as s:
        consorcios = s.execute(select(Consorcio.id, Consorcio.administracion_id).order_by(Consorcio.id)).all()
        filas = [{"id": a.id, "nombre": a.nombre, "consorcios": [c for c, adm in consorcios if adm == a.id]}
                 for a in s.scalars(select(Administracion).order_by(Administracion.id))]
    filas.append({"id": None, "nombre": "Sin administración", "consorcios": [c for c, adm in consorcios if adm is None]})
    return filas


def migrar_consorcios(por_defecto: str) -> list[dict]:
    """Migración 26/10/2026: los documentos existentes (id = hash) pasan a `<consorcio>:<hash>`.
    Idempotente. Cabrera va a su propio consorcio y vuelve a estar activo ahí; el resto, al consorcio
    por defecto. Devuelve [{viejo, nuevo, consorcio}]."""
    # `consorcios` tiene FK a `administraciones`: va primero, y la 0011 la crea con IF NOT EXISTS
    Base.metadata.create_all(engine(), tables=[Administracion.__table__, Consorcio.__table__, Chat.__table__])
    hijas = ["paginas", "personas", "organizaciones", "direcciones", "eventos", "unidades", "montos",
             "secciones", "llamadas_modelo", "datos_sensibles", "liquidaciones",
             # cuelgan de liquidaciones.documento_id
             "liquidacion_rubros", "liquidacion_gastos", "liquidacion_unidades"]
    cambios: list[dict] = []
    with engine().begin() as con:
        for t in hijas:
            con.execute(text(f"ALTER TABLE {t} ALTER COLUMN documento_id TYPE varchar(120)"))
        con.execute(text("ALTER TABLE documentos ALTER COLUMN id TYPE varchar(120)"))
        con.execute(text("ALTER TABLE documentos ADD COLUMN IF NOT EXISTS consorcio_id varchar(60)"))
        con.execute(text("ALTER TABLE consultas ALTER COLUMN documento_id TYPE varchar(120)"))
        con.execute(text("ALTER TABLE consultas ADD COLUMN IF NOT EXISTS consorcio_id varchar(60)"))
        # Los FKs tienen que poder diferirse para cambiar la clave primaria y las hijas en la misma transacción
        for nombre, tabla in con.execute(text(
            "SELECT conname, conrelid::regclass FROM pg_constraint "
            "WHERE confrelid IN ('documentos'::regclass, 'liquidaciones'::regclass)"
        )).all():
            con.execute(text(f"ALTER TABLE {tabla} ALTER CONSTRAINT {nombre} DEFERRABLE INITIALLY DEFERRED"))
        con.execute(text("SET CONSTRAINTS ALL DEFERRED"))
        con.execute(text("INSERT INTO consorcios (id, nombre, creado_en) VALUES (:id, :n, now()) ON CONFLICT DO NOTHING"),
                    [{"id": por_defecto, "n": "Mercedes 274"}, {"id": "cabrera", "n": "Edificio Cabrera"}])
        docs = con.execute(text("SELECT id, nombre_archivo FROM documentos WHERE id NOT LIKE '%:%'")).all()
        for viejo, nombre in docs:
            cid = "cabrera" if "cabrera" in nombre.lower() else por_defecto
            nuevo = id_documento(cid, viejo)
            con.execute(text("UPDATE documentos SET id = :n, consorcio_id = :c WHERE id = :v"),
                        {"n": nuevo, "c": cid, "v": viejo})
            if cid == "cabrera":  # en su propio consorcio vuelve a ser un documento válido
                con.execute(text("UPDATE documentos SET activo = true WHERE id = :n"), {"n": nuevo})
            for t in hijas:
                con.execute(text(f"UPDATE {t} SET documento_id = :n WHERE documento_id = :v"), {"n": nuevo, "v": viejo})
            con.execute(text("UPDATE consultas SET documento_id = :n WHERE documento_id = :v"), {"n": nuevo, "v": viejo})
            cambios.append({"viejo": viejo, "nuevo": nuevo, "consorcio": cid})
        con.execute(text("UPDATE documentos SET consorcio_id = split_part(id, ':', 1) WHERE consorcio_id IS NULL"))
        con.execute(text("UPDATE consultas SET consorcio_id = :c WHERE consorcio_id IS NULL"), {"c": por_defecto})
    # Los chequeos diferidos corren al commit; el DDL que sigue no puede ir en la misma transacción
    with engine().begin() as con:
        # Diferirlos solo hacía falta para la transacción de arriba: los modelos y las migraciones no lo son
        for nombre, tabla in con.execute(text(
            "SELECT conname, conrelid::regclass FROM pg_constraint "
            "WHERE confrelid IN ('documentos'::regclass, 'liquidaciones'::regclass) AND condeferrable"
        )).all():
            con.execute(text(f"ALTER TABLE {tabla} ALTER CONSTRAINT {nombre} NOT DEFERRABLE"))
        con.execute(text("CREATE INDEX IF NOT EXISTS ix_consultas_consorcio_id ON consultas (consorcio_id)"))
        existe = con.execute(text(
            "SELECT 1 FROM pg_constraint WHERE conname = 'documentos_consorcio_id_fkey'"
        )).first()
        if not existe:
            con.execute(text("ALTER TABLE documentos ALTER COLUMN consorcio_id SET NOT NULL"))
            con.execute(text("ALTER TABLE documentos ADD CONSTRAINT documentos_consorcio_id_fkey "
                             "FOREIGN KEY (consorcio_id) REFERENCES consorcios(id)"))
            con.execute(text("CREATE INDEX IF NOT EXISTS ix_documentos_consorcio_id ON documentos (consorcio_id)"))
    return cambios


def listar_consultas(consorcio_id: str | None = None, origen: str | None = None, ultimas: int = 20) -> list[dict]:
    """Últimas consultas de un consorcio (de cualquier canal), para revisar qué se preguntó y qué se contestó."""
    cid = exigir_consorcio(consorcio_id)
    return _ultimas_consultas(Consulta.consorcio_id == cid, origen, ultimas)


def listar_consultas_de_todos_los_consorcios(origen: str | None = None, ultimas: int = 20) -> list[dict]:
    """Cruza consorcios a propósito (con el suyo en cada fila): solo para la CLI de administración."""
    return _ultimas_consultas(None, origen, ultimas)


def _ultimas_consultas(filtro, origen: str | None, ultimas: int) -> list[dict]:
    crear_tablas()
    with Session(engine()) as s:
        q = select(Consulta).order_by(Consulta.creado_en.desc()).limit(ultimas)
        if filtro is not None:
            q = q.where(filtro)
        if origen:
            q = q.where(Consulta.origen == origen)
        return [
            {"cuando": c.creado_en.isoformat(timespec="seconds"), "origen": c.origen, "consorcio": c.consorcio_id,
             "pregunta": c.pregunta, "derivada": c.derivada, "verificada": c.verificada, "motivo": c.motivo,
             "respuesta": c.respuesta, "cita": c.cita, "documento_id": c.documento_id, "pagina": c.pagina,
             "segundos": c.segundos, "tokens": (c.tokens_entrada or 0) + (c.tokens_salida or 0)}
            for c in s.scalars(q).all()
        ]


def guardar_consulta(datos: dict) -> int:
    crear_tablas()
    with Session(engine()) as s:
        c = Consulta(**datos)
        s.add(c)
        s.commit()
        return c.id


def registrar_gasto(
    consorcio_id: str | None, nodo: str, modelo: str, tokens_entrada: int, tokens_salida: int, costo_usd: float | None
) -> None:
    """Una fila del libro de gastos. Lo llama `Contador.registrar`, que decide qué hacer si falla."""
    crear_tablas()
    with Session(engine()) as s:
        s.add(GastoModelo(consorcio_id=consorcio_id, nodo=nodo[:40], modelo=modelo[:80], tokens_entrada=tokens_entrada,
                          tokens_salida=tokens_salida, costo_usd=costo_usd))
        s.commit()


def registrar_carga(consorcio_id: str, usuario_id: int | None, estado: str) -> None:
    crear_tablas()
    with Session(engine()) as s:
        s.add(Carga(consorcio_id=consorcio_id, usuario_id=usuario_id, estado=estado))
        s.commit()
