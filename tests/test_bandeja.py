"""Bandeja de aprobación y feedback, de punta a punta con `atender`. Datos inventados: consorcios demo.

La base se destruye al empezar el módulo: `vaciar_y_sembrar` exige una base descartable por nombre."""
from __future__ import annotations

import threading

import pytest
from apoyo_web import vaciar_y_sembrar
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from consorcio.agente import grafo
from consorcio.agente.bandeja import (
    SinPermiso,
    SolicitudInexistente,
    SolicitudYaResuelta,
    TurnoAjeno,
    aprobar_reclamo,
    editar_reclamo,
    listar_solicitudes,
    mis_solicitudes,
    rechazar_reclamo,
    registrar_feedback,
    resolver_derivacion,
)
from consorcio.agente.esquemas import SalidaRouter
from consorcio.agente.grafo import atender
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Solicitud, Turno, Usuario
from consorcio.identidad import resolver_identidad

PALMERAS, ROBLES = "demo-palmeras", "demo-robles"
CAMPOS = {"id", "tipo", "estado", "motivo", "mensaje", "titulo", "detalle", "unidad", "titulo_final", "detalle_final",
          "respuesta", "autor_id", "autor_nombre", "resuelta_por", "creado_en", "resuelta_en"}


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base):
    yield
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE turnos, mensajes, conversaciones, solicitudes, consultas CASCADE"))


@pytest.fixture
def admin():
    return resolver_identidad("demo-admin-palmeras", PALMERAS)


@pytest.fixture
def vecino():
    return resolver_identidad("demo-prop-palmeras", PALMERAS)


@pytest.fixture
def admin_robles():
    return resolver_identidad("demo-multi", ROBLES)


def _pedir(monkeypatch, identidad, intencion: str, **campos) -> dict:
    def invocar(contador, nodo, sistema, usuario, esquema):
        return SalidaRouter(intencion=intencion, **campos)

    monkeypatch.setattr(grafo, "invocar_estructurado", invocar)
    return atender("mensaje original del vecino", identidad, None, "web")


def _reclamo(monkeypatch, identidad) -> int:
    return _pedir(monkeypatch, identidad, "reclamo", titulo="Propuesto", detalle="Detalle propuesto")["solicitud_id"]


def _derivacion(monkeypatch, identidad) -> int:
    return _pedir(monkeypatch, identidad, "derivar")["solicitud_id"]


def _fila(solicitud_id: int) -> Solicitud:
    with Session(postgres.engine()) as s:
        return s.get(Solicitud, solicitud_id)


def test_flujo_de_reclamo_aprobado(monkeypatch, admin, vecino):
    sid = _reclamo(monkeypatch, vecino)

    pendientes = listar_solicitudes(admin, estado="pendiente")
    assert [s["id"] for s in pendientes] == [sid] and set(pendientes[0]) == CAMPOS
    assert pendientes[0]["autor_nombre"] == "Pablo Propietario (demo)"

    r = aprobar_reclamo(admin, sid, "Mandamos al plomero")

    assert (r["estado"], r["respuesta"], r["resuelta_por"]) == ("aprobada", "Mandamos al plomero", admin.usuario_id)
    assert r["resuelta_en"] is not None and r["titulo_final"] is None
    assert listar_solicitudes(admin, estado="pendiente") == []
    visto = mis_solicitudes(vecino)
    assert [(s["id"], s["estado"], s["respuesta"]) for s in visto] == [(sid, "aprobada", "Mandamos al plomero")]


def test_aprobar_sin_respuesta(monkeypatch, admin, vecino):
    assert aprobar_reclamo(admin, _reclamo(monkeypatch, vecino))["respuesta"] is None


def test_editar_conserva_lo_propuesto(monkeypatch, admin, vecino):
    sid = _reclamo(monkeypatch, vecino)
    r = editar_reclamo(admin, sid, "Final", "Detalle final", "Listo")
    assert (r["estado"], r["titulo"], r["detalle"]) == ("editada", "Propuesto", "Detalle propuesto")
    assert (r["titulo_final"], r["detalle_final"], r["respuesta"]) == ("Final", "Detalle final", "Listo")


def test_rechazar_deja_estado_y_fecha(monkeypatch, admin, vecino):
    sid = _reclamo(monkeypatch, vecino)
    rechazar_reclamo(admin, sid, "No corresponde")
    f = _fila(sid)
    assert (f.estado, f.respuesta) == ("rechazada", "No corresponde") and f.resuelta_en is not None


def test_resolver_derivacion(monkeypatch, admin, vecino):
    sid = _derivacion(monkeypatch, vecino)
    r = resolver_derivacion(admin, sid, "Te llamamos")
    assert (r["estado"], r["respuesta"], r["motivo"]) == ("resuelta", "Te llamamos", "pedido_usuario")


@pytest.mark.parametrize("segunda", [
    lambda i, sid: aprobar_reclamo(i, sid),
    lambda i, sid: rechazar_reclamo(i, sid, "no"),
    lambda i, sid: editar_reclamo(i, sid, "t", "d"),
], ids=["aprobar", "rechazar", "editar"])
def test_doble_resolucion(monkeypatch, admin, vecino, segunda):
    sid = _reclamo(monkeypatch, vecino)
    aprobar_reclamo(admin, sid, "primera")
    with pytest.raises(SolicitudYaResuelta):
        segunda(admin, sid)
    f = _fila(sid)
    assert (f.estado, f.respuesta) == ("aprobada", "primera")


def test_aprobaciones_en_paralelo_gana_una(monkeypatch, admin, vecino):
    sid = _reclamo(monkeypatch, vecino)
    resultados: list[str] = []
    barrera = threading.Barrier(4)

    def aprobar():
        barrera.wait(timeout=30)
        try:
            aprobar_reclamo(admin, sid)
            resultados.append("ok")
        except SolicitudYaResuelta:
            resultados.append("ya")

    hilos = [threading.Thread(target=aprobar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join(timeout=60)
    assert sorted(resultados) == ["ok", "ya", "ya", "ya"]


@pytest.mark.parametrize("operacion", [
    lambda i, sid: listar_solicitudes(i),
    lambda i, sid: aprobar_reclamo(i, sid),
    lambda i, sid: editar_reclamo(i, sid, "t", "d"),
    lambda i, sid: rechazar_reclamo(i, sid, "no"),
    lambda i, sid: resolver_derivacion(i, sid, "no"),
], ids=["listar", "aprobar", "editar", "rechazar", "resolver"])
def test_propietario_no_opera_la_bandeja(monkeypatch, vecino, operacion):
    sid = _reclamo(monkeypatch, vecino)
    with pytest.raises(SinPermiso):
        operacion(vecino, sid)
    assert _fila(sid).estado == "pendiente"


def test_administrador_de_otro_consorcio_no_la_ve(monkeypatch, vecino, admin_robles):
    sid = _reclamo(monkeypatch, vecino)
    with pytest.raises(SolicitudInexistente):
        aprobar_reclamo(admin_robles, sid)
    assert listar_solicitudes(admin_robles) == []
    assert _fila(sid).estado == "pendiente"


def test_id_inexistente_da_lo_mismo_que_uno_ajeno(admin):
    with pytest.raises(SolicitudInexistente):
        aprobar_reclamo(admin, 999_999)


def test_el_tipo_tiene_que_coincidir(monkeypatch, admin, vecino):
    derivacion, reclamo = _derivacion(monkeypatch, vecino), _reclamo(monkeypatch, vecino)
    with pytest.raises(SolicitudInexistente):
        aprobar_reclamo(admin, derivacion)
    with pytest.raises(SolicitudInexistente):
        resolver_derivacion(admin, reclamo, "x")


def test_administrador_aprueba_su_propio_reclamo(monkeypatch, admin):
    assert aprobar_reclamo(admin, _reclamo(monkeypatch, admin))["estado"] == "aprobada"


def test_filtros_y_limite(monkeypatch, admin, vecino):
    reclamo, derivacion = _reclamo(monkeypatch, vecino), _derivacion(monkeypatch, vecino)
    assert [s["id"] for s in listar_solicitudes(admin)] == [derivacion, reclamo]
    assert [s["id"] for s in listar_solicitudes(admin, tipo="reclamo")] == [reclamo]
    assert [s["id"] for s in listar_solicitudes(admin, limite=1)] == [derivacion]
    aprobar_reclamo(admin, reclamo)
    assert [s["id"] for s in listar_solicitudes(admin, estado="aprobada", tipo="reclamo")] == [reclamo]


def test_mis_solicitudes_son_solo_las_propias_de_este_consorcio(monkeypatch, admin, vecino):
    multi_palmeras = resolver_identidad("demo-multi", PALMERAS)
    multi_robles = resolver_identidad("demo-multi", ROBLES)
    propia = _reclamo(monkeypatch, vecino)
    _reclamo(monkeypatch, admin)
    de_palmeras = _reclamo(monkeypatch, multi_palmeras)
    assert [s["id"] for s in mis_solicitudes(vecino)] == [propia]
    assert [s["id"] for s in mis_solicitudes(multi_palmeras)] == [de_palmeras]
    assert mis_solicitudes(multi_robles) == []


def test_autor_sin_nombre(monkeypatch, admin, vecino):
    sid = _reclamo(monkeypatch, vecino)
    with Session(postgres.engine()) as s:
        s.scalars(select(Usuario).where(Usuario.id == vecino.usuario_id)).one().nombre = None
        s.commit()
    try:
        assert listar_solicitudes(admin)[0]["id"] == sid and listar_solicitudes(admin)[0]["autor_nombre"] is None
    finally:
        vaciar_y_sembrar()


@pytest.mark.parametrize("valor", [1, -1])
def test_feedback_sobre_turno_propio(monkeypatch, vecino, valor):
    turno_id = _derivacion_turno(monkeypatch, vecino)
    registrar_feedback(vecino, turno_id, valor)
    with Session(postgres.engine()) as s:
        assert s.get(Turno, turno_id).feedback == valor


def _derivacion_turno(monkeypatch, identidad) -> int:
    return _pedir(monkeypatch, identidad, "derivar")["turno_id"]


def test_feedback_se_puede_cambiar(monkeypatch, vecino):
    turno_id = _derivacion_turno(monkeypatch, vecino)
    registrar_feedback(vecino, turno_id, 1)
    registrar_feedback(vecino, turno_id, -1)
    with Session(postgres.engine()) as s:
        assert s.get(Turno, turno_id).feedback == -1


def test_feedback_sobre_turno_ajeno(monkeypatch, admin, vecino):
    turno_id = _derivacion_turno(monkeypatch, vecino)
    with pytest.raises(TurnoAjeno):
        registrar_feedback(admin, turno_id, 1)
    with pytest.raises(TurnoAjeno):
        registrar_feedback(vecino, 999_999, 1)
    with Session(postgres.engine()) as s:
        assert s.get(Turno, turno_id).feedback is None


def test_feedback_del_mismo_usuario_en_otro_consorcio(monkeypatch):
    palmeras, robles = resolver_identidad("demo-multi", PALMERAS), resolver_identidad("demo-multi", ROBLES)
    turno_id = _derivacion_turno(monkeypatch, palmeras)
    with pytest.raises(TurnoAjeno):
        registrar_feedback(robles, turno_id, 1)


def test_feedback_invalido(monkeypatch, vecino):
    turno_id = _derivacion_turno(monkeypatch, vecino)
    with pytest.raises(ValueError):
        registrar_feedback(vecino, turno_id, 5)
