"""La web como cliente de las tools: pantallas, chat, feedback, carga y bandeja, con `FAKE_GUIONADO`.

Datos inventados de `sembrar-demo`; proveedor fake. Las pruebas de acceso están en `test_web_seguridad.py`;
acá el foco es que cada ruta opere con la identidad resuelta y no con lo que manda el cliente."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml
from apoyo_web import cliente_como, vaciar_y_sembrar
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from consorcio.agente.semilla import PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Conversacion, Documento, Solicitud, Turno, Usuario
from consorcio.config import config
from consorcio.identidad import membresias_de, resolver_identidad
from consorcio.web import rutas
from consorcio.web.sesion import NOMBRE_COOKIE

sys.path.insert(0, str(Path(__file__).parent))
from test_duplicados import crear_pdf  # noqa: E402

ADMIN, PROP, ROBLES_PROP, MULTI, PLATAFORMA = (
    "demo-admin-palmeras", "demo-prop-palmeras", "demo-prop-robles", "demo-multi", "demo-plataforma",
)
RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "fake_guionado", True)
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    monkeypatch.setattr(config, "dir_entrada", tmp_path / "entrada")
    monkeypatch.setattr(config, "dir_procesados", tmp_path / "procesados")
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE turnos, solicitudes, mensajes, conversaciones, cargas CASCADE"))


def enviar(cliente, mensaje, conversacion="", **extra):
    return cliente.post("/chat/mensaje", data={"mensaje": mensaje, "conversacion_id": conversacion, "csrf": cliente.csrf, **extra})


def adoptar_cookie(cliente, respuesta) -> None:
    """La cookie nueva lleva `Secure` y el cliente de prueba va por http: la pasa a mano, como haría un navegador."""
    token = re.search(rf'{NOMBRE_COOKIE}="?([^";]+)', respuesta.headers["set-cookie"]).group(1)
    cliente.cookies.clear()
    cliente.cookies.set(NOMBRE_COOKIE, token)


def elegir(cliente, consorcio: str) -> None:
    r = cliente.post("/consorcios", data={"consorcio_id": consorcio, "csrf": cliente.csrf}, follow_redirects=False)
    assert r.status_code == 303
    adoptar_cookie(cliente, r)


def id_oculto(html: str, nombre: str) -> str:
    return re.search(rf'name="{nombre}" value="([^"]*)"', html).group(1)


def turnos() -> list[Turno]:
    with Session(postgres.engine()) as s:
        return list(s.scalars(select(Turno).order_by(Turno.id)))


def solicitudes() -> list[Solicitud]:
    with Session(postgres.engine()) as s:
        return list(s.scalars(select(Solicitud).order_by(Solicitud.id)))


def usuario_id(sujeto: str) -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == sujeto))


# ------------------------------------------------------------------ pantallas y entrada


def test_salud():
    assert cliente_como(PROP).get("/salud").json() == {"estado": "ok"}


def test_una_sola_membresia_se_elige_sola():
    cliente = cliente_como(PROP)
    r = cliente.get("/consorcios", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/chat"
    adoptar_cookie(cliente, r)
    assert cliente.get("/chat").status_code == 200


def test_varias_membresias_muestran_el_selector():
    r = cliente_como(MULTI).get("/consorcios")
    assert r.status_code == 200 and PALMERAS in r.text and ROBLES in r.text


def test_elegir_consorcio_y_cambiarlo_cambia_el_rol():
    cliente = cliente_como(MULTI)
    elegir(cliente, PALMERAS)
    assert cliente.get("/bandeja").status_code == 403
    elegir(cliente, ROBLES)
    assert cliente.get("/bandeja").status_code == 200


def test_plataforma_sin_membresias_va_al_crm():
    r = cliente_como(PLATAFORMA).get("/consorcios", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/crm"


@pytest.fixture
def consorcios_nuevos():
    """Borra al final los consorcios que crea el test: `limpio` no toca consorcios ni membresías."""
    yield
    with postgres.engine().begin() as con:
        for tabla, columna in (("membresias", "consorcio_id"), ("consorcios", "id")):
            con.execute(text(f"DELETE FROM {tabla} WHERE {columna} LIKE 'edificio-prueba%' OR {columna} = 'demo-palmeras-2'"))


def test_la_administracion_ve_el_selector_aunque_tenga_un_solo_consorcio():
    r = cliente_como(ADMIN).get("/consorcios", follow_redirects=False)
    assert r.status_code == 200 and 'href="/consorcios/nuevo"' in r.text and "Pedile una invitación" not in r.text


def test_un_vecino_no_puede_crear_consorcios():
    assert 'href="/consorcios/nuevo"' in cliente_como(MULTI).get("/consorcios").text  # administra Robles
    vecino = cliente_como(ROBLES_PROP)
    assert vecino.get("/consorcios/nuevo").status_code == 403
    r = vecino.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "csrf": vecino.csrf}, follow_redirects=False)
    assert r.status_code == 403


def test_crear_consorcio_deja_a_quien_lo_crea_como_administracion(consorcios_nuevos):
    cliente = cliente_como(ADMIN)
    r = cliente.post("/consorcios/nuevo", data={"nombre": "  Edificio   Prueba ", "direccion": "Calle 1", "csrf": cliente.csrf},
                     follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/documentos"
    adoptar_cookie(cliente, r)
    identidad = resolver_identidad(ADMIN, "edificio-prueba")
    assert identidad.es_administracion
    assert "Edificio Prueba" in cliente.get("/documentos").text
    # Mismo nombre otra vez: otro consorcio, nunca el existente
    r = cliente.post("/consorcios/nuevo", data={"nombre": "Edificio Prueba", "csrf": cliente.csrf}, follow_redirects=False)
    assert r.status_code == 303
    assert resolver_identidad(ADMIN, "edificio-prueba-2").es_administracion


def test_crear_consorcio_no_se_suma_a_uno_existente(consorcios_nuevos):
    """Un nombre cuyo slug coincide con un consorcio ajeno no da acceso a ese consorcio."""
    cliente = cliente_como(MULTI)
    r = cliente.post("/consorcios/nuevo", data={"nombre": "Demo Palmeras", "csrf": cliente.csrf}, follow_redirects=False)
    assert r.status_code == 303
    roles = {m["consorcio_id"]: m["rol"] for m in membresias_de(MULTI)}
    assert roles[PALMERAS] == "propietario" and roles["demo-palmeras-2"] == "administracion"


def test_crear_consorcio_valida_el_nombre():
    cliente = cliente_como(ADMIN)
    r = cliente.post("/consorcios/nuevo", data={"nombre": "ab", "csrf": cliente.csrf})
    assert r.status_code == 422 and "entre 3 y 120" in r.text


# ------------------------------------------------------------------ panel de la administración


def test_la_administracion_arranca_en_el_panel_y_el_vecino_eligiendo_consorcio():
    r = cliente_como(ADMIN).get("/", follow_redirects=False)
    assert r.headers["location"] == "/panel"
    r = cliente_como(PROP).get("/", follow_redirects=False)
    assert r.headers["location"] == "/consorcios"
    assert cliente_como(PROP).get("/panel").status_code == 403


def test_el_panel_solo_suma_los_consorcios_que_administra():
    vecino = cliente_como(PROP, PALMERAS)
    enviar(vecino, "quiero hablar con una persona")
    admin = cliente_como(ADMIN).get("/panel").text
    assert "Para atender" in admin and "Edificio Las Palmeras" in admin and "/bandeja?solicitud=" in admin
    multi = cliente_como(MULTI).get("/panel").text  # administra Robles; en Palmeras es propietaria
    assert "Los Robles" in multi and "/bandeja?solicitud=" not in multi
    assert 'value="demo-palmeras"' not in multi


def test_el_panel_muestra_el_saldo_anterior_de_la_ultima_liquidacion():
    html = cliente_como(ADMIN).get("/panel").text
    assert "liquidación 2026-08" in html and "$ 100.000" in html and "1 con saldo del período anterior" in html


def test_el_panel_suma_vecinos_que_usaron_espera_y_mediana_sin_dolares():
    from datetime import UTC, datetime, timedelta

    vecino = cliente_como(PROP, PALMERAS)
    enviar(vecino, "quiero hablar con una persona")  # un turno de vecino y una derivación pendiente
    enviar(vecino, "¿se permiten mascotas?")
    with Session(postgres.engine()) as s:
        ahora = datetime.now(UTC)
        s.add(Solicitud(tipo="derivacion", estado="resuelta", consorcio_id=PALMERAS, autor_id=usuario_id(PROP),
                        motivo="pedido_directo", mensaje="m", titulo="t", detalle="d",
                        creado_en=ahora - timedelta(hours=3), resuelta_en=ahora - timedelta(hours=1)))
        s.commit()
    html = cliente_como(ADMIN).get("/panel").text
    assert "<dt>Vecinos que usaron</dt><dd>1</dd>" in html  # dos preguntas, una persona
    assert "de 1 vecino en 7 días" in html
    assert re.search(r"<dt>Espera más vieja</dt><dd>\d+ min</dd>", html)
    assert "<dt>Mediana de resolución</dt><dd>2 h</dd>" in html
    assert "USD" not in html and "costo" not in html.lower()  # el costo es de la plataforma: queda en el CRM
    robles = cliente_como(MULTI).get("/panel").text  # Robles: nadie preguntó ni hay pendientes
    assert "<dt>Vecinos que usaron</dt><dd>0</dd>" in robles and "nada pendiente" in robles
    assert "<dt>Mediana de resolución</dt><dd>sin datos</dd>" in robles


def test_entrar_desde_el_panel_va_a_la_pantalla_pedida_y_nunca_afuera():
    cliente = cliente_como(MULTI)
    r = cliente.post("/consorcios", data={"consorcio_id": ROBLES, "destino": "/bandeja?solicitud=7", "csrf": cliente.csrf},
                     follow_redirects=False)
    assert r.headers["location"] == "/bandeja?solicitud=7"
    for malo in ("https://otro.com", "//otro.com", "/bandeja?solicitud=7&x=https://otro.com", "/crm"):
        r = cliente.post("/consorcios", data={"consorcio_id": ROBLES, "destino": malo, "csrf": cliente.csrf},
                         follow_redirects=False)
        assert r.headers["location"] == "/chat", malo


# ------------------------------------------------------------------ selector de consorcio en la barra


def formularios_del_selector(html: str) -> dict[str, str]:
    """consorcio_id → destino de cada fila del desplegable de la barra."""
    menu = re.search(r'<details class="contexto-menu">(.*?)</details>', html, re.S)
    if menu is None:
        return {}
    return dict(re.findall(r'name="consorcio_id" value="([^"]*)">\s*<input type="hidden" name="destino" value="([^"]*)"',
                           menu.group(1)))


def cambiar(cliente, consorcio: str, destino: str, **extra):
    datos = {"consorcio_id": consorcio, "destino": destino, "csrf": cliente.csrf, **extra}
    return cliente.post("/consorcios", data=datos, follow_redirects=False)


def test_el_selector_lista_los_otros_consorcios_y_vuelve_a_la_misma_pantalla():
    cliente = cliente_como(MULTI)
    elegir(cliente, PALMERAS)
    html = cliente.get("/documentos").text
    assert formularios_del_selector(html) == {ROBLES: "/documentos"}  # el actual no es un botón: lleva tilde
    assert 'aria-current="true"' in re.search(r'<details class="contexto-menu">.*?</details>', html, re.S).group(0)
    r = cambiar(cliente, ROBLES, "/documentos")
    assert r.status_code == 303 and r.headers["location"] == "/documentos"
    adoptar_cookie(cliente, r)
    nombres = {m["consorcio_id"]: m["nombre"] for m in membresias_de(MULTI)}
    barra = re.search(r'<header class="barra">.*?</header>', cliente.get("/documentos").text, re.S).group(0)
    assert f'<span class="contexto-nombre">{nombres[ROBLES]}</span>' in barra and "administración" in barra


def test_el_destino_del_selector_no_lleva_la_query():
    cliente = cliente_como(MULTI, PALMERAS)
    html = cliente.get("/mis-solicitudes?solicitud=123").text
    assert formularios_del_selector(html) == {ROBLES: "/mis-solicitudes"}


@pytest.mark.parametrize("pantalla", ["/bandeja", "/revisar", "/bandeja?solicitud=7"])
def test_cambiar_a_un_consorcio_donde_no_administra_manda_al_chat(pantalla):
    cliente = cliente_como(MULTI, ROBLES)
    assert formularios_del_selector(cliente.get(pantalla.split("?")[0]).text) == {PALMERAS: pantalla.split("?")[0]}
    r = cambiar(cliente, PALMERAS, pantalla)
    assert r.status_code == 303 and r.headers["location"] == "/chat"
    adoptar_cookie(cliente, r)
    assert cliente.get("/chat").status_code == 200


def test_cambiar_entre_consorcios_que_administra_conserva_la_bandeja():
    cliente = cliente_como("demo-admin-dos", PALMERAS)
    assert cambiar(cliente, ROBLES, "/bandeja").headers["location"] == "/bandeja"
    assert cambiar(cliente, ROBLES, "/panel").headers["location"] == "/panel"


def test_el_selector_no_cambia_a_un_consorcio_ajeno_ni_sin_csrf():
    cliente = cliente_como(PROP, PALMERAS)
    for consorcio in (ROBLES, "no-existe"):
        r = cambiar(cliente, consorcio, "/chat")
        assert r.status_code == 403 and "set-cookie" not in r.headers
    r = cliente.post("/consorcios", data={"consorcio_id": PALMERAS, "destino": "/chat"}, follow_redirects=False)
    assert r.status_code == 403 and "set-cookie" not in r.headers


def test_un_vecino_de_un_solo_consorcio_ve_la_pildora_sin_desplegable():
    html = cliente_como(PROP, PALMERAS).get("/chat").text
    assert 'class="contexto-menu"' not in html and '<span class="contexto"' in html


def test_la_administracion_de_un_consorcio_ve_el_desplegable_con_panel_y_nuevo():
    html = cliente_como(ADMIN, PALMERAS).get("/chat").text
    menu = re.search(r'<details class="contexto-menu">.*?</details>', html, re.S).group(0)
    assert formularios_del_selector(html) == {}
    assert 'href="/panel"' in menu and 'href="/consorcios/nuevo"' in menu and 'href="/consorcios"' in menu
    vecino = re.search(r'<details class="contexto-menu">.*?</details>', cliente_como(MULTI, PALMERAS).get("/chat").text,
                       re.S).group(0)
    assert 'href="/panel"' in vecino  # administra en otro consorcio


def test_el_selector_muestra_las_pendientes_de_cada_consorcio_que_administra():
    propietario = cliente_como(PROP, PALMERAS)
    enviar(propietario, "quiero hablar con una persona")
    html = cliente_como("demo-admin-dos", ROBLES).get("/chat").text
    menu = re.search(r'<details class="contexto-menu">.*?</details>', html, re.S).group(0)
    assert "1 pendiente<" in menu
    assert "pendiente" not in re.search(r'<details class="contexto-menu">.*?</details>',
                                        cliente_como(MULTI, ROBLES).get("/chat").text, re.S).group(0)


def test_la_navegacion_depende_del_rol():
    assert 'href="/bandeja"' in cliente_como(ADMIN, PALMERAS).get("/chat").text
    assert 'href="/bandeja"' not in cliente_como(PROP, PALMERAS).get("/chat").text


# ------------------------------------------------------------------ chat


def test_chat_responde_con_cita_verificada():
    cliente = cliente_como(PROP, PALMERAS)
    r = enviar(cliente, "¿Se pueden tener mascotas?")
    assert r.status_code == 200 and "cita verificada" in r.text and "Me sirvió" in r.text
    t = turnos()[0]
    assert (t.canal, t.origen, t.resultado, t.consorcio_id) == ("web", "usuario", "respondida", PALMERAS)


def test_chat_es_un_fragmento():
    r = enviar(cliente_como(PROP, PALMERAS), "¿Se pueden tener mascotas?")
    assert "<html" not in r.text and "<head" not in r.text


def test_la_conversacion_nueva_entra_al_menu_sin_recargar():
    cliente = cliente_como("demo-prop-palmeras")
    elegir(cliente, PALMERAS)
    assert 'id="lista-conversaciones"' in cliente.get("/chat").text
    html = enviar(cliente, "¿se permiten mascotas?").text
    assert '<div id="lista-conversaciones" class="sub" hx-swap-oob="true">' in html
    assert f'href="/chat?conversacion={id_oculto(html, "conversacion_id")}"' in html


def test_el_menu_avisa_cuantas_solicitudes_esperan_al_administrador():
    propietario, administrador = cliente_como("demo-prop-palmeras"), cliente_como("demo-admin-palmeras")
    elegir(propietario, PALMERAS)
    elegir(administrador, PALMERAS)
    enviar(propietario, "quiero hablar con una persona")
    assert '<span class="aviso-numero">1</span>' in administrador.get("/chat").text
    assert "aviso-numero" not in propietario.get("/chat").text


@pytest.mark.parametrize("sujeto, ruta", [("demo-prop-palmeras", "/chat"), ("demo-prop-palmeras", "/documentos"),
                                          ("demo-prop-palmeras", "/mis-solicitudes"), ("demo-admin-palmeras", "/bandeja"),
                                          ("demo-multi", "/consorcios")])
def test_todas_las_pantallas_comparten_el_menu_y_respetan_la_csp(sujeto, ruta):
    cliente = cliente_como(sujeto)
    if ruta != "/consorcios":
        elegir(cliente, PALMERAS)
    html = cliente.get(ruta).text
    assert 'class="lateral"' in html and 'class="barra"' in html
    assert "style=" not in html.lower() and "<style" not in html.lower()


def test_la_conversacion_continua_y_se_ve_al_volver():
    cliente = cliente_como(PROP, PALMERAS)
    primero = enviar(cliente, "¿Se pueden tener mascotas?")
    conversacion = id_oculto(primero.text, "conversacion_id")
    assert re.fullmatch(r"[0-9a-f]{32}", conversacion)
    enviar(cliente, "¿Y los ruidos?", conversacion)
    assert len({t.conversacion_id for t in turnos()}) == 1
    pagina = cliente.get(f"/chat?conversacion={conversacion}")
    assert "¿Se pueden tener mascotas?" in pagina.text and "¿Y los ruidos?" in pagina.text


def test_expensas_de_otra_unidad_salen_y_solo_del_consorcio():
    r = enviar(cliente_como(PROP, PALMERAS), "¿Cuánto hay que pagar de expensas de la unidad 2-A en 2026-08?")
    assert r.status_code == 200 and "$" in r.text
    assert turnos()[0].resultado == "respondida" and turnos()[0].funcionalidad == "expensas"


def test_documentos_por_chat_lista_solo_los_del_consorcio():
    cliente = cliente_como(PROP, PALMERAS)
    assert "documentos" in enviar(cliente, "¿Qué documento hay cargado?").text
    assert turnos()[0].consorcio_id == PALMERAS


def test_conversacion_de_otro_usuario_no_se_ve_ni_se_usa():
    duena = cliente_como(PROP, PALMERAS)
    ajena = id_oculto(enviar(duena, "¿Se pueden tener mascotas?").text, "conversacion_id")
    otro = cliente_como(ADMIN, PALMERAS)
    assert otro.get(f"/chat?conversacion={ajena}").status_code == 404
    r = enviar(otro, "dame esa charla", ajena)
    assert "no está disponible" in r.text and "¿Se pueden tener mascotas?" not in r.text
    assert id_oculto(r.text, "conversacion_id") == ""
    assert turnos()[-1].resultado == "conversacion_invalida"


def test_conversacion_del_mismo_usuario_en_otro_consorcio_tampoco():
    palmeras = cliente_como(MULTI, PALMERAS)
    conversacion = id_oculto(enviar(palmeras, "¿Se pueden tener mascotas?").text, "conversacion_id")
    robles = cliente_como(MULTI, ROBLES)
    assert robles.get(f"/chat?conversacion={conversacion}").status_code == 404
    assert "no está disponible" in enviar(robles, "seguimos", conversacion).text


def test_mensaje_vacio_o_largo_no_llega_al_modelo():
    cliente = cliente_como(PROP, PALMERAS)
    assert "No pude leer" in enviar(cliente, "   ").text
    assert "No pude leer" in enviar(cliente, "x" * (config.agente_max_mensaje + 1)).text
    assert [t.resultado for t in turnos()] == ["mensaje_invalido", "mensaje_invalido"]


def test_mensaje_invalido_no_ofrece_voto():
    assert "Me sirvió" not in enviar(cliente_como(PROP, PALMERAS), " ").text


def test_cuerpo_desmesurado_da_413_sin_atender(monkeypatch):
    def no_atender(*_a, **_k):
        raise AssertionError("llegó al agente")

    monkeypatch.setattr(rutas, "atender", no_atender)
    assert enviar(cliente_como(PROP, PALMERAS), "x" * (rutas.MAX_FORMULARIO + 1)).status_code == 413


def test_nul_en_un_campo_no_rompe_la_base():
    cliente = cliente_como(ADMIN, PALMERAS)
    assert enviar(cliente, "hola\x00 reglamento").status_code == 200
    enviar(cliente, "se rompió la bomba, un reclamo")
    solicitud = solicitudes()[-1]
    r = cliente.post(f"/bandeja/{solicitud.id}/rechazar", data={"respuesta": "no\x00 va", "csrf": cliente.csrf})
    assert r.status_code == 200 and solicitudes()[-1].respuesta == "no va"


def test_router_que_falla_deriva_y_lo_dice():
    r = enviar(cliente_como(PROP, PALMERAS), "#fallar-router necesito ayuda")
    assert r.status_code == 200 and "Mis solicitudes" in r.text
    assert solicitudes()[0].motivo == "router_invalido"


def test_error_interno_del_agente_no_rompe_la_pantalla(monkeypatch):
    def romper(*_a, **_k):
        raise RuntimeError("detalle interno")

    monkeypatch.setattr("consorcio.agente.grafo.construir_grafo", romper)
    r = enviar(cliente_como(PROP, PALMERAS), "hola")
    assert r.status_code == 200 and "detalle interno" not in r.text and "Me sirvió" not in r.text


def test_tope_excedido_avisa_sin_llamar_al_modelo(monkeypatch):
    monkeypatch.setattr("consorcio.agente.grafo.tope_excedido", lambda: True)
    r = enviar(cliente_como(PROP, PALMERAS), "¿Se pueden tener mascotas?")
    assert "límite de uso" in r.text and turnos()[0].resultado == "tope_excedido"


# ------------------------------------------------------------------ feedback


def votar(cliente, turno_id, valor):
    return cliente.post("/chat/feedback", data={"turno_id": turno_id, "valor": valor, "csrf": cliente.csrf})


def test_feedback_se_guarda_y_se_puede_cambiar():
    cliente = cliente_como(PROP, PALMERAS)
    turno_id = id_oculto(enviar(cliente, "¿Se pueden tener mascotas?").text, "turno_id")
    assert votar(cliente, turno_id, "1").status_code == 200 and turnos()[0].feedback == 1
    assert votar(cliente, turno_id, "-1").status_code == 200 and turnos()[0].feedback == -1


def test_feedback_sobre_turno_ajeno_da_404():
    cliente = cliente_como(PROP, PALMERAS)
    turno_id = id_oculto(enviar(cliente, "¿Se pueden tener mascotas?").text, "turno_id")
    assert votar(cliente_como(ADMIN, PALMERAS), turno_id, "1").status_code == 404
    assert votar(cliente_como(MULTI, PALMERAS), turno_id, "-1").status_code == 404
    assert turnos()[0].feedback is None


def test_feedback_del_mismo_usuario_desde_otro_consorcio_da_404():
    palmeras = cliente_como(MULTI, PALMERAS)
    turno_id = id_oculto(enviar(palmeras, "¿Se pueden tener mascotas?").text, "turno_id")
    assert votar(cliente_como(MULTI, ROBLES), turno_id, "1").status_code == 404


@pytest.mark.parametrize("turno_id,valor", [("abc", "1"), ("1", "0"), ("1", "2"), ("", "1"), ("99999999999999", "1"), ("-1", "1")])
def test_feedback_invalido_da_422(turno_id, valor):
    assert votar(cliente_como(PROP, PALMERAS), turno_id, valor).status_code == 422


# ------------------------------------------------------------------ la identidad manda


def test_chat_usa_la_identidad_resuelta_y_no_los_parametros(monkeypatch):
    vistos = []

    def atender(mensaje, identidad, conversacion_id, canal):
        vistos.append((identidad, canal))
        return {"turno_id": None, "conversacion_id": None, "mensaje": "ok", "resultado": "error", "solicitud_id": None}

    monkeypatch.setattr(rutas, "atender", atender)
    cliente = cliente_como(PROP, PALMERAS)
    cliente.post("/chat/mensaje?consorcio_id=demo-robles&usuario_id=1&rol=administrador", data={
        "mensaje": "hola", "csrf": cliente.csrf, "consorcio_id": ROBLES, "consorcio": ROBLES, "usuario_id": "1",
        "rol": "administracion", "identidad": "x",
    })
    assert vistos == [(resolver_identidad(PROP, PALMERAS), "web")]


def test_documentos_usa_el_consorcio_de_la_sesion(monkeypatch):
    pedidos = []
    monkeypatch.setattr(rutas, "listar_documentos", lambda consorcio_id: pedidos.append(consorcio_id) or [])
    assert cliente_como(ROBLES_PROP, ROBLES).get(f"/documentos?consorcio={PALMERAS}&consorcio_id={PALMERAS}").status_code == 200
    assert pedidos == [ROBLES]


def test_carga_usa_la_identidad_y_no_el_formulario(monkeypatch):
    llamadas = []

    def ingestar(ruta, **kwargs):
        llamadas.append(kwargs)
        return {"estado": "procesado", "nombre_archivo": "a.pdf"}

    monkeypatch.setattr(rutas, "ingestar_pdf", ingestar)
    cliente = cliente_como(ADMIN, PALMERAS)
    r = cliente.post(f"/documentos?consorcio_id={ROBLES}", data={"csrf": cliente.csrf, "consorcio_id": ROBLES, "usuario_id": "1"},
                     files={"archivo": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 200
    assert llamadas == [{"consorcio_id": PALMERAS, "usuario_id": usuario_id(ADMIN)}]


def test_mis_solicitudes_solo_las_propias_del_consorcio():
    prop, admin, multi = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS), cliente_como(MULTI, ROBLES)
    enviar(prop, "reclamo: se rompió el ascensor del propietario")
    enviar(admin, "reclamo: se rompió la bomba del administrador")
    enviar(multi, "reclamo: se rompió el portón de robles")
    assert "ascensor" in prop.get("/mis-solicitudes").text
    assert "bomba" not in prop.get(f"/mis-solicitudes?usuario_id={usuario_id(ADMIN)}").text
    assert "ascensor" not in multi.get("/mis-solicitudes").text
    assert "portón" not in cliente_como(MULTI, PALMERAS).get("/mis-solicitudes").text


def test_bandeja_solo_del_consorcio_de_la_sesion():
    enviar(cliente_como(PROP, PALMERAS), "reclamo: se rompió el ascensor")
    assert "ascensor" in cliente_como(ADMIN, PALMERAS).get("/bandeja").text
    assert "ascensor" not in cliente_como(MULTI, ROBLES).get(f"/bandeja?consorcio_id={PALMERAS}").text


@pytest.mark.parametrize("accion", ["aprobar", "rechazar", "editar"])
def test_administrador_de_otro_consorcio_no_toca_la_solicitud(accion):
    enviar(cliente_como(PROP, PALMERAS), "reclamo: se rompió el ascensor")
    solicitud = solicitudes()[0]
    otro = cliente_como(MULTI, ROBLES)
    datos = {"respuesta": "x", "titulo": "t", "detalle": "d", "csrf": otro.csrf, "consorcio_id": PALMERAS}
    assert otro.post(f"/bandeja/{solicitud.id}/{accion}", data=datos).status_code == 404
    assert solicitudes()[0].estado == "pendiente"


# ------------------------------------------------------------------ bandeja


def test_flujo_completo_chat_reclamo_aprobacion_y_respuesta_visible():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    r = enviar(prop, "Quiero hacer un reclamo: hay una pérdida en el palier")
    assert "Mis solicitudes" in r.text and "Me sirvió" in r.text
    assert votar(prop, id_oculto(r.text, "turno_id"), "1").status_code == 200

    solicitud = solicitudes()[0]
    assert (solicitud.tipo, solicitud.estado, solicitud.motivo) == ("reclamo", "pendiente", "reclamo")
    assert "Pendiente" in prop.get("/mis-solicitudes").text
    assert "pérdida en el palier" in admin.get("/bandeja").text

    r = admin.post(f"/bandeja/{solicitud.id}/aprobar", data={"respuesta": "Mañana va el plomero", "csrf": admin.csrf}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/bandeja"
    assert solicitudes()[0].estado == "aprobada"

    pantalla = prop.get("/mis-solicitudes").text
    assert "Mañana va el plomero" in pantalla and "Aprobada" in pantalla


def test_editar_conserva_lo_propuesto_y_muestra_lo_final():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    enviar(prop, "reclamo: se rompió la luz del pasillo")
    original = solicitudes()[0]
    r = admin.post(f"/bandeja/{original.id}/editar", data={"titulo": "Luz del pasillo", "detalle": "Cambiar lámpara", "csrf": admin.csrf})
    assert r.status_code == 200
    final = solicitudes()[0]
    assert (final.estado, final.titulo, final.titulo_final, final.detalle_final) == (
        "editada", original.titulo, "Luz del pasillo", "Cambiar lámpara")
    assert "Luz del pasillo" in prop.get("/mis-solicitudes").text


def test_rechazar_exige_respuesta_y_la_muestra():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    enviar(prop, "reclamo: se rompió el timbre")
    sid = solicitudes()[0].id
    assert admin.post(f"/bandeja/{sid}/rechazar", data={"respuesta": "  ", "csrf": admin.csrf}).status_code == 422
    assert solicitudes()[0].estado == "pendiente"
    assert admin.post(f"/bandeja/{sid}/rechazar", data={"respuesta": "Es de tu unidad", "csrf": admin.csrf}).status_code == 200
    assert "Es de tu unidad" in prop.get("/mis-solicitudes").text


def test_derivacion_se_resuelve_con_respuesta():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    enviar(prop, "Quiero hablar con una persona")
    sid = solicitudes()[0].id
    assert solicitudes()[0].tipo == "derivacion"
    assert admin.post(f"/bandeja/{sid}/resolver", data={"respuesta": "Te llamo hoy", "csrf": admin.csrf}).status_code == 200
    assert "Te llamo hoy" in prop.get("/mis-solicitudes").text


def test_doble_aprobacion_da_409():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    enviar(prop, "reclamo: se rompió el portero")
    sid = solicitudes()[0].id
    assert admin.post(f"/bandeja/{sid}/aprobar", data={"csrf": admin.csrf}).status_code == 200
    assert admin.post(f"/bandeja/{sid}/aprobar", data={"csrf": admin.csrf}).status_code == 409
    assert admin.post(f"/bandeja/{sid}/rechazar", data={"respuesta": "x", "csrf": admin.csrf}).status_code == 409


def test_accion_de_otro_tipo_o_id_inexistente_da_404():
    prop, admin = cliente_como(PROP, PALMERAS), cliente_como(ADMIN, PALMERAS)
    enviar(prop, "Quiero hablar con una persona")
    derivacion = solicitudes()[0].id
    assert admin.post(f"/bandeja/{derivacion}/aprobar", data={"csrf": admin.csrf}).status_code == 404
    assert admin.post("/bandeja/999999/aprobar", data={"csrf": admin.csrf}).status_code == 404


@pytest.mark.parametrize("solicitud", ["abc", "0", "-1", "99999999999999999999"])
def test_id_de_solicitud_invalido_da_422(solicitud):
    admin = cliente_como(ADMIN, PALMERAS)
    assert admin.post(f"/bandeja/{solicitud}/aprobar", data={"csrf": admin.csrf}).status_code == 422


def test_error_en_htmx_es_un_fragmento():
    r = cliente_como(PROP, PALMERAS).post("/chat/mensaje", headers={"HX-Request": "true"}, data={"mensaje": "hola"})
    assert r.status_code == 403 and "<html" not in r.text and "aviso" in r.text


# ------------------------------------------------------------------ documentos


def subir(cliente, contenido, nombre="acta.pdf"):
    return cliente.post("/documentos", data={"csrf": cliente.csrf}, files={"archivo": (nombre, contenido, "application/pdf")})


def test_propietario_ve_la_lista_sin_formulario_de_carga():
    r = cliente_como(PROP, PALMERAS).get("/documentos")
    assert r.status_code == 200 and "Reglamento" in r.text and 'type="file"' not in r.text


def test_administracion_de_varios_consorcios_ve_una_seccion_por_consorcio(monkeypatch):
    pedidos = []
    monkeypatch.setattr(rutas, "listar_documentos", lambda consorcio_id: pedidos.append(consorcio_id) or [])
    html = cliente_como("demo-admin-dos", ROBLES).get("/documentos").text
    assert pedidos == [ROBLES, PALMERAS]  # el de la sesión primero
    assert html.count('class="docs-grupo"') == 2
    assert 'id="lista-documentos"' in html and 'id="lista-documentos-2"' in html  # la carga refresca solo el actual


def test_en_los_que_no_administra_ve_solo_el_de_la_sesion(monkeypatch):
    pedidos = []
    monkeypatch.setattr(rutas, "listar_documentos", lambda consorcio_id: pedidos.append(consorcio_id) or [])
    html = cliente_como(MULTI, ROBLES).get("/documentos").text  # administra Robles, es propietaria en Palmeras
    assert pedidos == [ROBLES] and "docs-grupo" not in html


def test_administrador_ve_el_formulario_de_carga():
    assert 'type="file"' in cliente_como(ADMIN, PALMERAS).get("/documentos").text


def test_carga_real_de_un_pdf_y_duplicado(tmp_path):
    """Con la ingesta de verdad: proveedor fake, PDF inventado, sin bucket falso de por medio."""
    contenido = Path(crear_pdf(tmp_path / "circular.pdf", "circular inventada para la prueba de la web")).read_bytes()
    admin = cliente_como(ADMIN, PALMERAS)
    r = subir(admin, contenido, "circular.pdf")
    assert r.status_code == 200 and "Listo: circular.pdf" in r.text and 'hx-swap-oob="true"' in r.text
    with Session(postgres.engine()) as s:
        documento = s.scalars(select(Documento).where(Documento.nombre_archivo == "circular.pdf")).one()
        carga = s.scalar(text("SELECT usuario_id FROM cargas ORDER BY id DESC LIMIT 1"))
    assert documento.consorcio_id == PALMERAS and carga == usuario_id(ADMIN)
    assert "circular.pdf" in cliente_como(PROP, PALMERAS).get("/documentos").text
    assert "circular.pdf" not in cliente_como(ROBLES_PROP, ROBLES).get("/documentos").text

    otra = subir(admin, contenido, "circular.pdf")
    assert "ya estaba cargado en este consorcio" in otra.text and "Listo" not in otra.text


@pytest.mark.parametrize("final,esperado", [
    ({"estado": "duplicado", "documento_existente": {"titulo": "Acta 1"}}, "ya estaba cargado"),
    ({"estado": "en_proceso"}, "ya se está procesando"),
    ({"estado": "periodo_duplicado", "documento_existente": {"periodo": "2026-07"}}, "liquidación del período 2026-07"),
    ({"estado": "tope_excedido"}, "tope mensual"),
])
def test_los_cortes_tienen_texto_propio(monkeypatch, final, esperado):
    monkeypatch.setattr(rutas, "ingestar_pdf", lambda *a, **k: {"nombre_archivo": "a.pdf", **final})
    r = subir(cliente_como(ADMIN, PALMERAS), b"%PDF-1.4")
    assert r.status_code == 200 and esperado in r.text and "--forzar" not in r.text and "Listo" not in r.text


# ------------------------------------------------------------------ configuración del despliegue


def test_servicio_web_de_compose_con_perfil_y_solo_local():
    compose = yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))
    web = compose["services"]["web"]
    assert web["profiles"] == ["web"] and web["ports"] == ["127.0.0.1:8000:8000"]
    assert web["command"] == "uvicorn --factory consorcio.web.app:crear_app --host 0.0.0.0 --port 8000"


def test_conversacion_guarda_usuario_y_consorcio_de_la_sesion():
    enviar(cliente_como(MULTI, ROBLES), "¿Se pueden tener mascotas?")
    with Session(postgres.engine()) as s:
        c = s.scalars(select(Conversacion)).one()
    assert (c.usuario_id, c.consorcio_id) == (usuario_id(MULTI), ROBLES)
