"""`consorcio sembrar-demo`: dos edificios y cinco usuarios inventados para probar la web sin clave.

Sin modelo, sin bucket y sin `datos/`: todo está escrito acá. Idempotente: `guardar_documento` reemplaza
por consorcio + hash y los usuarios se buscan por `sujeto_externo`. Los importes también son inventados,
y distintos en cada consorcio, para que un cruce entre ellos se note.
"""
from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..almacen.postgres import (
    Membresia,
    MembresiaUnidad,
    Usuario,
    asegurar_consorcio,
    crear_tablas,
    engine,
    guardar_documento,
)
from ..ingesta.esquemas import DocumentoExtraido, MetadatosDocumento, Seccion
from ..texto import id_documento

PALMERAS, ROBLES = "demo-palmeras", "demo-robles"
CONSORCIOS = {PALMERAS: "Edificio Las Palmeras (demo)", ROBLES: "Edificio Los Robles (demo)"}
PERIODOS = ("2026-07", "2026-08")

# sujeto_externo, nombre, plataforma, [(consorcio, rol, unidades)]
USUARIOS = [
    ("demo-admin-palmeras", "Ana Administradora (demo)", False, [(PALMERAS, "administrador", ())]),
    ("demo-prop-palmeras", "Pablo Propietario (demo)", False, [(PALMERAS, "propietario", (3,))]),
    ("demo-prop-robles", "Rita Propietaria (demo)", False, [(ROBLES, "propietario", (1,))]),
    ("demo-multi", "Marta Multiconsorcio (demo)", False, [(PALMERAS, "propietario", (5,)), (ROBLES, "administrador", ())]),
    ("demo-plataforma", "Equipo de plataforma (demo)", True, []),
]

# Lo que cambia de un edificio a otro
EDIFICIOS = {
    PALMERAS: {
        "mascotas": "Se permite la tenencia de mascotas pequeñas en las unidades, siempre que no molesten a los vecinos "
                    "y circulen con correa por los espacios comunes.",
        "ruidos": "Entre las 22 y las 8 horas no se pueden hacer ruidos molestos ni obras en las unidades.",
        "cocheras": "Cada cochera se usa solo para estacionar y no se puede guardar en ella ningún otro objeto.",
        "acta": "La asamblea aprobó por mayoría pintar el palier de todos los pisos durante el mes de septiembre.",
        "unidades": ["1-A", "1-B", "2-A", "2-B", "3-A"], "base": 100_000.0,
    },
    ROBLES: {
        "mascotas": "Queda prohibida la tenencia de animales en las unidades, salvo los de asistencia, por decisión del "
                    "reglamento de este edificio.",
        "ruidos": "Entre las 23 y las 7 horas no se pueden hacer ruidos molestos ni obras en las unidades.",
        "cocheras": "Las cocheras de cortesía se asignan por sorteo anual entre los propietarios interesados.",
        "acta": "La asamblea aprobó por mayoría cambiar la bomba de agua del edificio durante el mes de octubre.",
        "unidades": ["PB-A", "1-A", "1-B"], "base": 170_000.0,
    },
}


def _secciones(*filas: tuple[str, str, str, int]) -> list[Seccion]:
    return [Seccion(titulo=t, tema=tema, resumen=r, pagina_inicio=p, pagina_fin=p) for t, tema, r, p in filas]


def _guardar(cid: str, archivo: str, tipo: str, titulo: str, paginas: list[str], secciones: list[Seccion],
             liquidacion: dict | None = None) -> None:
    sha256 = hashlib.sha256(f"{cid}:{archivo}:{paginas}".encode()).hexdigest()
    guardar_documento(
        documento_id=id_documento(cid, sha256[:16]), nombre_archivo=archivo, ruta=f"semilla/{archivo}",
        clasificacion={"metodo": "texto", "confianza": 1.0, "total_paginas": len(paginas), "motivo": "semilla"},
        paginas=[{"numero": i, "texto": t, "metodo": "texto", "confianza": 100.0} for i, t in enumerate(paginas, start=1)],
        doc=DocumentoExtraido(metadatos=MetadatosDocumento(tipo_documento=tipo, titulo=titulo, consorcio=CONSORCIOS[cid],
                                                           resumen=f"{titulo}, inventado para la demo."),
                              secciones=secciones),
        costo={}, llamadas=[], segundos_total=0.0, liquidacion=liquidacion, consorcio_id=cid, sha256=sha256,
    )


def _liquidacion(cid: str, periodo: str, factor: float) -> dict:
    """Importes redondos: cada unidad paga lo mismo y el total sale de la suma."""
    edificio = EDIFICIOS[cid]
    cuota = edificio["base"] * factor
    unidades = edificio["unidades"]
    total = cuota * len(unidades)
    filas = [
        {"uf": i, "piso_depto": depto, "propietario": f"Persona Inventada {i}", "porcentual": round(100 / len(unidades), 2),
         "expensas_ordinarias": cuota, "total_expensa": cuota, "saldo_anterior": 0.0, "a_pagar": cuota, "pagina": 2}
        for i, depto in enumerate(unidades, start=1)
    ]
    return {
        "periodo": periodo, "primer_vencimiento": f"{periodo}-10", "segundo_vencimiento": f"{periodo}-20",
        "consorcio": CONSORCIOS[cid], "administracion": "Administración Inventada", "total_gastos": total,
        "filas_prorrateo": len(filas), "filas_no_parseadas": 0,
        "rubros": [{"numero": 1, "nombre": "ABONOS", "total": total * 0.6, "porcentaje": 60.0},
                   {"numero": 2, "nombre": "MANTENIMIENTO", "total": total * 0.4, "porcentaje": 40.0}],
        "gastos": [{"rubro": "ABONOS", "proveedor": "LIMPIEZA INVENTADA", "concepto": "Limpieza del edificio",
                    "importe": total * 0.6, "fecha": f"{periodo}-05", "comprobante": "0001", "pagina": 1},
                   {"rubro": "MANTENIMIENTO", "proveedor": "ASCENSORES INVENTADOS", "concepto": "Service de ascensor",
                    "importe": total * 0.4, "fecha": f"{periodo}-12", "comprobante": "0002", "pagina": 1}],
        "unidades": filas,
    }


def _documentos(cid: str) -> None:
    e = EDIFICIOS[cid]
    nombre = CONSORCIOS[cid]
    _guardar(
        cid, "reglamento-demo.pdf", "reglamento_copropiedad", f"Reglamento de {nombre}",
        [f"REGLAMENTO DE {nombre.upper()}. Artículo 1, mascotas. {e['mascotas']} Artículo 2, ruidos. {e['ruidos']}",
         f"Artículo 3, cocheras. {e['cocheras']}"],
        _secciones(("Artículo 1: mascotas", "mascotas", e["mascotas"], 1), ("Artículo 2: ruidos", "ruidos", e["ruidos"], 1),
                   ("Artículo 3: cocheras", "cocheras", e["cocheras"], 2)),
    )
    _guardar(cid, "acta-demo.pdf", "acta_asamblea", f"Acta de asamblea de {nombre}",
             [f"ACTA DE ASAMBLEA ORDINARIA DE {nombre.upper()}. {e['acta']}"],
             _secciones(("Obras aprobadas en asamblea", "obras", e["acta"], 1)))
    for periodo, factor in zip(PERIODOS, (1.0, 1.1), strict=True):
        liq = _liquidacion(cid, periodo, factor)
        _guardar(cid, f"liquidacion-{periodo}-demo.pdf", "liquidacion_expensas", f"Liquidación {periodo} de {nombre}",
                 [f"LIQUIDACIÓN DE EXPENSAS DEL PERÍODO {periodo} DE {nombre.upper()}.", "PRORRATEO POR UNIDAD."],
                 _secciones(("Rubros de la liquidación", "expensas", f"Rubros de gastos del período {periodo}.", 1)), liq)


def _asegurar_membresia(s: Session, usuario_id: int, cid: str, rol: str, unidades: tuple[int, ...]) -> None:
    membresia = s.scalars(
        select(Membresia).where(Membresia.usuario_id == usuario_id, Membresia.consorcio_id == cid)
    ).first()
    if membresia is None:
        membresia = Membresia(usuario_id=usuario_id, consorcio_id=cid, rol=rol)
        s.add(membresia)
    membresia.rol = rol
    actuales = {u.uf for u in membresia.unidades}
    membresia.unidades.extend(MembresiaUnidad(uf=uf) for uf in sorted(set(unidades) - actuales))


def _usuarios() -> None:
    with Session(engine()) as s:
        for sujeto, nombre, plataforma, membresias in USUARIOS:
            usuario = s.scalars(select(Usuario).where(Usuario.sujeto_externo == sujeto)).first()
            if usuario is None:
                usuario = Usuario(sujeto_externo=sujeto)
                s.add(usuario)
            usuario.nombre, usuario.plataforma = nombre, plataforma
            s.flush()
            for cid, rol, unidades in membresias:
                _asegurar_membresia(s, usuario.id, cid, rol, unidades)
        s.commit()


def sembrar_demo() -> dict:
    crear_tablas()
    for cid, nombre in CONSORCIOS.items():
        asegurar_consorcio(cid, nombre)
        _documentos(cid)
    _usuarios()
    return {"consorcios": list(CONSORCIOS), "usuarios": [u[0] for u in USUARIOS]}
