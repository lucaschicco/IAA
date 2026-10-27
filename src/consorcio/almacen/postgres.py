"""Modelo relacional (SQLAlchemy 2.0) y guardado de un documento extraído.

Regla de datos sensibles: DNI, CUIT/CUIL, teléfonos, emails y fechas de nacimiento
van a `datos_sensibles` con `sensible=true`. El resto de las tablas no los contiene,
así `enmascarado()` puede servir para generar la muestra sanitizada del repo.
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from ..config import config
from ..ingesta.esquemas import DocumentoExtraido
from ..texto import consorcio_de_chat_id, id_documento

_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = create_engine(config.postgres_url, pool_pre_ping=True)
    return _engine


class Base(DeclarativeBase):
    pass


def _ahora() -> datetime:
    return datetime.now(UTC)


class Consorcio(Base):
    """Una base por consorcio: todo lo que se ingesta y se consulta lleva `consorcio_id`.
    Por ahora cada número/grupo de Telegram tiene el suyo (`chat-<id>`); la tabla `chats` permite
    después asignar varios números a un mismo consorcio."""

    __tablename__ = "consorcios"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)  # slug: mercedes-274, chat-123
    nombre: Mapped[str] = mapped_column(Text)
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
    fecha_documento: Mapped[str | None] = mapped_column(String(40))
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
    periodo: Mapped[str] = mapped_column(String(7))  # AAAA-MM
    primer_vencimiento: Mapped[str | None] = mapped_column(String(20))
    segundo_vencimiento: Mapped[str | None] = mapped_column(String(20))
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
    origen: Mapped[str] = mapped_column(String(20), default="cli")  # cli | evaluacion | telegram
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_ahora)


# ------------------------------------------------------------------ operaciones


def crear_tablas() -> None:
    Base.metadata.create_all(engine())


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
) -> dict:
    """Inserta (o reemplaza) el documento y todo lo extraído. Idempotente por consorcio + hash."""
    crear_tablas()
    consorcio_id = consorcio_id or documento_id.split(":", 1)[0]
    asegurar_consorcio(consorcio_id)
    m = doc.metadatos
    with Session(engine()) as s:
        previo = s.get(Documento, documento_id)
        # Reingestar no debe reactivar un documento sacado del buscador (p. ej. Cabrera, 26/10)
        activo = previo.activo if previo else True
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


def paginas_guardadas(documento_id: str) -> list[dict]:
    """Páginas de la última corrida de un documento (por hash), como las produce la ingesta."""
    with Session(engine()) as s:
        filas = s.query(Pagina).filter(Pagina.documento_id == documento_id).order_by(Pagina.numero).all()
        return [{"numero": p.numero, "texto": p.texto, "metodo": p.metodo, "confianza": p.confianza} for p in filas]


def listar_documentos(consorcio_id: str | None = None) -> list[dict]:
    """Documentos de un consorcio (None = todos, con su consorcio en cada fila)."""
    crear_tablas()
    with Session(engine()) as s:
        q = select(Documento).order_by(Documento.creado_en.desc())
        if consorcio_id:
            q = q.where(Documento.consorcio_id == consorcio_id)
        filas = s.scalars(q).all()
        return [
            {
                "id": d.id, "consorcio": d.consorcio_id, "archivo": d.nombre_archivo, "tipo": d.tipo_documento, "titulo": d.titulo,
                "fecha": d.fecha_documento, "paginas": d.total_paginas, "metodo": d.metodo_extraccion,
                "estado": d.estado, "activo": d.activo, "costo_usd": (d.costo or {}).get("costo_usd"),
            }
            for d in filas
        ]


def activar_documento(id_o_nombre: str, activo: bool, consorcio_id: str | None = None) -> list[str]:
    """Activa o desactiva para el buscador los documentos cuyo id o nombre contenga el texto."""
    with Session(engine()) as s:
        q = select(Documento).where(
            (Documento.id == id_o_nombre) | Documento.nombre_archivo.ilike(f"%{id_o_nombre}%")
        )
        if consorcio_id:
            q = q.where(Documento.consorcio_id == consorcio_id)
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
    crear_tablas()
    cid = consorcio_id or config.consorcio_por_defecto
    with Session(engine()) as s:
        if que == "resumen":
            filas = s.scalars(
                select(Liquidacion).join(Documento).where(Documento.consorcio_id == cid).order_by(Liquidacion.periodo)
            ).all()
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


def migrar_consorcios(por_defecto: str | None = None) -> list[dict]:
    """Migración 26/10/2026: los documentos existentes (id = hash) pasan a `<consorcio>:<hash>`.
    Idempotente. Cabrera va a su propio consorcio y vuelve a estar activo ahí; el resto, al consorcio
    por defecto. Devuelve [{viejo, nuevo, consorcio}] para replicar el cambio de id en Neo4j."""
    por_defecto = por_defecto or config.consorcio_por_defecto
    crear_tablas()  # crea consorcios y chats si faltan
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
    """Últimas consultas registradas (de cualquier canal), para revisar qué se preguntó y qué se contestó."""
    crear_tablas()
    with Session(engine()) as s:
        q = select(Consulta).order_by(Consulta.creado_en.desc()).limit(ultimas)
        if consorcio_id:
            q = q.where(Consulta.consorcio_id == consorcio_id)
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
