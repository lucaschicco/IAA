"""Calidad de las respuestas: medidor de certeza, revisión de la administración, su pantalla y el CRM.

Datos inventados de `sembrar-demo` y modelo simulado. La base se destruye al empezar el módulo:
`vaciar_y_sembrar` exige una base descartable por nombre."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from consorcio import crm
from consorcio.agente import certeza, grafo, revision
from consorcio.agente.bandeja import SinPermiso
from consorcio.agente.esquemas import SalidaRouter
from consorcio.agente.grafo import atender
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Mensaje, Revision, Turno
from consorcio.config import config
from consorcio.identidad import resolver_identidad

PALMERAS, ROBLES = "demo-palmeras", "demo-robles"
ADMIN, PROP, MULTI = "demo-admin-palmeras", "demo-prop-palmeras", "demo-multi"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, monkeypatch):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    yield
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE revisiones, turnos, mensajes, conversaciones, solicitudes, consultas CASCADE"))


def admin():
    return resolver_identidad(ADMIN, PALMERAS)


def vecino():
    return resolver_identidad(PROP, PALMERAS)


def router(monkeypatch, **campos) -> None:
    monkeypatch.setattr(grafo, "invocar_estructurado", lambda *a, **k: SalidaRouter(**campos))


def turno(turno_id: int) -> Turno:
    with Session(postgres.engine()) as s:
        return s.get(Turno, turno_id)


def texto(mensaje_id: int | None) -> str | None:
    with Session(postgres.engine()) as s:
        return s.get(Mensaje, mensaje_id).texto if mensaje_id else None


def respondida_reglamento(monkeypatch, identidad=None, mensaje="¿Se permiten mascotas?") -> dict:
    monkeypatch.setattr(config, "fake_guionado", True)
    router(monkeypatch, intencion="reglamento", pregunta_reescrita="¿Se permiten mascotas?")
    return atender(mensaje, identidad or vecino(), None, "web")


def derivada(monkeypatch, identidad=None) -> dict:
    router(monkeypatch, intencion="derivar")
    return atender("quiero hablar con alguien", identidad or vecino(), None, "web")


# ------------------------------------------------------------------ medidor


def test_certeza_de_reglamento_premia_la_primera_seccion_y_el_margen():
    fuerte = certeza.calcular("reglamento", "respondida", certeza.senales_reglamento(
        [{"puntaje": 0.9}, {"puntaje": 0.1}], fuente=1))
    floja = certeza.calcular("reglamento", "respondida", certeza.senales_reglamento(
        [{"puntaje": 0.1}, {"puntaje": 0.1}], fuente=2))
    assert fuerte > floja and certeza.nivel(fuerte) == "alta" and certeza.nivel(floja) == "baja"
    assert 0.5 <= floja and fuerte <= 1.0


def test_una_sola_seccion_no_cuenta_como_margen_perfecto():
    sola = certeza.senales_reglamento([{"puntaje": 0.5}], fuente=1)
    assert sola["margen"] is None
    con_rival = certeza.senales_reglamento([{"puntaje": 0.5}, {"puntaje": 0.0}], fuente=1)
    assert certeza.calcular("reglamento", "respondida", sola) < certeza.calcular("reglamento", "respondida", con_rival)


def test_certeza_de_expensas_baja_con_supuestos():
    explicita = certeza.calcular("expensas", "respondida", certeza.senales_expensas(False, False, 3))
    supuesta = certeza.calcular("expensas", "respondida", certeza.senales_expensas(True, True, 3))
    assert explicita == 0.9 and supuesta == pytest.approx(0.55) and certeza.nivel(supuesta) == "baja"


@pytest.mark.parametrize("funcionalidad,resultado", [
    ("reglamento", "derivada"), ("expensas", "sin_datos"), ("reclamo", "reclamo_pendiente"), (None, "error"),
    ("derivar", "respondida"),
])
def test_sin_respuesta_no_hay_certeza(funcionalidad, resultado):
    assert certeza.calcular(funcionalidad, resultado, {"puntaje": 1.0}) is None
    assert certeza.nivel(None) is None


def test_el_turno_guarda_certeza_senales_y_sus_mensajes(monkeypatch):
    r = respondida_reglamento(monkeypatch, mensaje="¿y los animales?")
    fila = turno(r["turno_id"])
    assert r["certeza"] == fila.certeza and 0.5 <= fila.certeza <= 1
    assert fila.senales_certeza["version"] == certeza.VERSION and fila.senales_certeza["secciones"] >= 1
    assert texto(fila.mensaje_usuario_id) == "¿y los animales?" and texto(fila.mensaje_asistente_id) == r["mensaje"]


def test_expensas_con_periodo_supuesto_tiene_menos_certeza(monkeypatch):
    router(monkeypatch, intencion="expensas", que="rubros", periodo="2026-07")
    explicita = atender("rubros de julio", vecino(), None, "web")
    router(monkeypatch, intencion="expensas", que="rubros")
    supuesta = atender("rubros", vecino(), None, "web")
    assert explicita["resultado"] == supuesta["resultado"] == "respondida"
    assert explicita["certeza"] > supuesta["certeza"]
    assert turno(supuesta["turno_id"]).senales_certeza["periodo_supuesto"] is True


def test_una_derivacion_no_tiene_certeza_pero_se_puede_revisar(monkeypatch):
    r = derivada(monkeypatch)
    assert r["certeza"] is None and turno(r["turno_id"]).senales_certeza == {}
    assert [x["turno_id"] for x in revision.listar(admin())] == [r["turno_id"]]


# ------------------------------------------------------------------ revisión


def test_la_fila_trae_pregunta_y_respuesta_y_no_quien_pregunto(monkeypatch):
    r = respondida_reglamento(monkeypatch)
    [fila] = revision.listar(admin())
    assert (fila["turno_id"], fila["pregunta"], fila["respuesta"]) == (r["turno_id"], "¿Se permiten mascotas?", r["mensaje"])
    assert fila["rol"] == "propietario" and fila["revision"] is None
    assert not {"usuario_id", "nombre", "autor_nombre", "email"} & set(fila)


def test_primero_lo_que_el_vecino_voto_mal_despues_lo_dudoso(monkeypatch):
    buena = respondida_reglamento(monkeypatch)["turno_id"]
    der = derivada(monkeypatch)["turno_id"]
    mala = respondida_reglamento(monkeypatch)["turno_id"]
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE turnos SET feedback = -1 WHERE id = :t"), {"t": mala})
        con.execute(text("UPDATE turnos SET certeza = 0.9 WHERE id = :t"), {"t": buena})
    filas = revision.listar(admin())
    assert [f["turno_id"] for f in filas] == [mala, der, buena]
    assert [f["prioridad"] for f in filas] == ["feedback_negativo", "derivada", "otra"]


def test_revisar_guarda_y_pisa(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    revision.revisar(admin(), tid, "incorrecta", "cita_incorrecta", "  Sí, con correa.\x00 ")
    revision.revisar(admin(), tid, "correcta", "dato_incorrecto")  # al ser correcta, el motivo se descarta

    with Session(postgres.engine()) as s:
        [fila] = s.scalars(select(Revision)).all()
    assert (fila.veredicto, fila.motivo, fila.respuesta_correcta) == ("correcta", None, None)
    assert fila.revisor_id == admin().usuario_id and fila.consorcio_id == PALMERAS
    assert revision.listar(admin()) == [] and [f["turno_id"] for f in revision.listar(admin(), "revisadas")] == [tid]


@pytest.mark.parametrize("veredicto,motivo", [("dudosa", None), ("incorrecta", None), ("parcial", "otro")])
def test_veredicto_o_motivo_invalidos(monkeypatch, veredicto, motivo):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    with pytest.raises(revision.RevisionInvalida):
        revision.revisar(admin(), tid, veredicto, motivo)


def test_solo_la_administracion_revisa(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    with pytest.raises(SinPermiso):
        revision.listar(vecino())
    with pytest.raises(SinPermiso):
        revision.revisar(vecino(), tid, "correcta")


def test_la_administracion_de_otro_consorcio_no_ve_ni_revisa(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    otra = resolver_identidad(MULTI, ROBLES)
    assert otra.es_administracion and revision.listar(otra) == []
    with pytest.raises(revision.RespuestaInexistente):
        revision.revisar(otra, tid, "correcta")


def test_reclamos_y_evaluaciones_no_se_revisan(monkeypatch):
    router(monkeypatch, intencion="reclamo", titulo="Ascensor", detalle="No anda")
    reclamo = atender("se rompió el ascensor", vecino(), None, "web")["turno_id"]
    monkeypatch.setattr(config, "fake_guionado", True)
    router(monkeypatch, intencion="reglamento")
    evaluacion = atender("¿Se permiten mascotas?", vecino(), None, "cli", origen="evaluacion")["turno_id"]
    assert revision.listar(admin()) == []
    for tid in (reclamo, evaluacion):
        with pytest.raises(revision.RespuestaInexistente):
            revision.revisar(admin(), tid, "correcta")


def test_la_revision_va_a_mlflow_sin_el_texto(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE turnos SET mlflow_traza_id = 'tr-123' WHERE id = :t"), {"t": tid})
    enviados = []
    monkeypatch.setattr(revision, "registrar_revision", lambda *a: enviados.append(a) or True)
    r = revision.revisar(admin(), tid, "parcial", "incompleta", "texto que no tiene que salir")
    assert enviados == [("tr-123", "parcial", "incompleta")] and r["en_mlflow"] is True


def test_sin_mlflow_la_revision_queda_igual(monkeypatch):
    monkeypatch.setattr(config, "mlflow_tracking_uri", "")
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    assert revision.revisar(admin(), tid, "correcta")["en_mlflow"] is False
    assert revision.listar(admin(), "revisadas")[0]["revision"]["veredicto"] == "correcta"


# ------------------------------------------------------------------ CRM


def test_el_crm_mide_aciertos_y_calibracion_sin_leer_texto(monkeypatch):
    alta = respondida_reglamento(monkeypatch)["turno_id"]
    baja = respondida_reglamento(monkeypatch)["turno_id"]
    der = derivada(monkeypatch)["turno_id"]
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE turnos SET certeza = 0.95 WHERE id = :t"), {"t": alta})
        con.execute(text("UPDATE turnos SET certeza = 0.55 WHERE id = :t"), {"t": baja})
    revision.revisar(admin(), alta, "correcta")
    revision.revisar(admin(), baja, "incorrecta", "dato_incorrecto", "SECRETO-DEL-CONSORCIO")
    revision.revisar(admin(), der, "parcial", "derivo_sin_necesidad")

    ahora = datetime.now(UTC)
    panel = crm.panel(ahora - timedelta(days=1), ahora + timedelta(minutes=1))
    calidad = panel["calidad"]
    assert (calidad["n"], calidad["correctas"], calidad["revisables"]) == (3, 1, 3)
    assert calidad["tasa"] == pytest.approx(1 / 3, abs=1e-4) and calidad["cobertura"] == 1.0
    assert calidad["por_veredicto"] == {"correcta": 1, "parcial": 1, "incorrecta": 1}
    assert calidad["por_motivo"] == {"dato_incorrecto": 1, "derivo_sin_necesidad": 1}
    assert calidad["calibracion"]["alta"] == {"n": 1, "correctas": 1, "tasa": 1.0}
    assert calidad["calibracion"]["baja"] == {"n": 1, "correctas": 0, "tasa": 0.0}
    assert calidad["calibracion"]["sin_certeza"]["n"] == 1
    assert calidad["certeza"]["por_nivel"] == {"alta": 1, "media": 0, "baja": 1}
    assert "SECRETO-DEL-CONSORCIO" not in json.dumps(panel, default=str)
    assert crm.panel(ahora - timedelta(days=1), ahora + timedelta(minutes=1), ROBLES)["calidad"]["n"] == 0


def test_la_pantalla_del_crm_muestra_la_seccion(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    revision.revisar(admin(), tid, "incorrecta", "no_entendio", "SECRETO-DEL-CONSORCIO")
    pagina = cliente_como("demo-plataforma").get("/crm", params={"vista": "calidad"})
    assert pagina.status_code == 200 and "Revisión de la administración" in pagina.text
    assert "Calibración del medidor de certeza" in pagina.text and "SECRETO-DEL-CONSORCIO" not in pagina.text


# ------------------------------------------------------------------ web


def test_la_pantalla_lista_y_guarda(monkeypatch):
    r = respondida_reglamento(monkeypatch)
    cliente = cliente_como(ADMIN, PALMERAS)
    pagina = cliente.get("/revisar")
    assert pagina.status_code == 200 and "¿Se permiten mascotas?" in pagina.text and "Certeza" in pagina.text
    assert 'href="/revisar"' in cliente.get("/chat").text

    guardar = cliente.post(f"/revisar/{r['turno_id']}", data={"veredicto": "parcial", "motivo": "incompleta",
                                                              "respuesta_correcta": "Sí, con correa", "csrf": cliente.csrf},
                           follow_redirects=False)
    assert guardar.status_code == 303 and guardar.headers["location"] == "/revisar"
    revisadas = cliente.get("/revisar?ver=revisadas").text
    assert "Parcial · Incompleta" in revisadas and "Sí, con correa" in revisadas


def test_un_vecino_no_entra_a_revisar(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    cliente = cliente_como(PROP, PALMERAS)
    assert cliente.get("/revisar").status_code == 403
    assert 'href="/revisar"' not in cliente.get("/chat").text
    assert cliente.post(f"/revisar/{tid}", data={"veredicto": "correcta", "csrf": cliente.csrf}).status_code == 403


def test_post_sin_csrf_o_invalido(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    cliente = cliente_como(ADMIN, PALMERAS)
    assert cliente.post(f"/revisar/{tid}", data={"veredicto": "correcta"}).status_code == 403
    assert cliente.post(f"/revisar/{tid}", data={"veredicto": "dudosa", "csrf": cliente.csrf}).status_code == 422
    assert cliente.post("/revisar/abc", data={"veredicto": "correcta", "csrf": cliente.csrf}).status_code == 422
    assert revision.listar(admin(), "revisadas") == []


def test_otro_consorcio_por_la_web_da_404(monkeypatch):
    tid = respondida_reglamento(monkeypatch)["turno_id"]
    otro = cliente_como(MULTI, ROBLES)
    datos = {"veredicto": "correcta", "csrf": otro.csrf, "consorcio_id": PALMERAS}
    assert otro.post(f"/revisar/{tid}", data=datos).status_code == 404
    assert "mascotas" not in otro.get(f"/revisar?consorcio_id={PALMERAS}").text
