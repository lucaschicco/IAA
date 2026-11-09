"""Métricas del CRM contra conteos calculados a mano sobre `escenario`, con datos inventados.

Consorcio A: 7 turnos de usuario. Consorcio B: 2. Además, en cada tabla hay filas fuera del período y
filas de evaluación que nunca tienen que contar. La base se destruye al empezar el módulo."""
from __future__ import annotations

import ast
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from consorcio import cli, crm, tope
from consorcio.agente.registro import FUNCIONALIDADES
from consorcio.almacen import postgres
from consorcio.almacen.postgres import (
    Carga,
    Consorcio,
    Consulta,
    Conversacion,
    Documento,
    GastoModelo,
    Membresia,
    Mensaje,
    Seccion,
    Solicitud,
    Turno,
    Usuario,
)
from consorcio.config import config
from consorcio.consulta import evaluar

BASES_DESCARTABLES = {"consorcio_test"}
A, B = "prueba-crm-a", "prueba-crm-b"
TEXTO_PRIVADO = "TEXTO-PRIVADO-DEL-USUARIO"
NOMBRE_PRIVADO = "NOMBRE-PRIVADO"
AHORA = datetime.now(UTC)
HACE_1H = AHORA - timedelta(hours=1)
HACE_10D = AHORA - timedelta(days=10)
DESDE, HASTA = AHORA - timedelta(days=7), AHORA + timedelta(minutes=1)
DESDE_30 = AHORA - timedelta(days=30)


@pytest.fixture(scope="module")
def esquema():
    if postgres.engine().url.database not in BASES_DESCARTABLES:
        raise RuntimeError("la base configurada no es descartable: este módulo la destruye")
    with postgres.engine().begin() as con:
        con.execute(text("DROP SCHEMA public CASCADE"))
        con.execute(text("CREATE SCHEMA public"))
    postgres.crear_tablas()


@pytest.fixture(autouse=True)
def base(esquema, monkeypatch, tmp_path):
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE consorcios, usuarios, gastos_modelo, cargas, consultas CASCADE"))
    monkeypatch.setattr(config, "tope_usd_mensual", 20.0)
    monkeypatch.setattr(crm, "DIR_EVALUACIONES", tmp_path)
    return tmp_path


def _consulta(s: Session, verificada: bool, origen: str = "web") -> int:
    fila = Consulta(pregunta=TEXTO_PRIVADO, respuesta=TEXTO_PRIVADO, consorcio_id=A, verificada=verificada,
                    derivada=not verificada, metodo_busqueda="tsvector", k=3, proveedor="fake", modelo="fake",
                    skill_version="0.2.0", segundos=0.1, origen=origen, creado_en=HACE_1H)
    s.add(fila)
    s.flush()
    return fila.id


def _solicitud(s: Session, cid: str, autor: int, tipo: str, estado: str, motivo: str, creado_en: datetime = HACE_1H,
               espera_s: float | None = None) -> int:
    fila = Solicitud(tipo=tipo, estado=estado, consorcio_id=cid, autor_id=autor, motivo=motivo, mensaje=TEXTO_PRIVADO,
                     titulo=TEXTO_PRIVADO, detalle=TEXTO_PRIVADO, creado_en=creado_en,
                     resuelta_en=None if espera_s is None else creado_en + timedelta(seconds=espera_s))
    s.add(fila)
    s.flush()
    return fila.id


def _turno(s: Session, cid: str, usuario: int, rol: str, funcionalidad: str | None, resultado: str,
           intencion: str | None = "igual", creado_en: datetime = HACE_1H, origen: str = "usuario",
           secciones: tuple[int, ...] = (), costo: float | None = None, feedback: int | None = None,
           consulta: int | None = None, solicitud: int | None = None) -> None:
    s.add(Turno(usuario_id=usuario, consorcio_id=cid, rol=rol, canal="web", origen=origen, funcionalidad=funcionalidad,
                intencion=funcionalidad if intencion == "igual" else intencion, resultado=resultado,
                secciones_usadas=list(secciones), consulta_id=consulta, solicitud_id=solicitud, costo_usd=costo,
                segundos=0.1, modelo="fake", skill_version="0.1.0", feedback=feedback, creado_en=creado_en))


def _gasto(s: Session, cid: str | None, costo: float | None, creado_en: datetime = HACE_1H, modelo: str = "m") -> None:
    s.add(GastoModelo(consorcio_id=cid, nodo="n", modelo=modelo, tokens_entrada=10, tokens_salida=5, costo_usd=costo,
                      creado_en=creado_en))


def _documento(s: Session, id_: str, cid: str, tipo: str, estado: str, activo: bool, secciones: int) -> None:
    s.add(Documento(id=id_, consorcio_id=cid, nombre_archivo=f"{id_}.pdf", ruta="x", tipo_documento=tipo, titulo="t",
                    resumen="r", total_paginas=1, metodo_extraccion="texto", confianza_clasificador=1.0, clasificacion={},
                    costo={}, segundos_total=0.0, estado=estado, activo=activo))
    s.flush()
    s.add_all(Seccion(documento_id=id_, titulo="t", tema="t", resumen="r") for _ in range(secciones))


def _usuario(s: Session, sufijo: str, membresias: tuple[str, ...], actividad: timedelta | None) -> int:
    fila = Usuario(sujeto_externo=f"crm-{sufijo}", nombre=f"{NOMBRE_PRIVADO}-{sufijo}",
                   ultima_actividad=None if actividad is None else AHORA - actividad)
    s.add(fila)
    s.flush()
    s.add_all(Membresia(usuario_id=fila.id, consorcio_id=cid, rol="consejo") for cid in membresias)
    return fila.id


@pytest.fixture
def escenario() -> dict[str, int]:
    with Session(postgres.engine()) as s:
        s.add_all([Consorcio(id=A, nombre=NOMBRE_PRIVADO), Consorcio(id=B, nombre=NOMBRE_PRIVADO)])
        s.flush()
        u1 = _usuario(s, "1", (A,), timedelta(minutes=1))
        u2 = _usuario(s, "2", (A,), timedelta(hours=2))
        u3 = _usuario(s, "3", (A, B), timedelta(days=3))
        u4 = _usuario(s, "4", (B,), timedelta(days=20))
        _usuario(s, "5", (B,), timedelta(days=40))
        _usuario(s, "6", (A,), None)

        _documento(s, "doc-1", A, "reglamento", "extraido", True, 3)
        _documento(s, "doc-2", A, "acta", "aprobado", False, 2)
        _documento(s, "doc-3", B, "reglamento", "extraido", True, 1)

        for cid, estado, creado_en in ((A, "duplicado", HACE_1H), (A, "periodo_duplicado", HACE_1H), (A, "procesado", HACE_1H),
                                       (A, "duplicado", HACE_10D), (B, "duplicado", HACE_1H)):
            s.add(Carga(consorcio_id=cid, estado=estado, creado_en=creado_en))

        # Derivaciones: en el período, 3 de A y 1 de B; esperas de 100, 300 y 1000 s
        d1 = _solicitud(s, A, u2, "derivacion", "resuelta", "cita_no_verificada", espera_s=100)
        d2 = _solicitud(s, A, u1, "derivacion", "resuelta", "sin_respaldo", espera_s=300)
        d3 = _solicitud(s, A, u3, "derivacion", "pendiente", "router_invalido")
        _solicitud(s, B, u4, "derivacion", "resuelta", "pedido_usuario", espera_s=1000)
        _solicitud(s, A, u1, "derivacion", "resuelta", "pedido_usuario", creado_en=HACE_10D, espera_s=5)
        # Reclamos: en el período, 5 de A y 1 de B
        r1 = _solicitud(s, A, u1, "reclamo", "aprobada", "reclamo", espera_s=10)
        _solicitud(s, A, u1, "reclamo", "editada", "reclamo", espera_s=10)
        _solicitud(s, A, u1, "reclamo", "rechazada", "reclamo", espera_s=10)
        _solicitud(s, A, u1, "reclamo", "pendiente", "reclamo")
        _solicitud(s, A, u2, "reclamo", "pendiente", "reclamo")
        _solicitud(s, B, u4, "reclamo", "aprobada", "reclamo", espera_s=10)
        _solicitud(s, A, u1, "reclamo", "rechazada", "reclamo", creado_en=HACE_10D, espera_s=10)

        adm, prop = "administracion", "propietario"
        _turno(s, A, u1, prop, "reglamento", "respondida", secciones=(1, 2, 3), costo=0.01, feedback=1,
               consulta=_consulta(s, True))
        _turno(s, A, u1, prop, "reglamento", "respondida", secciones=(4,), costo=0.02, feedback=-1,
               consulta=_consulta(s, True))
        _turno(s, A, u2, adm, "reglamento", "derivada", secciones=(5, 6, 7), costo=0.03, consulta=_consulta(s, False),
               solicitud=d1)
        _turno(s, A, u1, prop, "reglamento", "derivada", consulta=_consulta(s, False), solicitud=d2)
        _turno(s, A, u2, adm, "expensas", "respondida", costo=0.001, feedback=1)
        _turno(s, A, u1, prop, "reclamo", "reclamo_pendiente", solicitud=r1)
        _turno(s, A, u3, prop, None, "derivada", intencion=None, costo=0.005, solicitud=d3)
        _turno(s, B, u3, prop, "reglamento", "respondida", secciones=(8, 9), costo=0.04, feedback=1,
               consulta=_consulta(s, True))
        _turno(s, B, u4, prop, None, "fuera_de_alcance", intencion="fuera_de_alcance", costo=0.002)
        _turno(s, A, u1, prop, "expensas", "respondida", creado_en=HACE_10D, costo=0.5)  # fuera del período

        # Libro: lo de los turnos, más ingesta (0,25 en A, 0,15 en B), 0,05 sin consorcio y 0,5 fuera del período
        for cid, costo in ((A, 0.066), (B, 0.042), (A, 0.25), (B, 0.15), (None, 0.05)):
            _gasto(s, cid, costo)
        _gasto(s, A, 0.5, HACE_10D)

        _evaluacion(s, u1)
        s.commit()
    return {"u1": u1, "u2": u2, "u3": u3, "u4": u4}


def _evaluacion(s: Session, usuario: int) -> None:
    """Turnos de evaluación, con lo que dejan en cada tabla, que no cambian ninguna métrica."""
    prop = "propietario"
    d = _solicitud(s, A, usuario, "derivacion", "resuelta", "sin_respaldo", espera_s=7777)
    r = _solicitud(s, A, usuario, "reclamo", "aprobada", "reclamo", espera_s=10)
    _turno(s, A, usuario, prop, "reglamento", "derivada", origen="evaluacion", secciones=(1, 2, 3, 4, 5), costo=0.7,
           feedback=-1, consulta=_consulta(s, False, "evaluacion"), solicitud=d)
    _turno(s, A, usuario, prop, "reclamo", "reclamo_pendiente", origen="evaluacion", costo=0.3, feedback=1, solicitud=r)
    _turno(s, A, usuario, prop, "documentos", "error", origen="evaluacion", intencion=None)
    _gasto(s, A, 1.0)


def _evaluacion_guardada(tmp: Path, nombre: str = "2026-10-26-consultas-test.json", **campos) -> None:
    contenido = {"particion": "test", "generacion": {"n": 13, "tasa_correctas": 0.615}, **campos}
    (tmp / nombre).write_text(json.dumps(contenido), encoding="utf-8")


def _corrida(**campos) -> dict:
    return {"archivo": "preguntas.csv", "particion": "test", "n": 2, "consorcio": config.consorcio_por_defecto,
            "retrieval": {"k": 3, "por_metodo": {}, "detalle": ["una pregunta real"]},
            "generacion": {"n": 2, "tasa_correctas": 0.5, "detalle": ["una respuesta real"]}, **campos}


@pytest.fixture
def evaluaciones(base, monkeypatch):
    """Con un proveedor real configurado, sin llamarlo: `guardar_evaluacion` solo lee su nombre."""
    monkeypatch.setattr(evaluar, "DIR_EVALUACIONES", base)
    monkeypatch.setattr(config, "llm_provider", "anthropic")
    monkeypatch.setattr(config, "llm_model", "claude-haiku-5-5")
    return base


def test_la_evaluacion_de_la_cli_llega_al_crm(evaluaciones, monkeypatch, capsys):
    monkeypatch.setattr(evaluar, "evaluar_consultas", lambda *a, **k: _corrida())
    assert cli.main(["evaluar-consultas", "preguntas.csv", "--particion", "test", "--con-modelo", "--sin-detalle"]) == 0
    salida = capsys.readouterr()
    assert json.loads(salida.out)["generacion"]["tasa_correctas"] == 0.5  # stdout sigue siendo JSON puro
    fecha = datetime.now(UTC).date().isoformat()
    assert crm._exactitud_evaluacion() == {"valor": 0.5, "n": 2, "fecha": fecha, "fuente": f"{fecha}-consultas-test.json"}
    assert f"{fecha}-consultas-test.json" in salida.err


def test_la_evaluacion_guardada_no_lleva_preguntas_ni_respuestas(evaluaciones):
    contenido = json.loads(evaluar.guardar_evaluacion(_corrida()).read_text(encoding="utf-8"))
    assert "detalle" not in contenido["retrieval"] and "detalle" not in contenido["generacion"]
    assert (contenido["proveedor"], contenido["modelo"]) == ("anthropic", "claude-haiku-5-5")


@pytest.mark.parametrize("corrida", [{"particion": "dev"}, {"consorcio": "demo-palmeras"}])
def test_una_corrida_que_no_es_la_del_sistema_no_se_guarda(evaluaciones, corrida):
    _evaluacion_guardada(evaluaciones)
    assert evaluar.guardar_evaluacion(_corrida(**corrida)) is None
    assert crm._exactitud_evaluacion()["valor"] == 0.615


def test_el_fake_no_pisa_la_exactitud_del_crm(evaluaciones, monkeypatch):
    monkeypatch.setattr(config, "llm_provider", "fake")
    _evaluacion_guardada(evaluaciones)
    assert evaluar.guardar_evaluacion(_corrida()) is None
    assert crm._exactitud_evaluacion()["valor"] == 0.615


def test_sin_modelo_no_hay_nada_que_guardar(evaluaciones):
    sin_generacion = {k: v for k, v in _corrida().items() if k != "generacion"}
    assert evaluar.guardar_evaluacion(sin_generacion) is None


def test_usuarios_son_ventanas_desde_ahora_y_dependen_del_consorcio(escenario):
    global_ = {"conectados": 1, "activos_dia": 2, "activos_semana": 3, "activos_mes": 4}
    assert crm.panel(DESDE, HASTA)["usuarios"] == global_
    assert crm.panel(DESDE, HASTA, A)["usuarios"] == {"conectados": 1, "activos_dia": 2, "activos_semana": 3, "activos_mes": 3}
    assert crm.panel(DESDE, HASTA, B)["usuarios"] == {"conectados": 0, "activos_dia": 0, "activos_semana": 1, "activos_mes": 2}
    # Un período sin nada no cambia las ventanas
    antiguo = AHORA - timedelta(days=500)
    assert crm.panel(antiguo, antiguo + timedelta(days=1))["usuarios"] == global_


def test_uso_por_usuario_consorcio_y_rol(escenario):
    uso = crm.panel(DESDE, HASTA)["uso"]
    assert uso["por_usuario"] == [
        {"usuario_id": escenario["u1"], "turnos": 4}, {"usuario_id": escenario["u2"], "turnos": 2},
        {"usuario_id": escenario["u3"], "turnos": 2}, {"usuario_id": escenario["u4"], "turnos": 1},
    ]
    assert uso["por_consorcio"] == {A: 7, B: 2}
    assert uso["por_rol"] == {"propietario": 7, "administracion": 2}
    assert crm.panel(DESDE, HASTA, B)["uso"]["por_rol"] == {"propietario": 2}


def test_por_usuario_se_corta_en_veinte_y_no_trae_nombres():
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=A, nombre="x"))
        s.flush()
        for i in range(23):
            usuario = _usuario(s, f"top-{i}", (A,), None)
            for _ in range(i % 2 + 1):
                _turno(s, A, usuario, "propietario", "expensas", "respondida")
        s.commit()
    por_usuario = crm.panel(DESDE, HASTA)["uso"]["por_usuario"]
    assert len(por_usuario) == 20
    assert [f["turnos"] for f in por_usuario] == [2] * 11 + [1] * 9
    assert set(por_usuario[0]) == {"usuario_id", "turnos"}


def test_documentos_son_el_estado_de_hoy_y_los_duplicados_del_periodo(escenario):
    documentos = crm.panel(DESDE, HASTA)["documentos"]
    assert documentos == {"por_tipo": {"reglamento": 2, "acta": 1}, "por_estado": {"extraido": 2, "aprobado": 1},
                          "activos": 2, "inactivos": 1, "duplicados_evitados": 3}
    assert crm.panel(DESDE, HASTA, A)["documentos"] == {
        "por_tipo": {"reglamento": 1, "acta": 1}, "por_estado": {"extraido": 1, "aprobado": 1},
        "activos": 1, "inactivos": 1, "duplicados_evitados": 2,
    }
    assert crm.panel(DESDE, HASTA, B)["documentos"]["duplicados_evitados"] == 1
    assert crm.panel(DESDE_30, HASTA)["documentos"]["duplicados_evitados"] == 4


def test_contextos(escenario):
    # Turnos de reglamento con 3, 1, 3, 0 y 2 secciones: 9 en 5 turnos
    assert crm.panel(DESDE, HASTA)["contextos"] == {
        "secciones_indexadas": 4,  # 3 + 1: las 2 del documento inactivo no se pueden buscar
        "secciones_por_respuesta": {"promedio": 1.8, "distribucion": {0: 1, 1: 1, 2: 1, 3: 2}},
        "respuestas_con_contexto": 4,
    }
    assert crm.panel(DESDE, HASTA, A)["contextos"] == {
        "secciones_indexadas": 3,
        "secciones_por_respuesta": {"promedio": 1.75, "distribucion": {0: 1, 1: 1, 3: 2}},
        "respuestas_con_contexto": 3,
    }


def test_preguntas_funcionalidades_e_intenciones(escenario):
    datos = crm.panel(DESDE, HASTA)
    assert datos["preguntas"] == {
        "total": 9, "por_resultado": {"respondida": 4, "derivada": 3, "reclamo_pendiente": 1, "fuera_de_alcance": 1},
    }
    assert datos["funcionalidades"] == {"reglamento": 5, "expensas": 1, "documentos": 0, "reclamo": 1, "derivar": 0}
    assert list(datos["funcionalidades"]) == list(FUNCIONALIDADES)
    assert datos["intenciones"] == {"reglamento": 5, "expensas": 1, "reclamo": 1, "fuera_de_alcance": 1, "sin_clasificar": 1}


def test_elevaciones_con_mediana(escenario):
    assert crm.panel(DESDE, HASTA)["elevaciones"] == {
        "total": 4, "resueltas": 3, "pendientes": 1, "tiempo_hasta_resolucion_mediana_s": 300.0,
        "por_motivo": {"cita_no_verificada": 1, "sin_respaldo": 1, "router_invalido": 1, "pedido_usuario": 1},
    }
    # Con cantidad par, la mediana interpola: (100 + 300) / 2
    assert crm.panel(DESDE, HASTA, A)["elevaciones"]["tiempo_hasta_resolucion_mediana_s"] == 200.0


def test_acciones_son_los_reclamos_por_estado(escenario):
    assert crm.panel(DESDE, HASTA)["acciones"] == {"aprobadas": 2, "editadas": 1, "rechazadas": 1, "pendientes": 2}
    assert crm.panel(DESDE, HASTA, B)["acciones"] == {"aprobadas": 1, "editadas": 0, "rechazadas": 0, "pendientes": 0}
    assert crm.panel(DESDE_30, HASTA)["acciones"]["rechazadas"] == 2


def test_costos_por_funcionalidad_e_ingesta(escenario):
    costos = crm.panel(DESDE, HASTA)["costos"]
    assert costos["por_funcionalidad"] == {
        "reglamento": {"turnos": 5, "costo_usd": pytest.approx(0.10)},
        "expensas": {"turnos": 1, "costo_usd": pytest.approx(0.001)},
        "documentos": {"turnos": 0, "costo_usd": 0.0},
        "reclamo": {"turnos": 1, "costo_usd": 0.0},
        "derivar": {"turnos": 0, "costo_usd": 0.0},
        "sin_funcionalidad": {"turnos": 2, "costo_usd": pytest.approx(0.007)},
    }
    # Libro 1,558 (0,108 de turnos + 0,45 de ingesta y otros + 1,0 de evaluación) menos 1,108 de turnos
    assert costos["ingesta_y_otros"] == pytest.approx(0.45)
    # Con filtro no entra lo que no tiene consorcio: 0,25 en A y 0,15 en B
    assert crm.panel(DESDE, HASTA, A)["costos"]["ingesta_y_otros"] == pytest.approx(0.25)
    assert crm.panel(DESDE, HASTA, B)["costos"]["ingesta_y_otros"] == pytest.approx(0.15)


def test_ingesta_y_otros_no_da_negativo_si_el_turno_redondea_para_arriba():
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=A, nombre="x"))
        s.flush()
        usuario = _usuario(s, "1", (A,), None)
        _turno(s, A, usuario, "propietario", "reglamento", "respondida", costo=0.0002)
        _gasto(s, A, 0.00015)
        s.commit()
    assert crm.panel(DESDE, HASTA)["costos"]["ingesta_y_otros"] == 0.0


def test_acertadas_son_tres_indicadores_que_no_se_combinan(escenario, base):
    _evaluacion_guardada(base)
    acertadas = crm.panel(DESDE, HASTA)["acertadas"]
    assert acertadas == {
        "cita_verificada": {"valor": 0.6, "aciertos": 3, "n": 5},
        "feedback_positivo": {"valor": 0.75, "positivos": 3, "n": 4, "cobertura": 0.4444},
        "exactitud_evaluacion": {"valor": 0.615, "n": 13, "fecha": "2026-10-26", "fuente": "2026-10-26-consultas-test.json"},
    }
    assert len({a["valor"] for a in acertadas.values()}) == 3
    assert crm.panel(DESDE, HASTA, A)["acertadas"]["cita_verificada"] == {"valor": 0.5, "aciertos": 2, "n": 4}
    assert crm.panel(DESDE, HASTA, A)["acertadas"]["feedback_positivo"] == {
        "valor": 0.6667, "positivos": 2, "n": 3, "cobertura": 0.4286,
    }
    assert crm.panel(DESDE, HASTA, B)["acertadas"]["feedback_positivo"] == {
        "valor": 1.0, "positivos": 1, "n": 1, "cobertura": 0.5,
    }


def test_turno_de_reglamento_sin_consulta_no_entra_en_cita_verificada():
    """Falló antes de `preguntar`: no hubo intento de citar. Se ve en `elevaciones.por_motivo`."""
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=A, nombre="x"))
        s.flush()
        usuario = _usuario(s, "1", (A,), None)
        _turno(s, A, usuario, "propietario", "reglamento", "respondida", consulta=_consulta(s, True))
        _turno(s, A, usuario, "propietario", "reglamento", "derivada")
        s.commit()
    assert crm.panel(DESDE, HASTA)["acertadas"]["cita_verificada"] == {"valor": 1.0, "aciertos": 1, "n": 1}


def test_periodo_incluye_desde_y_excluye_hasta(escenario):
    assert crm.panel(HACE_1H, HACE_1H + timedelta(seconds=1))["preguntas"]["total"] == 9
    assert crm.panel(HACE_1H - timedelta(seconds=1), HACE_1H)["preguntas"]["total"] == 0


def test_filtro_de_periodo_amplio_suma_lo_viejo(escenario):
    datos = crm.panel(DESDE_30, HASTA)
    assert datos["preguntas"]["total"] == 10
    assert datos["funcionalidades"]["expensas"] == 2
    assert datos["elevaciones"]["total"] == 5
    assert datos["elevaciones"]["tiempo_hasta_resolucion_mediana_s"] == 200.0  # 5, 100, 300, 1000
    assert datos["costos"]["ingesta_y_otros"] == pytest.approx(0.45)  # los 0,5 viejos son de un turno


def test_evaluacion_no_altera_ninguna_metrica(escenario):
    """Antes y después, panel completo. Solo se mueve el tope: ese gasto se pagó de verdad."""
    def sin_tope(datos: dict) -> dict:
        datos["costos"] = {**datos["costos"], "tope": None}
        return datos

    for consorcio in (None, A):
        antes = sin_tope(crm.panel(DESDE, HASTA, consorcio))
        with Session(postgres.engine()) as s:
            _evaluacion(s, escenario["u1"])
            s.commit()
        assert sin_tope(crm.panel(DESDE, HASTA, consorcio)) == antes


def test_filtro_por_consorcio_excluye_al_otro_salvo_el_tope(escenario):
    datos = crm.panel(DESDE, HASTA, A)
    assert datos["uso"]["por_consorcio"] == {A: 7}
    assert datos["preguntas"]["total"] == 7
    assert datos["elevaciones"]["total"] == 3
    assert crm.panel(DESDE, HASTA, B)["preguntas"]["total"] == 2
    assert datos["costos"]["tope"]["alcance"] == "global"
    assert datos["costos"]["tope"] == crm.panel(DESDE, HASTA, B)["costos"]["tope"] == crm.panel(DESDE, HASTA)["costos"]["tope"]


def test_tope_es_el_estado_global_con_precio_y_sin_precio():
    with Session(postgres.engine()) as s:
        s.add_all([Consorcio(id=A, nombre="x"), Consorcio(id=B, nombre="x")])
        s.flush()
        _gasto(s, A, 0.3, AHORA)
        _gasto(s, B, None, AHORA, modelo="sin-precio")
        s.commit()
    en_b = crm.panel(DESDE, HASTA, B)["costos"]["tope"]
    assert en_b == {**tope.estado_tope(), "alcance": "global"}
    assert en_b["gastado_usd"] == pytest.approx(0.3)  # gastado en A, se ve filtrando por B
    assert en_b["modelos_sin_precio"] == [{"modelo": "sin-precio", "llamadas": 1, "tokens": 15}]


def test_periodo_vacio_da_sin_datos_y_no_cero():
    datos = crm.panel(DESDE, HASTA)
    assert datos["acertadas"] == {
        "cita_verificada": {"valor": None, "aciertos": 0, "n": 0},
        "feedback_positivo": {"valor": None, "positivos": 0, "n": 0, "cobertura": None},
        "exactitud_evaluacion": {"valor": None, "estado": "sin corrida"},
    }
    assert datos["funcionalidades"] == dict.fromkeys(FUNCIONALIDADES, 0)
    assert datos["contextos"]["secciones_por_respuesta"] == {"promedio": None, "distribucion": {}}
    assert datos["elevaciones"]["tiempo_hasta_resolucion_mediana_s"] is None
    assert datos["preguntas"] == {"total": 0, "por_resultado": {}}


def test_turnos_sin_feedback_dan_cobertura_cero_y_valor_sin_datos():
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=A, nombre="x"))
        s.flush()
        _turno(s, A, _usuario(s, "1", (A,), None), "propietario", "expensas", "respondida")
        s.commit()
    assert crm.panel(DESDE, HASTA)["acertadas"]["feedback_positivo"] == {"valor": None, "positivos": 0, "n": 0, "cobertura": 0.0}


def test_evaluacion_usa_el_ultimo_archivo_de_test_por_fecha_del_nombre(base):
    _evaluacion_guardada(base, "2026-09-01-consultas-test.json", generacion={"n": 5, "tasa_correctas": 0.2})
    _evaluacion_guardada(base, "2026-11-01-consultas-test.json", generacion={"n": 7, "tasa_correctas": 0.8})
    _evaluacion_guardada(base, "2026-12-01-consultas-dev.json", generacion={"n": 9, "tasa_correctas": 0.9})
    _evaluacion_guardada(base, "sin-fecha-consultas-test.json", generacion={"n": 9, "tasa_correctas": 0.9})
    assert crm.panel(DESDE, HASTA)["acertadas"]["exactitud_evaluacion"] == {
        "valor": 0.8, "n": 7, "fecha": "2026-11-01", "fuente": "2026-11-01-consultas-test.json",
    }


@pytest.mark.parametrize("campos", [{"generacion": None}, {"generacion": {"n": 3}}, {"particion": "dev"}])
def test_ultima_evaluacion_sin_generacion_de_test_es_sin_corrida(base, campos):
    _evaluacion_guardada(base, "2026-09-01-consultas-test.json")  # una anterior válida no la reemplaza
    _evaluacion_guardada(base, "2026-11-01-consultas-test.json", **campos)
    assert crm.panel(DESDE, HASTA)["acertadas"]["exactitud_evaluacion"] == {"valor": None, "estado": "sin corrida"}


@pytest.mark.parametrize("contenido", ["{roto", "[1, 2]"])
def test_archivo_de_evaluacion_ilegible_es_sin_corrida(base, contenido):
    (base / "2026-11-01-consultas-test.json").write_text(contenido, encoding="utf-8")
    assert crm.panel(DESDE, HASTA)["acertadas"]["exactitud_evaluacion"] == {"valor": None, "estado": "sin corrida"}


def test_evaluacion_real_del_repo_tiene_el_formato_esperado(monkeypatch):
    monkeypatch.undo()
    exactitud = crm.panel(DESDE, HASTA)["acertadas"]["exactitud_evaluacion"]
    assert exactitud["fuente"].endswith("-consultas-test.json")
    assert 0 <= exactitud["valor"] <= 1


def test_panel_serializable_sin_textos_ni_nombres(escenario, base):
    _evaluacion_guardada(base)
    with Session(postgres.engine()) as s:
        s.add(Conversacion(id="c" * 32, usuario_id=escenario["u1"], consorcio_id=A))
        s.flush()
        s.add(Mensaje(conversacion_id="c" * 32, rol="usuario", texto=TEXTO_PRIVADO))
        s.commit()
    for consorcio in (None, A):
        volcado = json.dumps(crm.panel(DESDE, HASTA, consorcio))
        assert TEXTO_PRIVADO not in volcado
        assert NOMBRE_PRIVADO not in volcado
        assert "crm-1" not in volcado  # tampoco el sujeto externo


def test_crm_no_importa_mensaje_ni_la_web():
    arbol = ast.parse(Path(crm.__file__).read_text(encoding="utf-8"))
    importados = {
        nombre.name for nodo in ast.walk(arbol) if isinstance(nodo, ast.ImportFrom | ast.Import) for nombre in nodo.names
    } | {nodo.module for nodo in ast.walk(arbol) if isinstance(nodo, ast.ImportFrom)}
    assert "Mensaje" not in importados
    assert not any(n and n.split(".")[0] in {"fastapi", "starlette", "web"} for n in importados)
    assert not any(isinstance(n, ast.Attribute) and n.attr == "mensajes" for n in ast.walk(arbol))


def test_consorcios_lista_ids_ordenados(escenario):
    assert crm.consorcios() == [A, B]


def test_actividad_cuenta_por_dia_y_rellena_los_dias_sin_uso():
    fin = datetime(2026, 3, 10, 12, tzinfo=UTC)
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=A, nombre="x"))
        s.flush()
        u = _usuario(s, "1", (A,), None)
        _turno(s, A, u, "propietario", "reglamento", "respondida", creado_en=fin - timedelta(hours=1))
        _turno(s, A, u, "propietario", "derivar", "derivada", creado_en=fin - timedelta(hours=2))
        _turno(s, A, u, "propietario", "reglamento", "respondida", creado_en=fin - timedelta(days=2))
        _turno(s, A, u, "propietario", "reglamento", "derivada", creado_en=fin - timedelta(days=1), origen="evaluacion")
        s.commit()
    serie = crm.panel(fin - timedelta(days=3), fin)["actividad"]
    assert serie == [
        {"dia": "2026-03-07", "turnos": 0, "derivadas": 0},
        {"dia": "2026-03-08", "turnos": 1, "derivadas": 0},
        {"dia": "2026-03-09", "turnos": 0, "derivadas": 0},
        {"dia": "2026-03-10", "turnos": 2, "derivadas": 1},
    ]


def test_periodo_anterior_es_la_ventana_previa_del_mismo_largo_y_respeta_el_filtro():
    fin = datetime(2026, 3, 10, tzinfo=UTC)
    previo = fin - timedelta(days=10)
    with Session(postgres.engine()) as s:
        s.add_all([Consorcio(id=A, nombre="x"), Consorcio(id=B, nombre="y")])
        s.flush()
        u = _usuario(s, "1", (A, B), None)
        _turno(s, A, u, "propietario", "reglamento", "respondida", creado_en=previo)
        _turno(s, A, u, "propietario", "reglamento", "respondida", creado_en=previo, origen="evaluacion")
        _turno(s, B, u, "propietario", "reglamento", "respondida", creado_en=previo)
        _turno(s, A, u, "propietario", "reglamento", "respondida", creado_en=fin - timedelta(days=20))  # fuera de ambas
        _solicitud(s, A, u, "derivacion", "pendiente", "pedido_usuario", creado_en=previo)
        _solicitud(s, A, u, "reclamo", "pendiente", "reclamo", creado_en=previo)  # no es una elevación
        s.commit()
    desde = fin - timedelta(days=7)
    assert crm.panel(desde, fin)["periodo_anterior"] == {"preguntas": 2, "elevaciones": 1}
    assert crm.panel(desde, fin, A)["periodo_anterior"] == {"preguntas": 1, "elevaciones": 1}

