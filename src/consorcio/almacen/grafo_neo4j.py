"""Grafo de contexto en Neo4j.

Nodos:   (:Documento) (:Persona) (:Organizacion) (:Direccion) (:Evento) (:Unidad)
         (:Seccion) (:Tema) (:Consorcio)
Aristas: (Documento)-[:MENCIONA {pagina, rol}]->(Persona|Organizacion)
         (Documento)-[:REFIERE_A {tipo}]->(Direccion)
         (Documento)-[:OCURRE {tipo}]->(Evento)
         (Documento)-[:DESCRIBE]->(Unidad)
         (Documento)-[:TIENE_SECCION]->(Seccion)-[:TRATA]->(Tema)
         (Documento)-[:PERTENECE_A]->(Consorcio)
         (Persona)-[:DOMICILIO_EN]->(Direccion)   cuando el texto coincide

No se guardan datos sensibles (DNI, CUIT, teléfonos...): esos quedan solo en Postgres.
Las entidades se unifican por nombre normalizado (MERGE), así el mismo escribano o
la misma dirección en dos documentos son un solo nodo.
"""
from __future__ import annotations

import unicodedata

from neo4j import GraphDatabase

from ..config import config
from ..ingesta.esquemas import DocumentoExtraido

_driver = None


def driver():
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password))
    return _driver


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


RESTRICCIONES = [
    "CREATE CONSTRAINT doc_id IF NOT EXISTS FOR (d:Documento) REQUIRE d.id IS UNIQUE",
    "CREATE CONSTRAINT persona_clave IF NOT EXISTS FOR (p:Persona) REQUIRE p.clave IS UNIQUE",
    "CREATE CONSTRAINT org_clave IF NOT EXISTS FOR (o:Organizacion) REQUIRE o.clave IS UNIQUE",
    "CREATE CONSTRAINT dir_clave IF NOT EXISTS FOR (d:Direccion) REQUIRE d.clave IS UNIQUE",
    "CREATE CONSTRAINT unidad_clave IF NOT EXISTS FOR (u:Unidad) REQUIRE u.clave IS UNIQUE",
    "CREATE CONSTRAINT tema_nombre IF NOT EXISTS FOR (t:Tema) REQUIRE t.nombre IS UNIQUE",
    "CREATE CONSTRAINT consorcio_clave IF NOT EXISTS FOR (c:Consorcio) REQUIRE c.clave IS UNIQUE",
]


def preparar() -> None:
    with driver().session() as s:
        for q in RESTRICCIONES:
            s.run(q)


def guardar_documento(documento_id: str, doc: DocumentoExtraido, consorcio_id: str | None = None) -> dict:
    preparar()
    m = doc.metadatos
    consorcio_id = consorcio_id or documento_id.split(":", 1)[0]
    with driver().session() as s:
        # Reemplazo idempotente: borramos lo que colgaba de este documento
        s.run(
            "MATCH (d:Documento {id: $id}) OPTIONAL MATCH (d)-[:TIENE_SECCION]->(sec:Seccion) "
            "DETACH DELETE sec, d",
            id=documento_id,
        )
        s.run(
            "CREATE (d:Documento {id: $id, consorcio: $cid, titulo: $titulo, tipo: $tipo, fecha: $fecha, resumen: $resumen})",
            id=documento_id, cid=consorcio_id, titulo=m.titulo, tipo=m.tipo_documento, fecha=m.fecha_documento,
            resumen=m.resumen,
        )
        # El consorcio es la base a la que pertenece el documento (slug), no el nombre que leyó el modelo
        s.run(
            "MATCH (d:Documento {id: $id}) MERGE (c:Consorcio {clave: $clave}) "
            "ON CREATE SET c.nombre = $nombre MERGE (d)-[:PERTENECE_A]->(c)",
            id=documento_id, clave=consorcio_id, nombre=m.consorcio or consorcio_id,
        )
        for p in doc.personas:
            s.run(
                "MATCH (d:Documento {id: $id}) MERGE (p:Persona {clave: $clave}) "
                "ON CREATE SET p.nombre = $nombre "
                "SET p.nacionalidad = coalesce($nac, p.nacionalidad) "
                "MERGE (d)-[r:MENCIONA]->(p) SET r.rol = $rol, r.pagina = $pagina",
                id=documento_id, clave=normalizar(p.nombre), nombre=p.nombre, nac=p.nacionalidad,
                rol=p.rol, pagina=p.pagina,
            )
            if p.domicilio:
                s.run(
                    "MATCH (p:Persona {clave: $pc}) MERGE (dir:Direccion {clave: $dc}) "
                    "ON CREATE SET dir.texto = $texto, dir.tipo = 'domicilio_persona' "
                    "MERGE (p)-[:DOMICILIO_EN]->(dir)",
                    pc=normalizar(p.nombre), dc=normalizar(p.domicilio), texto=p.domicilio,
                )
        for o in doc.organizaciones:
            s.run(
                "MATCH (d:Documento {id: $id}) MERGE (o:Organizacion {clave: $clave}) "
                "ON CREATE SET o.nombre = $nombre SET o.tipo = coalesce($tipo, o.tipo) "
                "MERGE (d)-[r:MENCIONA]->(o) SET r.rol = $rol, r.pagina = $pagina",
                id=documento_id, clave=normalizar(o.nombre), nombre=o.nombre, tipo=o.tipo,
                rol=o.rol, pagina=o.pagina,
            )
        for dr in doc.direcciones:
            s.run(
                "MATCH (d:Documento {id: $id}) MERGE (dir:Direccion {clave: $clave}) "
                "ON CREATE SET dir.texto = $texto SET dir.tipo = $tipo, dir.calle = $calle, "
                "dir.numero = $numero, dir.localidad = $localidad "
                "MERGE (d)-[r:REFIERE_A]->(dir) SET r.tipo = $tipo, r.pagina = $pagina",
                id=documento_id, clave=normalizar(dr.texto), texto=dr.texto, tipo=dr.tipo, calle=dr.calle,
                numero=dr.numero, localidad=dr.localidad, pagina=dr.pagina,
            )
        for e in doc.eventos:
            s.run(
                "MATCH (d:Documento {id: $id}) "
                "CREATE (ev:Evento {fecha: $fecha, tipo: $tipo, descripcion: $desc, pagina: $pagina}) "
                "CREATE (d)-[:OCURRE {tipo: $tipo}]->(ev)",
                id=documento_id, fecha=e.fecha, tipo=e.tipo, desc=e.descripcion, pagina=e.pagina,
            )
        for u in doc.unidades:
            s.run(
                "MATCH (d:Documento {id: $id}) MERGE (u:Unidad {clave: $clave}) "
                "ON CREATE SET u.identificador = $ident "
                "SET u.piso = coalesce($piso, u.piso), u.destino = coalesce($destino, u.destino), "
                "u.superficie_m2 = coalesce($sup, u.superficie_m2), u.porcentual = coalesce($porc, u.porcentual) "
                "MERGE (d)-[r:DESCRIBE]->(u) SET r.pagina = $pagina",
                id=documento_id, clave=normalizar(u.identificador), ident=u.identificador, piso=u.piso,
                destino=u.destino, sup=u.superficie_m2, porc=u.porcentual, pagina=u.pagina,
            )
        for sec in doc.secciones:
            s.run(
                "MATCH (d:Documento {id: $id}) "
                "CREATE (s:Seccion {titulo: $titulo, resumen: $resumen, pagina_inicio: $pi, pagina_fin: $pf}) "
                "CREATE (d)-[:TIENE_SECCION]->(s) "
                "MERGE (t:Tema {nombre: $tema}) CREATE (s)-[:TRATA]->(t)",
                id=documento_id, titulo=sec.titulo, resumen=sec.resumen, pi=sec.pagina_inicio,
                pf=sec.pagina_fin, tema=normalizar(sec.tema) or "general",
            )
        # borrar eventos huérfanos de versiones anteriores de este documento
        s.run("MATCH (ev:Evento) WHERE NOT (ev)<-[:OCURRE]-() DELETE ev")
        conteo = s.run(
            "MATCH (d:Documento {id: $id})-[r]->(n) RETURN type(r) AS rel, count(n) AS n", id=documento_id
        )
        return {"documento_id": documento_id, "aristas": {row["rel"]: row["n"] for row in conteo}}


def migrar_consorcios(cambios: list[dict]) -> int:
    """Replica en Neo4j el cambio de id de `postgres.migrar_consorcios` (idempotente)."""
    preparar()
    n = 0
    with driver().session() as s:
        for c in cambios:
            r = s.run(
                "MATCH (d:Documento {id: $viejo}) SET d.id = $nuevo, d.consorcio = $cid "
                "WITH d MERGE (c:Consorcio {clave: $cid}) ON CREATE SET c.nombre = $cid "
                "MERGE (d)-[:PERTENECE_A]->(c) RETURN count(d) AS n",
                viejo=c["viejo"], nuevo=c["nuevo"], cid=c["consorcio"],
            ).single()
            n += r["n"] if r else 0
    return n


def resumen_grafo() -> dict:
    with driver().session() as s:
        filas = s.run("MATCH (n) RETURN labels(n)[0] AS etiqueta, count(n) AS n ORDER BY etiqueta")
        return {row["etiqueta"]: row["n"] for row in filas}
