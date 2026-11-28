"""Elevar a la administración (objetivo C de docs/producto/plan-tanda-perfiles.md): el botón bajo cada respuesta
del chat (`POST /chat/elevar`) y el mensaje directo desde Mis solicitudes (`POST /mis-solicitudes`).

Datos inventados de `sembrar-demo`, proveedor fake con `FAKE_GUIONADO`."""
from __future__ import annotations

import re
import threading

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from consorcio.agente import bandeja
from consorcio.agente.semilla import PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Solicitud, Turno
from consorcio.config import config
from consorcio.identidad import resolver_identidad

ADMIN, PROP, MULTI = "demo-admin-palmeras", "demo-prop-palmeras", "demo-multi"
BOTON = "Pasar a la administración"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, monkeypatch):
    monkeypatch.setattr(config, "fake_guionado", True)
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE turnos, solicitudes, mensajes, conversaciones CASCADE"))


def enviar(cliente, mensaje, conversacion=""):
    return cliente.post("/chat/mensaje", data={"mensaje": mensaje, "conversacion_id": conversacion, "csrf": cliente.csrf})


def elevar(cliente, turno_id, nota="", **extra):
    return cliente.post("/chat/elevar", data={"turno_id": str(turno_id), "nota": nota, "csrf": cliente.csrf, **extra})


def escribir(cliente, asunto, mensaje, **extra):
    return cliente.post("/mis-solicitudes", data={"asunto": asunto, "mensaje": mensaje, "csrf": cliente.csrf, **extra},
                        follow_redirects=False)


def turno_id(html: str) -> str:
    return re.search(r'name="turno_id" value="([^"]*)"', html).group(1)


def solicitudes() -> list[Solicitud]:
    with Session(postgres.engine()) as s:
        return list(s.scalars(select(Solicitud).order_by(Solicitud.id)))


def turnos() -> list[Turno]:
    with Session(postgres.engine()) as s:
        return list(s.scalars(select(Turno).order_by(Turno.id)))


# ------------------------------------------------------------------ C1: el botón bajo la respuesta


def test_un_clic_crea_una_derivacion_y_el_doble_clic_no_suma_otra():
    vecino, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    respuesta = enviar(vecino, "¿Se pueden tener mascotas?").text
    assert BOTON in respuesta and 'hx-post="/chat/elevar"' in respuesta
    turno = turno_id(respuesta)

    primero, segundo = elevar(vecino, turno), elevar(vecino, turno)
    assert primero.status_code == segundo.status_code == 200
    [s] = solicitudes()
    assert (s.tipo, s.motivo, s.estado, s.consorcio_id) == ("derivacion", "elevada_usuario", "pendiente", PALMERAS)
    assert s.mensaje == s.titulo == "¿Se pueden tener mascotas?" and "cita verificada" in s.detalle
    assert turnos()[0].solicitud_id == s.id and turnos()[0].resultado == "respondida"  # el resultado no cambia
    for r in (primero, segundo):
        assert "Lo pasaste a la administración" in r.text and f'href="/mis-solicitudes?solicitud={s.id}"' in r.text
        assert "<html" not in r.text and BOTON not in r.text

    bandeja_html = admin.get("/bandeja").text
    assert "¿Se pueden tener mascotas?" in bandeja_html and "lo elevó el vecino desde una respuesta" in bandeja_html
    assert "¿Se pueden tener mascotas?" in vecino.get(f"/mis-solicitudes?solicitud={s.id}").text


def test_la_nota_se_suma_al_mensaje_y_tiene_tope():
    vecino = cliente_como(PROP, PALMERAS)
    turno = turno_id(enviar(vecino, "¿Se pueden tener mascotas?").text)
    assert elevar(vecino, turno, "x" * (bandeja.MAX_NOTA + 1)).status_code == 422 and solicitudes() == []
    assert elevar(vecino, turno, "Tengo un gato chico").status_code == 200
    assert solicitudes()[0].mensaje == "¿Se pueden tener mascotas?\n\nAgregó: Tengo un gato chico"


def test_dos_elevaciones_en_paralelo_dejan_una_sola_solicitud():
    vecino = cliente_como(PROP, PALMERAS)
    turno = int(turno_id(enviar(vecino, "¿Se pueden tener mascotas?").text))
    identidad = resolver_identidad(PROP, PALMERAS)
    salidas, barrera = [], threading.Barrier(4)

    def correr():
        barrera.wait()
        salidas.append(bandeja.elevar_turno(identidad, turno))

    hilos = [threading.Thread(target=correr) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    [s] = solicitudes()
    assert {x["solicitud_id"] for x in salidas} == {s.id} and sum(x["nueva"] for x in salidas) == 1


@pytest.mark.parametrize("quien, consorcio", [(ADMIN, PALMERAS), (MULTI, PALMERAS), (PROP, "inexistente")])
def test_turno_ajeno_de_otro_consorcio_o_inexistente_da_404_y_no_crea_nada(quien, consorcio):
    turno = turno_id(enviar(cliente_como(PROP, PALMERAS), "¿Se pueden tener mascotas?").text)
    if consorcio == "inexistente":
        r = elevar(cliente_como(PROP, PALMERAS), 999999)
    else:
        r = elevar(cliente_como(quien, consorcio), turno)
    assert r.status_code == 404 and solicitudes() == []


def test_el_mismo_usuario_desde_otro_consorcio_da_404():
    turno = turno_id(enviar(cliente_como(MULTI, PALMERAS), "¿Se pueden tener mascotas?").text)
    assert elevar(cliente_como(MULTI, ROBLES), turno).status_code == 404 and solicitudes() == []


def test_sin_csrf_da_403_y_no_crea_nada():
    vecino = cliente_como(PROP, PALMERAS)
    turno = turno_id(enviar(vecino, "¿Se pueden tener mascotas?").text)
    r = vecino.post("/chat/elevar", data={"turno_id": turno})
    assert r.status_code == 403 and solicitudes() == []


@pytest.mark.parametrize("valor", ["abc", "", "0", "-1", "99999999999999"])
def test_turno_id_invalido_da_422(valor):
    assert elevar(cliente_como(PROP, PALMERAS), valor).status_code == 422


def test_un_turno_derivado_no_muestra_el_boton_y_devuelve_su_solicitud():
    vecino = cliente_como(PROP, PALMERAS)
    respuesta = enviar(vecino, "quiero hablar con una persona").text
    [s] = solicitudes()
    assert BOTON not in respuesta and f'href="/mis-solicitudes?solicitud={s.id}"' in respuesta
    r = elevar(vecino, turno_id(respuesta))
    assert r.status_code == 200 and f"n.º {s.id}" in r.text and len(solicitudes()) == 1


def test_un_reclamo_no_muestra_el_boton():
    respuesta = enviar(cliente_como(PROP, PALMERAS), "Quiero hacer un reclamo: hay una pérdida en el palier").text
    assert BOTON not in respuesta and "Mis solicitudes" in respuesta


def test_un_mensaje_invalido_no_se_eleva():
    vecino = cliente_como(PROP, PALMERAS)
    assert BOTON not in enviar(vecino, " ").text
    [t] = turnos()
    assert t.resultado == "mensaje_invalido" and elevar(vecino, t.id).status_code == 422 and solicitudes() == []


def test_con_el_tope_excedido_se_puede_elevar(monkeypatch):
    monkeypatch.setattr("consorcio.agente.grafo.tope_excedido", lambda: True)
    vecino = cliente_como(PROP, PALMERAS)
    respuesta = enviar(vecino, "¿Se pueden tener mascotas?").text
    assert BOTON in respuesta and "Me sirvió" not in respuesta
    assert elevar(vecino, turno_id(respuesta)).status_code == 200
    assert solicitudes()[0].mensaje == "¿Se pueden tener mascotas?"


def test_el_boton_y_el_voto_aparecen_al_reabrir_la_conversacion():
    vecino = cliente_como(PROP, PALMERAS)
    primero = enviar(vecino, "¿Se pueden tener mascotas?").text
    conversacion = re.search(r'name="conversacion_id" value="([0-9a-f]{32})"', primero).group(1)
    pagina = vecino.get(f"/chat?conversacion={conversacion}").text
    assert BOTON in pagina and "Me sirvió" in pagina and f'value="{turno_id(primero)}"' in pagina

    vecino.post("/chat/feedback", data={"turno_id": turno_id(primero), "valor": "1", "csrf": vecino.csrf})
    elevar(vecino, turno_id(primero))
    pagina = vecino.get(f"/chat?conversacion={conversacion}").text
    assert BOTON not in pagina and "Lo pasaste a la administración" in pagina
    assert "Gracias por avisar que te sirvió." in pagina


def test_la_administracion_puede_elevar_su_propia_pregunta_y_verla_en_su_bandeja():
    admin = cliente_como(ADMIN, PALMERAS)
    elevar(admin, turno_id(enviar(admin, "¿Se pueden tener mascotas?").text))
    assert "lo elevó el vecino desde una respuesta" in admin.get("/bandeja").text


def test_lo_que_eleva_multi_en_palmeras_no_aparece_en_la_bandeja_de_robles():
    multi = cliente_como(MULTI, PALMERAS)
    elevar(multi, turno_id(enviar(multi, "¿Se pueden tener mascotas?").text))
    assert solicitudes()[0].consorcio_id == PALMERAS
    assert "¿Se pueden tener mascotas?" not in cliente_como(MULTI, ROBLES).get("/bandeja").text


def test_pasado_el_cupo_diario_da_429(monkeypatch):
    monkeypatch.setattr(bandeja, "CUPO_DIARIO", 2)
    vecino = cliente_como(PROP, PALMERAS)
    ids = [turno_id(enviar(vecino, "¿Se pueden tener mascotas?").text) for _ in range(3)]
    assert [elevar(vecino, t).status_code for t in ids[:2]] == [200, 200]
    r = elevar(vecino, ids[2])
    assert r.status_code == 429 and "muchos mensajes" in r.text and len(solicitudes()) == 2
    assert elevar(vecino, ids[0]).status_code == 200  # repetir una ya elevada no gasta cupo
    assert escribir(vecino, "Portón", "El portón no cierra bien").status_code == 429


# ------------------------------------------------------------------ C2: mensaje directo desde Mis solicitudes


def test_mis_solicitudes_ofrece_escribirle_a_la_administracion():
    html = cliente_como(PROP, PALMERAS).get("/mis-solicitudes").text
    assert "Escribirle a la administración" in html and 'action="/mis-solicitudes"' in html


def test_el_mensaje_directo_crea_una_derivacion_sin_turno_aun_con_el_tope_excedido(monkeypatch):
    monkeypatch.setattr("consorcio.agente.grafo.tope_excedido", lambda: True)
    vecino, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    r = escribir(vecino, "Portón roto", "El portón de la cochera no cierra desde ayer.")
    [s] = solicitudes()
    assert r.status_code == 303 and r.headers["location"] == f"/mis-solicitudes?solicitud={s.id}"
    assert (s.tipo, s.motivo, s.titulo, s.mensaje) == ("derivacion", "pedido_directo", "Portón roto",
                                                       "El portón de la cochera no cierra desde ayer.")
    assert turnos() == []
    assert "Le escribiste a la administración" in vecino.get(r.headers["location"]).text
    assert "mensaje directo" in admin.get("/bandeja").text


@pytest.mark.parametrize("asunto, mensaje", [("ab", "El portón no cierra bien"), ("Portón", "corto"),
                                             ("x" * 121, "El portón no cierra bien"), ("Portón", "x" * 2001),
                                             ("", ""), ("   ", "          ")])
def test_el_mensaje_directo_valida_los_largos(asunto, mensaje):
    r = escribir(cliente_como(PROP, PALMERAS), asunto, mensaje)
    assert r.status_code == 422 and solicitudes() == []
    assert "El asunto va de 3 a 120 caracteres" in r.text and 'class="lateral"' in r.text


def test_el_mensaje_directo_conserva_lo_escrito_si_falla():
    r = escribir(cliente_como(PROP, PALMERAS), "Portón", "corto")
    assert 'value="Portón"' in r.text and ">corto</textarea>" in r.text and "<details class=\"escribir\" open>" in r.text


def test_el_mensaje_directo_sin_csrf_da_403():
    vecino = cliente_como(PROP, PALMERAS)
    r = vecino.post("/mis-solicitudes", data={"asunto": "Portón", "mensaje": "El portón no cierra bien"},
                    follow_redirects=False)
    assert r.status_code == 403 and solicitudes() == []


def test_el_mensaje_directo_va_al_consorcio_de_la_sesion():
    escribir(cliente_como(MULTI, ROBLES), "Portón", "El portón no cierra bien", consorcio_id=PALMERAS)
    assert solicitudes()[0].consorcio_id == ROBLES


# ------------------------------------------------------------------ CRM: solo el motivo, nunca el texto


def test_el_crm_cuenta_los_motivos_nuevos_sin_texto():
    from datetime import UTC, datetime, timedelta

    from consorcio import crm

    vecino = cliente_como(PROP, PALMERAS)
    elevar(vecino, turno_id(enviar(vecino, "¿Se pueden tener mascotas?").text))
    escribir(vecino, "Portón secreto", "El portón no cierra bien")
    ahora = datetime.now(UTC)
    elevaciones = crm.panel(ahora - timedelta(days=1), ahora + timedelta(minutes=1))["elevaciones"]
    assert elevaciones["por_motivo"] == {"elevada_usuario": 1, "pedido_directo": 1}
    assert "Portón secreto" not in str(elevaciones) and "mascotas" not in str(elevaciones)
    html = cliente_como("demo-plataforma").get("/crm", params={"vista": "calidad"}).text
    assert "Portón secreto" not in html and "mascotas" not in html
