"""Controles de acceso de la web: sesión, CSRF, consorcio, cookies, cargas y cabeceras.

Datos inventados de `sembrar-demo`; proveedor fake. Los fragmentos de HTML se comparan contra lo que
ve el navegador: una prueba de escape que mire el texto crudo de la plantilla no prueba nada."""
from __future__ import annotations

import base64
import json
import re
import secrets
import time
from pathlib import Path

import pytest
from apoyo_web import cliente_como, manda_a_entrar, vaciar_y_sembrar
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session
from starlette.requests import Request

from consorcio.agente.semilla import PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.almacen.postgres import Membresia, Usuario
from consorcio.config import config
from consorcio.web import crm_rutas, rutas
from consorcio.web.app import crear_app
from consorcio.web.sesion import NOMBRE_COOKIE, Sesion, firmar_sesion, token_csrf

ADMIN, PROP, MULTI, PLATAFORMA = "demo-admin-palmeras", "demo-prop-palmeras", "demo-multi", "demo-plataforma"
PDF_CHICO = b"%PDF-1.4\n%%EOF\n"
PLANTILLAS = Path(rutas.__file__).parent / "plantillas"


@pytest.fixture(scope="module")
def base():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def limpio(base, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "dir_entrada", tmp_path)
    monkeypatch.setattr(config, "web_selector_usuarios", False)
    with postgres.engine().begin() as con:
        con.execute(text("TRUNCATE turnos, solicitudes, mensajes, conversaciones CASCADE"))


@pytest.fixture
def carga(monkeypatch):
    """Reemplaza la ingesta y anota con qué la llamaron: acá se prueba lo que pasa antes de ella."""
    llamadas = []

    def ingestar(ruta, **kwargs):
        llamadas.append({"ruta": ruta, "contenido": Path(ruta).read_bytes(), **kwargs})
        return {"estado": "procesado", "nombre_archivo": Path(ruta).name}

    monkeypatch.setattr(rutas, "ingestar_pdf", ingestar)
    return llamadas


@pytest.fixture
def sin_leer_cuerpo(monkeypatch):
    """Si alguien llega a parsear el formulario, el pedido revienta y el test falla."""
    def no_leer(_request, **_kwargs):
        raise AssertionError("leyó el cuerpo")

    monkeypatch.setattr(Request, "form", no_leer)


def rutas_de_la_app() -> list[tuple[str, str]]:
    """De los routers y no de `app.routes`: según la versión de FastAPI ahí no están las rutas sueltas."""
    pares = []
    for ruta in [*rutas.router.routes, *crm_rutas.router.routes]:
        assert isinstance(ruta, APIRoute)
        camino = ruta.path.replace("{solicitud_id}", "1")
        pares += [(metodo, camino) for metodo in sorted(ruta.methods) if metodo != "HEAD"]
    return pares


def cookie_con(sesion: Sesion) -> TestClient:
    cliente = TestClient(crear_app())
    cliente.cookies.set(NOMBRE_COOKIE, firmar_sesion(sesion))
    cliente.csrf = token_csrf(sesion)
    return cliente


def sesion_de(sujeto: str, consorcio: str | None = PALMERAS, proveedor: str = "prueba", emitida: int | None = None) -> Sesion:
    return Sesion(sujeto, consorcio, proveedor, "nonce-de-prueba", int(time.time()) if emitida is None else emitida)


def subir(cliente, contenido=PDF_CHICO, nombre="acta.pdf", tipo="application/pdf", **kwargs):
    return cliente.post("/documentos", data={"csrf": cliente.csrf}, files={"archivo": (nombre, contenido, tipo)}, **kwargs)


# ------------------------------------------------------------------ criterio 15: selector apagado


def test_el_barrido_cubre_las_rutas_que_importan():
    pares = rutas_de_la_app()
    assert {("GET", "/entrar"), ("POST", "/entrar"), ("POST", "/salir"), ("GET", "/chat"), ("POST", "/chat/mensaje"),
            ("POST", "/documentos"), ("POST", "/bandeja/1/aprobar"), ("GET", "/bandeja")} <= set(pares)


# Sin sesión solo responden la salud y las pantallas del login: los POST piden el CSRF de la cookie previa
PUBLICAS = {("GET", "/salud"): 200, ("GET", "/entrar"): 200, ("POST", "/entrar"): 403,
            ("GET", "/registro"): 400, ("POST", "/registro"): 403}


def test_selector_apagado_toda_ruta_manda_a_entrar_salvo_salud_y_login():
    cliente = TestClient(crear_app(), follow_redirects=False)
    for metodo, camino in rutas_de_la_app():
        esperado = PUBLICAS.get((metodo, camino), 303)  # 303 a /entrar
        assert cliente.request(metodo, camino).status_code == esperado, (metodo, camino)


def test_cookie_selector_valida_no_vale_con_el_selector_apagado():
    cliente = cookie_con(sesion_de(ADMIN, proveedor="selector"))
    cliente.follow_redirects = False
    for metodo, camino in rutas_de_la_app():
        if (metodo, camino) not in PUBLICAS:
            pedido = {"data": {"csrf": cliente.csrf}} if metodo == "POST" else {}
            assert manda_a_entrar(cliente.request(metodo, camino, **pedido)), (metodo, camino)


def test_sin_secreto_y_sin_selector_no_arranca(monkeypatch):
    monkeypatch.setattr(config, "web_secreto", "")
    with pytest.raises(RuntimeError, match="WEB_SECRETO"):
        crear_app()


@pytest.mark.parametrize("selector", [False, True])
@pytest.mark.parametrize("secreto", ["a", "   ", "a" * 40, "corto-pero-distinto", " " * 20 + "abcdefghijklmnop" + " " * 20])
def test_un_secreto_debil_no_arranca(monkeypatch, secreto, selector):
    """Con WEB_SECRETO=a se forjaba una sesión de administrador o de plataforma."""
    monkeypatch.setattr(config, "web_secreto", secreto)
    monkeypatch.setattr(config, "web_selector_usuarios", selector)
    with pytest.raises(RuntimeError, match="WEB_SECRETO"):
        crear_app()


def test_un_secreto_aleatorio_arranca(monkeypatch):
    monkeypatch.setattr(config, "web_secreto", secrets.token_urlsafe(32))
    assert crear_app() is not None


def test_con_selector_y_sin_secreto_arranca_con_uno_aleatorio(monkeypatch):
    monkeypatch.setattr(config, "web_secreto", "")
    monkeypatch.setattr(config, "web_selector_usuarios", True)
    assert crear_app() is not None


@pytest.mark.parametrize("selector", [False, True])
def test_sin_sesion_redirige_a_entrar(monkeypatch, selector):
    monkeypatch.setattr(config, "web_selector_usuarios", selector)
    cliente = TestClient(crear_app(), follow_redirects=False)
    r = cliente.get("/chat")
    assert r.status_code == 303 and r.headers["location"] == "/entrar"
    htmx = cliente.post("/chat/mensaje", headers={"HX-Request": "true"})
    assert htmx.status_code == 401 and htmx.headers["HX-Redirect"] == "/entrar"


# ------------------------------------------------------------------ criterio 16: cookie y CSRF


def test_cookie_alterada_da_401():
    cliente = cliente_como(ADMIN, PALMERAS)
    token = cliente.cookies[NOMBRE_COOKIE]
    cliente.cookies.clear()
    cuerpo, firma = token.split(".")
    otra_firma = ("0" if firma[0] != "0" else "1") + firma[1:]
    for alterada in (f"{cuerpo}.{otra_firma}", f"{cuerpo[:-2]}AA.{firma}", cuerpo, "basura", "a.b.c"):
        cliente.cookies.set(NOMBRE_COOKIE, alterada)
        assert manda_a_entrar(cliente.get("/chat")), alterada
        cliente.cookies.clear()


def test_cookie_firmada_con_otro_secreto_da_401(monkeypatch):
    cliente = cliente_como(ADMIN, PALMERAS)
    monkeypatch.setattr(config, "web_secreto", "otro-secreto")
    assert manda_a_entrar(cliente.get("/chat"))


def test_cookie_vencida_da_401():
    emitida = int(time.time()) - config.web_sesion_horas * 3600 - 5
    assert manda_a_entrar(cookie_con(sesion_de(ADMIN, emitida=emitida)).get("/chat"))


@pytest.mark.parametrize("camino", [
    "/salir", "/consorcios", "/chat/mensaje", "/chat/feedback", "/documentos", "/bandeja/1/resolver", "/bandeja/1/aprobar",
    "/bandeja/1/editar", "/bandeja/1/rechazar",
])
def test_post_sin_csrf_da_403(camino):
    cliente = cliente_como(ADMIN, PALMERAS)
    assert cliente.post(camino, data={"mensaje": "hola"}).status_code == 403
    assert cliente.post(camino, data={"csrf": "x" * 64}).status_code == 403
    assert cliente.post(camino, data={"csrf": "ñandú"}).status_code == 403


def test_csrf_de_otra_sesion_no_sirve():
    uno, otro = cliente_como(ADMIN, PALMERAS), cliente_como(ADMIN, PALMERAS)
    assert uno.post("/chat/mensaje", data={"mensaje": "hola", "csrf": otro.csrf}).status_code == 403


def test_csrf_tambien_por_cabecera(monkeypatch):
    monkeypatch.setattr(rutas, "atender", lambda *a, **k: {
        "turno_id": None, "conversacion_id": None, "mensaje": "ok", "resultado": "error", "solicitud_id": None})
    cliente = cliente_como(ADMIN, PALMERAS)
    assert cliente.post("/chat/mensaje", data={"mensaje": "hola"}, headers={"X-CSRF": cliente.csrf}).status_code == 200


def entrar_con_selector(monkeypatch) -> tuple[TestClient, str]:
    monkeypatch.setattr(config, "web_selector_usuarios", True)
    cliente = TestClient(crear_app(), follow_redirects=False)
    pagina = cliente.get("/entrar")
    return cliente, re.search(r'name="csrf" value="([^"]+)"', pagina.text).group(1)


def test_entrar_exige_csrf_y_usuario_de_la_lista(monkeypatch):
    cliente, csrf = entrar_con_selector(monkeypatch)
    assert cliente.post("/entrar", data={"sujeto": ADMIN}).status_code == 403
    assert cliente.post("/entrar", data={"sujeto": ADMIN, "csrf": "x"}).status_code == 403
    assert cliente.post("/entrar", data={"sujeto": "real-1", "csrf": csrf}).status_code == 303  # 401 → /entrar
    assert NOMBRE_COOKIE not in cliente.cookies


def test_el_selector_no_ofrece_ni_acepta_usuarios_reales(monkeypatch):
    with Session(postgres.engine()) as s:
        s.add(Usuario(sujeto_externo="persona-real", nombre="Persona Real"))
        s.commit()
    try:
        cliente, csrf = entrar_con_selector(monkeypatch)
        assert "Persona Real" not in cliente.get("/entrar").text
        cliente.post("/entrar", data={"sujeto": "persona-real", "csrf": csrf})
        assert NOMBRE_COOKIE not in cliente.cookies
    finally:
        with postgres.engine().begin() as con:
            con.execute(delete(Usuario).where(Usuario.sujeto_externo == "persona-real"))


def test_atributos_de_cookie_en_desarrollo(monkeypatch):
    cliente, csrf = entrar_con_selector(monkeypatch)
    cabecera = cliente.post("/entrar", data={"sujeto": ADMIN, "csrf": csrf}).headers["set-cookie"].lower()
    assert "httponly" in cabecera and "samesite=lax" in cabecera and "secure" not in cabecera.replace("samesite", "")


def test_cookie_lleva_secure_fuera_del_modo_desarrollo():
    cliente = cliente_como(ADMIN)
    cliente.follow_redirects = False
    cabecera = cliente.post("/consorcios", data={"consorcio_id": PALMERAS, "csrf": cliente.csrf}).headers["set-cookie"].lower()
    assert NOMBRE_COOKIE in cabecera and "httponly" in cabecera and "samesite=lax" in cabecera and "; secure" in cabecera


def test_salir_borra_la_cookie():
    cliente = cliente_como(ADMIN, PALMERAS)
    cliente.follow_redirects = False
    r = cliente.post("/salir", data={"csrf": cliente.csrf})
    borrado = r.headers["set-cookie"].lower()
    assert r.status_code == 303 and NOMBRE_COOKIE in borrado and "max-age=0" in borrado


# ------------------------------------------------------------------ criterio 17: consorcio y membresía


def test_elegir_un_consorcio_ajeno_por_formulario_da_403():
    cliente = cliente_como(PROP)
    assert cliente.post("/consorcios", data={"consorcio_id": ROBLES, "csrf": cliente.csrf}).status_code == 403


def test_cookie_con_consorcio_ajeno_da_403():
    """Forjada de verdad: firmada bien, porque la firma no dice nada sobre la membresía."""
    cliente = cliente_como(PROP, ROBLES)
    for camino in ("/chat", "/documentos", "/mis-solicitudes"):
        assert cliente.get(camino).status_code == 403, camino
    assert cliente.post("/chat/mensaje", data={"mensaje": "hola", "csrf": cliente.csrf}).status_code == 403


def test_cambiar_el_consorcio_sin_firmar_da_401():
    cliente = cliente_como(PROP, PALMERAS)
    cuerpo, firma = cliente.cookies[NOMBRE_COOKIE].split(".")
    datos = json.loads(base64.urlsafe_b64decode(cuerpo))
    forjado = base64.urlsafe_b64encode(json.dumps({**datos, "consorcio_id": ROBLES}).encode()).decode()
    cliente.cookies.clear()
    cliente.cookies.set(NOMBRE_COOKIE, f"{forjado}.{firma}")
    assert manda_a_entrar(cliente.get("/chat"))


def test_perder_la_membresia_corta_en_el_pedido_siguiente():
    cliente = cliente_como("demo-multi", ROBLES)
    assert cliente.get("/bandeja").status_code == 200
    with Session(postgres.engine()) as s:
        membresia = s.scalars(select(Membresia).join(Usuario).where(
            Usuario.sujeto_externo == "demo-multi", Membresia.consorcio_id == ROBLES)).one()
        usuario_id, rol = membresia.usuario_id, membresia.rol
        s.delete(membresia)
        s.commit()
    try:
        assert cliente.get("/bandeja").status_code == 403
        assert cliente.get("/chat").status_code == 403
    finally:
        with Session(postgres.engine()) as s:
            s.add(Membresia(usuario_id=usuario_id, consorcio_id=ROBLES, rol=rol))
            s.commit()


def test_el_rol_se_lee_de_la_base_y_no_de_la_cookie():
    cliente = cliente_como("demo-multi", ROBLES)
    assert cliente.get("/bandeja").status_code == 200
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE membresias SET rol='consejo' WHERE consorcio_id=:c AND usuario_id="
                         "(SELECT id FROM usuarios WHERE sujeto_externo='demo-multi')"), {"c": ROBLES})
    try:
        assert cliente.get("/bandeja").status_code == 403
    finally:
        with postgres.engine().begin() as con:
            con.execute(text("UPDATE membresias SET rol='administracion' WHERE consorcio_id=:c AND usuario_id="
                             "(SELECT id FROM usuarios WHERE sujeto_externo='demo-multi')"), {"c": ROBLES})


def test_usuario_sin_membresia():
    cliente = cliente_como("alguien-sin-cuenta", PALMERAS)
    assert cliente.get("/chat").status_code == 403
    r = cliente.get("/consorcios")
    assert r.status_code == 200 and "ningún consorcio" in r.text


def test_sesion_sin_consorcio_elegido_no_entra_al_chat():
    assert cliente_como(MULTI).get("/chat").status_code == 403


def test_plataforma_no_es_miembro_de_ningun_consorcio():
    cliente = cliente_como(PLATAFORMA, PALMERAS)
    for camino in ("/chat", "/documentos", "/bandeja", "/mis-solicitudes"):
        assert cliente.get(camino).status_code == 403, camino


# ------------------------------------------------------------------ criterio 18: permisos y cargas


def test_propietario_no_entra_a_la_bandeja_ni_carga(carga):
    cliente = cliente_como(PROP, PALMERAS)
    assert cliente.get("/bandeja").status_code == 403
    assert subir(cliente).status_code == 403
    assert cliente.post("/bandeja/1/aprobar", data={"csrf": cliente.csrf}).status_code == 403
    assert carga == []


def test_propietario_rechazado_sin_leer_el_cuerpo(carga, sin_leer_cuerpo):
    """Ni el CSRF se mira: el rol corta primero, y mirar el CSRF obliga a leer el formulario."""
    cliente = cliente_como(PROP, PALMERAS)
    assert cliente.post("/documentos", content=b"x" * 100, headers={"Content-Length": "100"}).status_code == 403


def test_archivo_mayor_al_limite_declarado_da_413_sin_ingestar(carga):
    cliente = cliente_como(ADMIN, PALMERAS)
    r = subir(cliente, b"%PDF-" + b"x" * (config.web_max_mb_pdf * 1024 * 1024 + 70 * 1024))
    assert r.status_code == 413 and carga == []


def test_el_limite_se_aplica_antes_de_leer_el_cuerpo(carga, sin_leer_cuerpo):
    """Un `Content-Length` enorme se rechaza sin tocar el formulario, aunque el cuerpo real sea chico."""
    cliente = cliente_como(ADMIN, PALMERAS)
    r = cliente.post("/documentos", content=b"x", headers={"Content-Length": str(10**12)})
    assert r.status_code == 413 and carga == []


def test_sin_longitud_da_411(carga, sin_leer_cuerpo):
    cliente = cliente_como(ADMIN, PALMERAS)

    def cuerpo():
        yield b"--x\r\n"

    r = cliente.post("/documentos", content=cuerpo(), headers={"Content-Type": "multipart/form-data; boundary=x"})
    assert r.status_code == 411 and carga == []


def test_archivo_dentro_del_margen_pero_sobre_el_limite_se_cuenta_al_copiar(carga, tmp_path):
    """El margen de multipart deja pasar un poco por `Content-Length`: lo frena el conteo y no queda archivo."""
    cliente = cliente_como(ADMIN, PALMERAS)
    r = subir(cliente, b"%PDF-" + b"x" * (config.web_max_mb_pdf * 1024 * 1024))
    assert r.status_code == 413 and carga == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("contenido", [b"", b"no soy un pdf", b"%PDF", b"MZ\x90\x00", b"<html>%PDF-1.4"])
def test_no_pdf_da_415(carga, tmp_path, contenido):
    r = subir(cliente_como(ADMIN, PALMERAS), contenido, "falso.pdf")
    assert r.status_code == 415 and carga == [] and list(tmp_path.iterdir()) == []


def test_la_extension_no_alcanza_y_tampoco_hace_falta(carga):
    assert subir(cliente_como(ADMIN, PALMERAS), PDF_CHICO, "sin-extension", "text/plain").status_code == 200


def test_sin_archivo_da_422(carga):
    cliente = cliente_como(ADMIN, PALMERAS)
    assert cliente.post("/documentos", data={"csrf": cliente.csrf}).status_code == 422


def test_nombre_con_ruta_queda_dentro_de_la_entrada(carga, tmp_path):
    assert subir(cliente_como(ADMIN, PALMERAS), nombre="../../../etc/acta.pdf").status_code == 200
    ruta = Path(carga[0]["ruta"]).resolve()
    assert ruta.is_relative_to(tmp_path.resolve()) and ruta.name == "acta.pdf"


def test_excepcion_en_la_ingesta_no_muestra_detalle(monkeypatch):
    def romper(*_a, **_k):
        raise RuntimeError("clave-secreta en /ruta/interna")

    monkeypatch.setattr(rutas, "ingestar_pdf", romper)
    r = subir(cliente_como(ADMIN, PALMERAS))
    assert r.status_code == 500 and "clave-secreta" not in r.text and "interna" not in r.text
    assert "No se pudo procesar" in r.text


# ------------------------------------------------------------------ criterio 19: escape y recursos


def test_las_plantillas_no_usan_safe():
    for plantilla in PLANTILLAS.glob("*.html"):
        contenido = plantilla.read_text(encoding="utf-8")
        assert "|safe" not in contenido.replace(" ", "") and "autoescape false" not in contenido, plantilla.name


def test_script_en_el_mensaje_sale_escapado(monkeypatch):
    malicioso = "<script>alert(1)</script>"
    monkeypatch.setattr(rutas, "atender", lambda *a, **k: {
        "turno_id": 1, "conversacion_id": "a" * 32, "mensaje": f"eco {malicioso}", "resultado": "respondida",
        "solicitud_id": None})
    cliente = cliente_como(PROP, PALMERAS)
    r = cliente.post("/chat/mensaje", data={"mensaje": malicioso, "csrf": cliente.csrf})
    assert "<script>" not in r.text and r.text.count("&lt;script&gt;") == 2


def test_script_en_el_titulo_de_documento_sale_escapado():
    malicioso = "<script>alert(1)</script>"
    with postgres.engine().begin() as con:
        con.execute(text("UPDATE documentos SET titulo = CAST(:t AS text), nombre_archivo = CAST(:t AS text) WHERE id = "
                         "(SELECT id FROM documentos WHERE consorcio_id = :c LIMIT 1)"), {"t": malicioso, "c": PALMERAS})
    r = cliente_como(PROP, PALMERAS).get("/documentos")
    assert "<script>alert" not in r.text and "&lt;script&gt;alert(1)" in r.text


def test_comillas_no_rompen_un_atributo():
    """El título del reclamo va en `value`: el escape también tiene que cubrir las comillas."""
    with Session(postgres.engine()) as s:
        autor = s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == PROP))
        s.add(postgres.Solicitud(tipo="reclamo", motivo="reclamo", consorcio_id=PALMERAS, autor_id=autor,
                                 mensaje="m", titulo='x" onfocus="alert(1)', detalle="</textarea><script>1</script>"))
        s.commit()
    r = cliente_como(ADMIN, PALMERAS).get("/bandeja")
    assert 'onfocus="alert' not in r.text and "<script>1" not in r.text


@pytest.mark.parametrize("camino,sujeto,consorcio", [
    ("/chat", ADMIN, PALMERAS), ("/documentos", ADMIN, PALMERAS), ("/bandeja", ADMIN, PALMERAS),
    ("/mis-solicitudes", PROP, PALMERAS), ("/consorcios", MULTI, None),
])
def test_ningun_recurso_externo_ni_codigo_en_linea(camino, sujeto, consorcio):
    html = cliente_como(sujeto, consorcio).get(camino).text
    assert not re.search(r'(?:src|href|action)="(?:https?:)?//', html)
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), "script en línea"
    assert not re.search(r"\sstyle=|<style|\son\w+=", html), "estilo o manejador en línea"


def test_entrar_tampoco_tiene_recursos_externos(monkeypatch):
    cliente, _ = entrar_con_selector(monkeypatch)
    assert not re.search(r'(?:src|href)="(?:https?:)?//', cliente.get("/entrar").text)


def test_htmx_config_apaga_eval_y_estilos():
    html = cliente_como(PROP, PALMERAS).get("/chat").text
    config_htmx = json.loads(re.search(r"name=\"htmx-config\" content='([^']+)'", html).group(1))
    assert config_htmx["allowEval"] is False and config_htmx["includeIndicatorStyles"] is False


@pytest.mark.parametrize("camino", ["/salud", "/estaticos/web.css", "/estaticos/htmx.min.js", "/no-existe"])
def test_cabeceras_en_toda_respuesta(camino):
    r = TestClient(crear_app()).get(camino)
    assert r.headers["content-security-policy"] == "default-src 'self'"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_cabeceras_tambien_en_error_de_acceso():
    r = TestClient(crear_app(), follow_redirects=False).get("/chat")
    assert r.status_code == 303 and r.headers["content-security-policy"] == "default-src 'self'"


def test_htmx_vendorizado_es_la_version_pedida():
    r = TestClient(crear_app()).get("/estaticos/htmx.min.js")
    assert r.status_code == 200 and 'version:"2.0.4"' in r.text


def test_404_de_ruta_oculta_igual_al_de_una_inexistente():
    assert cliente_como(PROP, PALMERAS).get("/no-existe").text == TestClient(crear_app()).get("/no-existe").text


# ------------------------------------------------------------------ criterio 21: actividad


def actividad(sujeto):
    with Session(postgres.engine()) as s:
        return s.scalar(select(Usuario.ultima_actividad).where(Usuario.sujeto_externo == sujeto))


def poner_actividad(sujeto, valor):
    with postgres.engine().begin() as con:
        con.execute(text(f"UPDATE usuarios SET ultima_actividad = {valor} WHERE sujeto_externo = :s"), {"s": sujeto})


def test_actividad_no_se_escribe_mas_de_una_vez_por_minuto():
    poner_actividad(PROP, "NULL")
    cliente = cliente_como(PROP, PALMERAS)
    cliente.get("/chat")
    primera = actividad(PROP)
    assert primera is not None
    for _ in range(3):
        cliente.get("/documentos")
    assert actividad(PROP) == primera


def test_actividad_se_vuelve_a_escribir_pasado_el_minuto():
    poner_actividad(PROP, "now() - interval '2 minutes'")
    vieja = actividad(PROP)
    cliente_como(PROP, PALMERAS).get("/chat")
    assert actividad(PROP) > vieja


def test_actividad_de_plataforma_sin_membresias():
    poner_actividad(PLATAFORMA, "NULL")
    cliente_como(PLATAFORMA).get("/consorcios", follow_redirects=False)
    assert actividad(PLATAFORMA) is not None


def test_sin_sesion_no_hay_actividad():
    poner_actividad(PROP, "NULL")
    TestClient(crear_app()).get("/chat")
    assert actividad(PROP) is None
