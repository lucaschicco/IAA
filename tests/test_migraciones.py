"""Migraciones de Alembic y comportamiento de `crear_tablas`. Datos inventados.

La fixture borra el schema entero: solo corre contra una base descartable (lista blanca) y deja la
base limpia al terminar."""
from __future__ import annotations

import logging
import threading

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from consorcio import cli
from consorcio.almacen import postgres
from consorcio.config import config
from consorcio.consulta.buscador import buscar_secciones
from consorcio.identidad import resolver_identidad

BASES_DESCARTABLES = {"consorcio_test"}
TABLAS_BASE = [
    "consorcios", "chats", "documentos", "paginas", "personas", "organizaciones", "direcciones", "eventos",
    "unidades", "montos", "secciones", "datos_sensibles", "llamadas_modelo", "liquidaciones",
    "liquidacion_rubros", "liquidacion_gastos", "liquidacion_unidades", "consultas",
]


def _hay_postgres() -> bool:
    try:
        with postgres.engine().connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _hay_postgres(), reason="sin Postgres")


def _vaciar_base() -> None:
    postgres.engine().dispose()
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))


@pytest.fixture
def base_vacia():
    if make_url(config.postgres_url).database not in BASES_DESCARTABLES:
        pytest.skip("la base configurada no es descartable: este test borra el schema")
    _vaciar_base()
    yield
    _vaciar_base()


def _alembic():
    return postgres._config_alembic()


def _ejecutar(sql: str) -> None:
    with postgres.engine().begin() as con:
        con.execute(text(sql))


def _escalar(sql: str):
    with postgres.engine().connect() as con:
        return con.scalar(text(sql))


def _existe(tabla: str) -> bool:
    return _escalar(f"SELECT to_regclass('{tabla}') IS NOT NULL")


def _conteos() -> dict[str, int]:
    return {t: _escalar(f"SELECT count(*) FROM {t}") for t in TABLAS_BASE}


def _cargar_datos_inventados() -> None:
    """Una fila por tabla, con SQL directo: los modelos ya no corresponden a este esquema."""
    sentencias = [
        "INSERT INTO consorcios (id, nombre, creado_en) VALUES ('edificio-a', 'Edificio A', now())",
        "INSERT INTO chats (chat_id, consorcio_id, creado_en) VALUES (1, 'edificio-a', now())",
        """INSERT INTO documentos (id, consorcio_id, nombre_archivo, ruta, tipo_documento, titulo, resumen,
               total_paginas, metodo_extraccion, confianza_clasificador, clasificacion, costo, segundos_total,
               estado, activo, creado_en)
           VALUES ('edificio-a:h1', 'edificio-a', 'a.pdf', 'x', 'acta', 'Acta', 'r', 1, 'texto', 1, '{}', '{}', 1,
                   'extraido', true, now())""",
        "INSERT INTO paginas (documento_id, numero, metodo, chars, texto) VALUES ('edificio-a:h1', 1, 'texto', 1, 'x')",
        "INSERT INTO personas (id, documento_id, nombre, rol) VALUES (1, 'edificio-a:h1', 'Ana Inventada', 'otro')",
        "INSERT INTO organizaciones (id, documento_id, nombre) VALUES (1, 'edificio-a:h1', 'Org Inventada')",
        "INSERT INTO direcciones (documento_id, texto, tipo) VALUES ('edificio-a:h1', 'Calle 1', 'otra')",
        "INSERT INTO eventos (documento_id, fecha, tipo, descripcion) VALUES ('edificio-a:h1', '2026', 'otro', 'x')",
        "INSERT INTO unidades (documento_id, identificador) VALUES ('edificio-a:h1', '1-A')",
        "INSERT INTO montos (documento_id, valor, moneda, concepto) VALUES ('edificio-a:h1', 1, 'ARS', 'x')",
        "INSERT INTO secciones (documento_id, titulo, tema, resumen) VALUES ('edificio-a:h1', 'Art 1', 'tema', 'r')",
        """INSERT INTO datos_sensibles (documento_id, persona_id, tipo, valor, sensible)
           VALUES ('edificio-a:h1', 1, 'dni', '00.000.000', true)""",
        """INSERT INTO llamadas_modelo (documento_id, nodo, modelo, tokens_entrada, tokens_salida, segundos, creado_en)
           VALUES ('edificio-a:h1', 'n', 'fake', 0, 0, 0, now())""",
        """INSERT INTO liquidaciones (documento_id, periodo, filas_prorrateo, filas_no_parseadas)
           VALUES ('edificio-a:h1', '2026-07', 1, 0)""",
        "INSERT INTO liquidacion_rubros (documento_id, nombre) VALUES ('edificio-a:h1', 'Rubro')",
        "INSERT INTO liquidacion_gastos (documento_id, rubro, concepto, importe) VALUES ('edificio-a:h1', 'R', 'c', 1)",
        """INSERT INTO liquidacion_unidades (documento_id, uf, piso_depto, propietario, porcentual, importes_previos,
               otros_conceptos)
           VALUES ('edificio-a:h1', 1, '1-A', 'Ana Inventada', 1, '[]', '[]')""",
        """INSERT INTO consultas (pregunta, consorcio_id, verificada, derivada, metodo_busqueda, k, proveedor, modelo,
               skill_version, tokens_entrada, tokens_salida, segundos, origen, creado_en)
           VALUES ('¿?', 'edificio-a', false, true, 'tsvector', 3, 'fake', 'fake', '0.1.0', 0, 0, 0, 'cli', now())""",
    ]
    for sql in sentencias:
        _ejecutar(sql)


def _base_existente(pasada_por_migrar_consorcios: bool = False) -> None:
    """Base como la de un despliegue anterior: esquema de 7d86ca2, con datos y sin `alembic_version`."""
    command.upgrade(_alembic(), "0001_base")
    _ejecutar("DROP TABLE alembic_version")
    _cargar_datos_inventados()
    if pasada_por_migrar_consorcios:  # lo que deja `migrar_consorcios`: FKs diferibles, quizá sin este índice
        _ejecutar("DROP INDEX ix_consultas_consorcio_id")
        for nombre, tabla in _filas_fk():
            _ejecutar(f"ALTER TABLE {tabla} ALTER CONSTRAINT {nombre} DEFERRABLE INITIALLY DEFERRED")


def _filas_fk() -> list[tuple[str, str]]:
    with postgres.engine().connect() as con:
        return [tuple(f) for f in con.execute(text(
            "SELECT conname, conrelid::regclass::text FROM pg_constraint "
            "WHERE confrelid IN ('documentos'::regclass, 'liquidaciones'::regclass)"
        )).all()]


@pytest.mark.parametrize("pasada_por_migrar", [False, True], ids=["esquema-7d86ca2", "tras-migrar_consorcios"])
def test_stamp_y_upgrade_conservan_los_datos(base_vacia, pasada_por_migrar):
    _base_existente(pasada_por_migrar)
    antes = _conteos()
    assert all(antes.values())

    command.stamp(_alembic(), "0001_base")
    command.upgrade(_alembic(), "head")

    assert _conteos() == antes
    assert _escalar("SELECT count(*) FROM consultas WHERE usuario_id IS NULL") == antes["consultas"]
    assert _existe("usuarios") and _existe("membresias") and _existe("unidades_funcionales")
    assert postgres.listar_documentos("edificio-a")[0]["archivo"] == "a.pdf"  # crear_tablas ya no frena
    command.check(_alembic())  # también tras `migrar_consorcios`: el --autogenerate no propone nada


def _base_anterior_al_26_10() -> None:
    """Sin consorcios, chats ni `consorcio_id`, con ids de documento sin prefijo."""
    command.upgrade(_alembic(), "0001_base")
    for sql in (
        "DROP TABLE alembic_version",
        "DROP TABLE chats",
        "ALTER TABLE documentos DROP COLUMN consorcio_id",
        "ALTER TABLE consultas DROP COLUMN consorcio_id",
        "DROP TABLE consorcios",
        """INSERT INTO documentos (id, nombre_archivo, ruta, tipo_documento, titulo, resumen, total_paginas,
               metodo_extraccion, confianza_clasificador, clasificacion, costo, segundos_total, estado, activo,
               creado_en)
           VALUES ('h1', 'a.pdf', 'x', 'acta', 'Acta', 'r', 1, 'texto', 1, '{}', '{}', 1, 'extraido', true, now())""",
        "INSERT INTO paginas (documento_id, numero, metodo, chars, texto) VALUES ('h1', 1, 'texto', 1, 'x')",
        """INSERT INTO liquidaciones (documento_id, periodo, filas_prorrateo, filas_no_parseadas)
           VALUES ('h1', '2026-07', 1, 0)""",
        "INSERT INTO liquidacion_rubros (documento_id, nombre) VALUES ('h1', 'Rubro')",
    ):
        _ejecutar(sql)


def test_base_anterior_al_26_10_queda_igual_a_los_modelos(base_vacia):
    _base_anterior_al_26_10()
    with pytest.raises(RuntimeError, match="consorcio migrar"):
        postgres.actualizar_esquema()
    assert not _existe("alembic_version")

    postgres.migrar_consorcios("edificio-a")
    assert _escalar("SELECT count(*) FROM pg_constraint WHERE condeferrable") == 0
    assert postgres.actualizar_esquema() == "0001_base"
    command.check(_alembic())
    assert [d["id"] for d in postgres.listar_documentos("edificio-a")] == ["edificio-a:h1"]

    postgres.migrar_consorcios("edificio-a")  # idempotente: repetirla sobre head no vuelve a ensuciar el esquema
    command.check(_alembic())


def test_actualizar_base_respeta_la_version_de_un_volcado_reciente(base_vacia, capsys):
    command.upgrade(_alembic(), "head")  # un volcado de `make respaldar` trae `alembic_version`
    _cargar_datos_inventados()
    antes = _conteos()

    assert cli.main(["actualizar-base"]) == 0

    assert '"marcada_en": null' in capsys.readouterr().out
    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()
    assert _conteos() == antes


def test_actualizar_base_sube_un_volcado_de_una_revision_anterior(base_vacia):
    command.upgrade(_alembic(), "0003_crudos")
    _cargar_datos_inventados()
    antes = _conteos()
    assert postgres.actualizar_esquema() is None
    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()
    assert _conteos() == antes


@pytest.mark.parametrize("pasada_por_migrar", [False, True], ids=["esquema-7d86ca2", "tras-migrar_consorcios"])
def test_actualizar_base_marca_una_base_sin_version(base_vacia, pasada_por_migrar):
    _base_existente(pasada_por_migrar)
    antes = _conteos()
    assert postgres.actualizar_esquema() == "0001_base"
    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()
    assert _conteos() == antes
    command.check(_alembic())


@pytest.mark.parametrize("revision", ["0002_usuarios", "0003_crudos"])
def test_actualizar_base_destraba_un_stamp_de_mas(base_vacia, revision):
    """Quien corrió a mano `alembic stamp 0001_base` sobre un volcado que ya tenía versión."""
    command.upgrade(_alembic(), revision)
    _cargar_datos_inventados()
    antes = _conteos()
    command.stamp(_alembic(), "0001_base")
    with pytest.raises(RuntimeError, match="consorcio actualizar-base"):
        postgres.crear_tablas()

    assert postgres.actualizar_esquema() == revision

    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()
    assert _conteos() == antes
    command.check(_alembic())


def test_actualizar_base_crea_una_base_vacia(base_vacia):
    assert postgres.actualizar_esquema() is None
    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()


def test_downgrade_y_nuevo_upgrade_no_pierden_documentos(base_vacia):
    _base_existente()
    command.stamp(_alembic(), "0001_base")
    command.upgrade(_alembic(), "head")
    antes = _conteos()

    command.downgrade(_alembic(), "0001_base")
    assert not _existe("usuarios")
    assert _escalar(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'consultas' AND column_name = 'usuario_id'"
    ) == 0
    assert _conteos() == antes

    command.upgrade(_alembic(), "head")
    assert _conteos() == antes


TABLAS_0007 = ["conversaciones", "mensajes", "turnos", "solicitudes", "gastos_modelo", "cargas"]


def _base_con_gastos_historicos() -> None:
    """En 0006: un documento con llamadas reales y fake, consultas con y sin consumo, y un usuario."""
    command.upgrade(_alembic(), "0006_fechas_texto")
    _cargar_datos_inventados()  # trae una llamada y una consulta fake: no pasan al libro
    llamada = """INSERT INTO llamadas_modelo (documento_id, nodo, modelo, tokens_entrada, tokens_salida, segundos,
                     costo_usd, creado_en)
                 VALUES ('edificio-a:h1', 'estructurar', 'claude-haiku-5-5', 100, 10, 1, {costo},
                         '2026-03-04 05:06:07+00')"""
    consulta = """INSERT INTO consultas (pregunta, consorcio_id, verificada, derivada, metodo_busqueda, k, proveedor,
                      modelo, skill_version, tokens_entrada, tokens_salida, costo_usd, segundos, origen, creado_en)
                  VALUES ('¿?', 'edificio-a', false, false, 'tsvector', 3, 'p', '{modelo}', '0.1.0', {tokens}, 0,
                          {costo}, 0, 'cli', '2026-03-05 05:06:07+00')"""
    for sql in (
        llamada.format(costo="0.5"), llamada.format(costo="NULL"),
        consulta.format(modelo="claude-haiku-5-5", tokens=50, costo="0.25"),
        consulta.format(modelo="modelo-sin-precio", tokens=70, costo="NULL"),
        consulta.format(modelo="claude-haiku-5-5", tokens=0, costo="NULL"),  # sin consumo: no pasa
        consulta.format(modelo="fake", tokens=15, costo="0"),  # fake: no pasa
        "INSERT INTO usuarios (sujeto_externo, nombre) VALUES ('sujeto', 'Ana Inventada')",
    ):
        _ejecutar(sql)


def test_0007_rellena_el_libro_de_gastos(base_vacia):
    _base_con_gastos_historicos()

    command.upgrade(_alembic(), "0007_agente")

    with postgres.engine().connect() as con:
        filas = [tuple(f) for f in con.execute(text(
            "SELECT consorcio_id, nodo, modelo, tokens_entrada, costo_usd, creado_en::date::text FROM gastos_modelo "
            "ORDER BY nodo, tokens_entrada, costo_usd NULLS LAST"
        )).all()]
    assert filas == [
        ("edificio-a", "estructurar", "claude-haiku-5-5", 100, 0.5, "2026-03-04"),
        ("edificio-a", "estructurar", "claude-haiku-5-5", 100, None, "2026-03-04"),
        ("edificio-a", "responder", "claude-haiku-5-5", 50, 0.25, "2026-03-05"),
        ("edificio-a", "responder", "modelo-sin-precio", 70, None, "2026-03-05"),
    ]
    assert _escalar("SELECT plataforma FROM usuarios") is False
    assert _escalar("SELECT ultima_actividad FROM usuarios") is None
    command.upgrade(_alembic(), "head")
    command.check(_alembic())


def test_0007_downgrade_y_upgrade_limpios(base_vacia):
    _base_con_gastos_historicos()
    command.upgrade(_alembic(), "0007_agente")
    antes = _conteos()

    command.downgrade(_alembic(), "0006_fechas_texto")

    assert not any(_existe(t) for t in TABLAS_0007)
    assert _escalar(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'usuarios' "
        "AND column_name IN ('plataforma', 'ultima_actividad')"
    ) == 0
    assert _conteos() == antes and _escalar("SELECT count(*) FROM usuarios") == 1

    command.upgrade(_alembic(), "head")

    assert all(_existe(t) for t in TABLAS_0007)
    assert _conteos() == antes and _escalar("SELECT count(*) FROM gastos_modelo") == 4
    command.check(_alembic())


def test_0007_restricciones_cerradas(base_vacia):
    command.upgrade(_alembic(), "0007_agente")
    _ejecutar("INSERT INTO consorcios (id, nombre, creado_en) VALUES ('a', 'A', now())")
    _ejecutar("INSERT INTO usuarios (id, sujeto_externo) VALUES (1, 's')")
    solicitud = """INSERT INTO solicitudes (tipo, estado, consorcio_id, autor_id, motivo, mensaje, titulo, detalle, creado_en)
                   VALUES ('{tipo}', '{estado}', 'a', 1, '{motivo}', 'm', 't', 'd', now())"""
    turno = """INSERT INTO turnos (usuario_id, consorcio_id, rol, canal, origen, resultado, segundos, modelo, skill_version,
                   feedback, creado_en)
               VALUES (1, 'a', 'propietario', 'web', 'usuario', '{resultado}', 0, 'fake', '0.1.0', {feedback}, now())"""
    _ejecutar(solicitud.format(tipo="derivacion", estado="resuelta", motivo="sin_respaldo"))
    _ejecutar(solicitud.format(tipo="reclamo", estado="editada", motivo="reclamo"))
    _ejecutar(turno.format(resultado="respondida", feedback=1))
    assert _escalar("SELECT secciones_usadas::text FROM turnos") == "[]"
    for invalida in (
        solicitud.format(tipo="derivacion", estado="aprobada", motivo="sin_respaldo"),
        solicitud.format(tipo="reclamo", estado="resuelta", motivo="reclamo"),
        solicitud.format(tipo="otra", estado="pendiente", motivo="reclamo"),
        solicitud.format(tipo="reclamo", estado="pendiente", motivo="texto libre"),
        turno.format(resultado="otro", feedback="NULL"),
        turno.format(resultado="respondida", feedback=0),
    ):
        with pytest.raises(IntegrityError):
            _ejecutar(invalida)


def test_0007_mensajes_caen_con_la_conversacion(base_vacia):
    command.upgrade(_alembic(), "0007_agente")
    _ejecutar("INSERT INTO consorcios (id, nombre, creado_en) VALUES ('a', 'A', now())")
    _ejecutar("INSERT INTO usuarios (id, sujeto_externo) VALUES (1, 's')")
    _ejecutar("INSERT INTO conversaciones (id, usuario_id, consorcio_id, creado_en, actualizada_en) VALUES ('c', 1, 'a', now(), now())")
    _ejecutar("INSERT INTO mensajes (conversacion_id, rol, texto, creado_en) VALUES ('c', 'usuario', 'hola', now())")
    _ejecutar("DELETE FROM conversaciones")
    assert _escalar("SELECT count(*) FROM mensajes") == 0


def test_alembic_check_no_detecta_diferencias_con_los_modelos(base_vacia):
    command.upgrade(_alembic(), "head")
    command.check(_alembic())  # CommandError si los modelos y las migraciones no coinciden


@pytest.mark.parametrize("llamada", [
    postgres.listar_consorcios,
    lambda: postgres.listar_documentos("edificio-a"),
    postgres.listar_documentos_de_todos_los_consorcios,
    lambda: postgres.consultar_expensas(consorcio_id="edificio-a"),
    lambda: postgres.listar_consultas("edificio-a"),
    lambda: buscar_secciones("cocheras", consorcio_id="edificio-a"),
    lambda: postgres.consorcio_de_chat(99),
    lambda: resolver_identidad("sujeto", "edificio-a"),
], ids=["consorcios", "documentos", "documentos-todos", "expensas", "consultas", "buscar", "chat", "identidad"])
def test_base_con_tablas_sin_version_frena_con_los_comandos(base_vacia, llamada):
    _base_existente()
    with pytest.raises(RuntimeError) as error:
        llamada()
    assert "consorcio actualizar-base" in str(error.value)
    assert not _existe("usuarios")
    assert not _existe("alembic_version")


def test_base_en_revision_anterior_frena(base_vacia):
    command.upgrade(_alembic(), "0001_base")
    with pytest.raises(RuntimeError, match="0001_base.*consorcio actualizar-base"):
        postgres.crear_tablas()
    assert not _existe("usuarios")


def test_base_vacia_queda_en_head_y_no_apaga_el_logging(base_vacia, caplog):
    log = logging.getLogger("consorcio.ingesta.grafo")  # un logger que ya existe, como los de la app
    handlers = list(logging.getLogger().handlers)

    postgres.crear_tablas()

    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()
    assert _existe("documentos") and _existe("usuarios")
    assert not log.disabled
    assert logging.getLogger().handlers == handlers
    with caplog.at_level(logging.INFO, logger=log.name):
        log.info("sigue andando")
    assert "sigue andando" in caplog.text

    postgres.crear_tablas()  # en head no hace nada


def test_dos_procesos_sobre_base_vacia_no_chocan(base_vacia):
    errores: list[Exception] = []

    def correr():
        try:
            postgres.crear_tablas()
        except Exception as e:
            errores.append(e)

    hilos = [threading.Thread(target=correr) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=60)
    assert errores == []
    assert _escalar("SELECT version_num FROM alembic_version") == postgres._revision_head()


def _base_con_membresias_0007() -> None:
    """Dos usuarios con el modelo anterior: un propietario con dos UF y un administrador con un turno."""
    command.upgrade(_alembic(), "0007_agente")
    for sql in (
        "INSERT INTO consorcios (id, nombre, creado_en) VALUES ('a', 'A', now())",
        "INSERT INTO usuarios (id, sujeto_externo) VALUES (1, 'prop'), (2, 'admin')",
        # Sin ids explícitos: la migración inserta membresías y usa la secuencia
        "INSERT INTO membresias (usuario_id, consorcio_id, rol) VALUES (1, 'a', 'propietario'), "
        "(2, 'a', 'administrador')",
        "INSERT INTO membresia_unidades (membresia_id, uf) SELECT id, uf FROM membresias, (VALUES (3), (7)) v(uf) "
        "WHERE usuario_id = 1",
        "INSERT INTO turnos (usuario_id, consorcio_id, rol, canal, origen, resultado, segundos, modelo, "
        "skill_version, creado_en) VALUES (2, 'a', 'administrador', 'web', 'usuario', 'respondida', 1.0, 'm', 'v', now())",
    ):
        _ejecutar(sql)


def test_0008_pasa_las_membresias_al_modelo_del_login(base_vacia):
    _base_con_membresias_0007()

    command.upgrade(_alembic(), "0008_login")

    assert not _existe("membresia_unidades") and _existe("invitaciones") and _existe("accesos")
    with postgres.engine().connect() as con:
        filas = sorted(tuple(f) for f in con.execute(text(
            "SELECT m.usuario_id, m.rol, u.uf FROM membresias m "
            "LEFT JOIN unidades_funcionales u ON u.id = m.unidad_id")).all())
    assert filas == [(1, "propietario", 3), (1, "propietario", 7), (2, "administracion", None)]
    assert _escalar("SELECT rol FROM turnos") == "administracion"
    command.upgrade(_alembic(), "head")  # el código solo lee una base en head
    assert resolver_identidad("prop", "a").unidades == (3, 7)
    assert resolver_identidad("admin", "a").es_administracion
    command.check(_alembic())


def test_0009_turnos_anteriores_quedan_sin_certeza_ni_mensajes(base_vacia):
    _base_con_membresias_0007()
    command.upgrade(_alembic(), "0009_calidad")

    assert _existe("revisiones")
    assert _escalar("SELECT certeza IS NULL AND mensaje_asistente_id IS NULL FROM turnos") is True
    assert _escalar("SELECT senales_certeza::text FROM turnos") == "{}"
    command.upgrade(_alembic(), "head")  # `check` compara contra head
    command.check(_alembic())


def test_0009_downgrade_y_upgrade_limpios(base_vacia):
    _base_con_membresias_0007()
    command.upgrade(_alembic(), "0009_calidad")
    _ejecutar("INSERT INTO revisiones (turno_id, consorcio_id, revisor_id, veredicto, creado_en, actualizado_en) "
              "SELECT id, consorcio_id, usuario_id, 'correcta', now(), now() FROM turnos")

    command.downgrade(_alembic(), "0008_login")

    assert not _existe("revisiones")
    assert _escalar(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'turnos' "
        "AND column_name IN ('certeza', 'senales_certeza', 'mensaje_usuario_id', 'mensaje_asistente_id')"
    ) == 0
    assert _escalar("SELECT count(*) FROM turnos") == 1
    command.upgrade(_alembic(), "head")
    command.check(_alembic())


def test_0009_motivo_solo_si_la_respuesta_no_fue_correcta(base_vacia):
    _base_con_membresias_0007()
    command.upgrade(_alembic(), "0009_calidad")
    insertar = ("INSERT INTO revisiones (turno_id, consorcio_id, revisor_id, veredicto, motivo, creado_en, actualizado_en) "
                "SELECT id, consorcio_id, usuario_id, '{}', {}, now(), now() FROM turnos")
    for veredicto, motivo in (("correcta", "'incompleta'"), ("incorrecta", "NULL"), ("dudosa", "NULL"),
                              ("parcial", "'otro'")):
        with pytest.raises(IntegrityError):
            _ejecutar(insertar.format(veredicto, motivo))
    _ejecutar(insertar.format("parcial", "'incompleta'"))
    with pytest.raises(IntegrityError):  # una revisión por turno
        _ejecutar(insertar.format("correcta", "NULL"))


def test_0008_downgrade_y_upgrade_limpios(base_vacia):
    _base_con_membresias_0007()
    command.upgrade(_alembic(), "0008_login")

    command.downgrade(_alembic(), "0007_agente")

    with postgres.engine().connect() as con:
        filas = sorted(tuple(f) for f in con.execute(text(
            "SELECT m.usuario_id, m.rol, mu.uf FROM membresias m "
            "LEFT JOIN membresia_unidades mu ON mu.membresia_id = m.id")).all())
    assert filas == [(1, "propietario", 3), (1, "propietario", 7), (2, "administrador", None)]
    assert _escalar("SELECT rol FROM turnos") == "administrador"

    command.upgrade(_alembic(), "head")
    assert resolver_identidad("prop", "a").unidades == (3, 7)
    command.check(_alembic())


_SOLICITUD = ("INSERT INTO solicitudes (tipo, estado, consorcio_id, autor_id, motivo, mensaje, titulo, detalle, "
              "creado_en) VALUES ('derivacion', 'pendiente', 'a', 1, '{}', 'm', 't', 'd', now())")


def test_0010_suma_los_motivos_de_elevar_y_el_downgrade_los_conserva_como_pedido(base_vacia):
    _base_con_membresias_0007()
    command.upgrade(_alembic(), "0009_calidad")
    with pytest.raises(IntegrityError):
        _ejecutar(_SOLICITUD.format("elevada_usuario"))

    command.upgrade(_alembic(), "0010_elevar")
    for motivo in ("elevada_usuario", "pedido_directo", "pedido_usuario"):
        _ejecutar(_SOLICITUD.format(motivo))
    with pytest.raises(IntegrityError):
        _ejecutar(_SOLICITUD.format("otro"))
    command.check(_alembic())

    command.downgrade(_alembic(), "0009_calidad")
    assert _escalar("SELECT count(*) FROM solicitudes WHERE motivo = 'pedido_usuario'") == 3
    with pytest.raises(IntegrityError):
        _ejecutar(_SOLICITUD.format("pedido_directo"))
    command.upgrade(_alembic(), "head")
    command.check(_alembic())
