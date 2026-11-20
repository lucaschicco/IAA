"""Ninguna sección rota, por ningún canal (objetivo A de docs/producto/plan-tanda-perfiles.md).

Es el crawler de la auditoría de QA del 20/11 pasado a pytest. Con los datos de `sembrar-demo` y actividad
generada con el guion fake (reclamos, derivaciones, consultas, una solicitud resuelta y una revisión):

- *Recorrido*: por cada usuario demo y cada membresía (y sin consorcio elegido) arranca en "/" y sigue todos
  los links internos, los `hx-get` y los formularios GET. Ninguna respuesta da 500, ningún link propio da 404,
  ningún link visible lleva a 403 y toda página con sesión tiene el menú lateral. Cada formulario POST apunta a
  una ruta POST que existe y lleva CSRF; los botones que navegan por POST (`/consorcios` con `destino`) se
  envían de verdad y su destino carga.
- *Cobertura*: todo GET sin parámetros de la app lo visita algún recorrido, salvo las pantallas sin sesión.
- *Rutas directas*: todo GET registrado, por usuario, sin consorcio, con cada consorcio (también el ajeno,
  firmado a mano) y sin sesión: nunca 500 y lo que se espera según el rol.
- *Parámetros raros*: nunca 500, nunca el JSON crudo de FastAPI y nada reflejado sin escapar.
- *CLI, bot y API de login*: `--help` de cada subcomando, los imports perezosos resuelven y la API JSON no da
  500 con un token válido.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import importlib.util
import re
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit

import pytest
from apoyo_web import cliente_como, vaciar_y_sembrar
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import text

from consorcio.agente.semilla import CONTRASENA_DEMO, PALMERAS, ROBLES
from consorcio.almacen import postgres
from consorcio.config import config
from consorcio.web import crm_rutas, rutas
from consorcio.web.app import crear_app
from consorcio.web.sesion import NOMBRE_COOKIE

MEMBRESIAS = {  # lo que siembra agente/semilla.py
    "demo-prop-palmeras": {PALMERAS: "propietario"},
    "demo-prop-robles": {ROBLES: "propietario"},
    "demo-multi": {PALMERAS: "propietario", ROBLES: "administracion"},
    "demo-admin-palmeras": {PALMERAS: "administracion"},
    "demo-admin-dos": {PALMERAS: "administracion", ROBLES: "administracion"},
    "demo-plataforma": {},
}
CONTEXTOS = [(u, c) for u, ms in MEMBRESIAS.items() for c in (None, *ms)]
SIN_SESION = {"/entrar", "/registro", "/salud"}  # las únicas que no piden sesión
MAX_PAGINAS = 200
REDIRECCION = (301, 302, 303, 307, 308)


# ------------------------------------------------------------------ HTML


@dataclass
class Enlace:
    url: str
    tipo: str  # a | hx-get | form-get | form-post | hx-post
    campos: dict = field(default_factory=dict)


class Lector(HTMLParser):
    """Links, formularios (con sus campos) y si la página tiene el menú lateral."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.enlaces: list[Enlace] = []
        self.tiene_menu = False
        self._form: Enlace | None = None
        self._select: str | None = None
        self._botones: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "nav" and "menu-principal" in (a.get("class") or ""):
            self.tiene_menu = True
        if tag == "a" and a.get("href"):
            self.enlaces.append(Enlace(a["href"], "a"))
        if a.get("hx-get") and tag != "form":
            self.enlaces.append(Enlace(a["hx-get"], "hx-get"))
        if tag == "form":
            if a.get("hx-post"):
                self._form = Enlace(a["hx-post"], "hx-post")
            elif a.get("hx-get"):
                self._form = Enlace(a["hx-get"], "hx-get")
            else:
                metodo = (a.get("method") or "get").lower()
                self._form = Enlace(a.get("action") or "", "form-post" if metodo == "post" else "form-get")
        elif self._form is not None:
            if tag == "select" and a.get("name"):
                self._select = a["name"]
                self._form.campos.setdefault(self._select, "")
            elif tag == "option" and self._select and (not self._form.campos[self._select] or "selected" in a):
                self._form.campos[self._select] = a.get("value", "")
            elif tag in ("input", "textarea") and a.get("name"):
                if (a.get("type") or "").lower() not in ("checkbox", "radio") or "checked" in a:
                    self._form.campos[a["name"]] = a.get("value") or ""
            elif tag == "button" and a.get("name"):
                self._botones.append((a["name"], a.get("value") or ""))

    def handle_endtag(self, tag):
        if tag == "select":
            self._select = None
        if tag == "form" and self._form is not None:
            # Un formulario con varios botones con name/value (elegir consorcio): un envío por botón
            for nombre, valor in self._botones or [(None, None)]:
                campos = {**self._form.campos, **({nombre: valor} if nombre else {})}
                self.enlaces.append(Enlace(self._form.url, self._form.tipo, campos))
            self._form, self._botones = None, []


def leer(html: str) -> Lector:
    lector = Lector()
    lector.feed(html)
    return lector


def interna(url: str, base: str) -> str | None:
    """Ruta y query si es de la app; None para anclas, otros esquemas, otros hosts y estáticos."""
    if not url or url.startswith(("#", "mailto:", "tel:", "javascript:")):
        return None
    partes = urlsplit(urljoin("http://testserver" + base, url))
    if partes.netloc != "testserver" or partes.path.startswith("/estaticos"):
        return None
    return partes.path + (f"?{partes.query}" if partes.query else "")


# ------------------------------------------------------------------ rutas de la app


def rutas_web() -> list[APIRoute]:
    """De los routers y no de `app.routes`: según la versión de FastAPI ahí no están las rutas sueltas."""
    return [r for r in [*rutas.router.routes, *crm_rutas.router.routes] if isinstance(r, APIRoute)]


def existe_post(camino: str) -> bool:
    ruta = camino.split("?")[0]
    return any("POST" in r.methods and re.fullmatch(re.sub(r"\{[^}]+\}", "[^/]+", r.path), ruta) for r in rutas_web())


# ------------------------------------------------------------------ clientes


def pedir(cliente: TestClient, metodo: str, url: str, **kw):
    """La cookie es `secure` sin el selector y el TestClient va por http: se copia a mano, como un navegador."""
    r = cliente.request(metodo, url, follow_redirects=False, **kw)
    if NOMBRE_COOKIE in r.cookies:
        cliente.cookies.set(NOMBRE_COOKIE, r.cookies[NOMBRE_COOKIE])
    return r


def seguir(cliente: TestClient, url: str) -> tuple[object, str, list[str]]:
    """GET siguiendo las redirecciones a mano: devuelve la respuesta final, su URL y los problemas."""
    cadena = [url]
    for _ in range(10):
        r = pedir(cliente, "GET", cadena[-1])
        if r.status_code not in REDIRECCION:
            return r, cadena[-1], []
        destino = interna(r.headers.get("location", ""), cadena[-1])
        if destino is None:
            return r, cadena[-1], [f"{url}: redirección fuera de la app ({r.headers.get('location')})"]
        if destino in cadena:
            return r, cadena[-1], [f"{url}: redirección en loop {' -> '.join([*cadena, destino])}"]
        cadena.append(destino)
    return r, cadena[-1], [f"{url}: más de 10 redirecciones"]


def elegir(sujeto: str, consorcio: str | None) -> TestClient:
    """Como la web: con sesión y, si hay consorcio, elegido con POST /consorcios y CSRF."""
    cliente = cliente_como(sujeto)
    if consorcio:
        r = pedir(cliente, "POST", "/consorcios", data={"csrf": cliente.csrf, "consorcio_id": consorcio})
        assert r.status_code == 303, (sujeto, consorcio, r.status_code)
    return cliente


# ------------------------------------------------------------------ datos


MENSAJES = ["Se rompió el portón, quiero hacer un reclamo", "Quiero hablar con una persona de la administracion",
            "¿Puedo tener mascotas?"]


@pytest.fixture(scope="module")
def actividad():
    """Siembra y genera actividad para que existan `?solicitud=` y `?turno=` que seguir.
    Devuelve los fragmentos htmx del chat, que también tienen links."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "fake_guionado", True)
        mp.setattr(config, "web_selector_usuarios", False)
        vaciar_y_sembrar()
        fragmentos = []
        for sujeto, consorcio in [("demo-prop-palmeras", PALMERAS), ("demo-prop-robles", ROBLES)]:
            cliente = cliente_como(sujeto, consorcio)
            conversacion = ""
            for mensaje in MENSAJES:
                r = cliente.post("/chat/mensaje", data={"csrf": cliente.csrf, "mensaje": mensaje,
                                                        "conversacion_id": conversacion})
                assert r.status_code == 200, (sujeto, mensaje, r.status_code)
                fragmentos.append((sujeto, consorcio, r.text))
                if not conversacion:
                    with postgres.engine().connect() as con:
                        conversacion = str(con.execute(text(
                            "SELECT id FROM conversaciones ORDER BY creado_en DESC LIMIT 1")).scalar() or "")
        with postgres.engine().connect() as con:
            solicitud = con.execute(text("SELECT id, tipo FROM solicitudes WHERE consorcio_id = :c AND estado = "
                                         "'pendiente' ORDER BY id LIMIT 1"), {"c": PALMERAS}).first()
            turno = con.execute(text("SELECT id FROM turnos WHERE consorcio_id = :c AND resultado = 'respondida' "
                                     "ORDER BY id LIMIT 1"), {"c": PALMERAS}).first()
        admin = cliente_como("demo-admin-palmeras", PALMERAS)
        if solicitud:
            accion = "rechazar" if solicitud.tipo == "reclamo" else "resolver"
            admin.post(f"/bandeja/{solicitud.id}/{accion}", data={"csrf": admin.csrf, "respuesta": "Resuelto"})
        if turno:
            admin.post(f"/revisar/{turno.id}", data={"csrf": admin.csrf, "veredicto": "correcta"})
        yield fragmentos


@pytest.fixture(autouse=True)
def selector_apagado(monkeypatch):
    monkeypatch.setattr(config, "web_selector_usuarios", False)


# ------------------------------------------------------------------ recorrido


def _probar_formulario(sujeto: str, enlace: Enlace, destino: str, origen: str) -> list[str]:
    """Un POST tiene que ir a una ruta que existe y llevar CSRF; los que navegan (`/consorcios`) se envían."""
    problemas = []
    if not existe_post(destino):
        problemas.append(f"{origen}: formulario {enlace.tipo} a {destino}, que no es una ruta POST")
    if "csrf" not in enlace.campos:
        problemas.append(f"{origen}: formulario {enlace.tipo} a {destino} sin csrf (daría 403 siempre)")
    if destino == "/consorcios" and enlace.tipo == "form-post":
        aparte = cliente_como(sujeto)  # en otro cliente: no cambia el consorcio del recorrido
        r = pedir(aparte, "POST", destino, data={**enlace.campos, "csrf": aparte.csrf})
        if r.status_code != 303:
            problemas.append(f"{origen}: botón a /consorcios {enlace.campos} da {r.status_code}")
        else:
            final, url, malos = seguir(aparte, r.headers["location"])
            problemas += malos
            if final.status_code != 200:
                problemas.append(f"{origen}: el botón {enlace.campos} lleva a {url}, que da {final.status_code}")
    return problemas


def recorrer(sujeto: str, consorcio: str | None) -> tuple[list[str], set[str]]:
    cliente = elegir(sujeto, consorcio)
    problemas: list[str] = []
    vistas: set[str] = set()
    formularios: set[tuple] = set()
    cola: deque[tuple[str, str]] = deque([("/", "inicio")])
    while cola and len(vistas) < MAX_PAGINAS:
        url, origen = cola.popleft()
        if url in vistas:
            continue
        vistas.add(url)
        r, final, malos = seguir(cliente, url)
        vistas.add(final)
        problemas += [f"{origen}: {m}" for m in malos]
        if r.status_code != 200:
            problemas.append(f"{origen}: link a {url} -> {final} da {r.status_code}")
            continue
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        lector = leer(r.text)
        if not lector.tiene_menu:
            problemas.append(f"{final}: página con sesión sin menú lateral")
        for enlace in lector.enlaces:
            destino = interna(enlace.url or final, final)
            if destino is None:
                continue
            if enlace.tipo in ("a", "hx-get", "form-get"):
                if enlace.tipo == "form-get" and enlace.campos:
                    destino = destino.split("?")[0] + "?" + urlencode(enlace.campos)
                if destino not in vistas:
                    cola.append((destino, final))
            elif (clave := (destino, tuple(sorted(enlace.campos.items())))) not in formularios:
                formularios.add(clave)
                problemas += _probar_formulario(sujeto, enlace, destino, final)
    return problemas, vistas


@pytest.fixture(scope="module")
def recorridos(actividad) -> dict:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(config, "web_selector_usuarios", False)
        return {(u, c): recorrer(u, c) for u, c in CONTEXTOS}


@pytest.mark.parametrize("sujeto, consorcio", CONTEXTOS)
def test_recorrido_sin_secciones_rotas(recorridos, sujeto, consorcio):
    problemas, vistas = recorridos[(sujeto, consorcio)]
    assert len(vistas) > 1
    assert problemas == []


def test_los_links_de_los_fragmentos_del_chat_funcionan(actividad):
    """Los turnos llegan por htmx y no los ve el recorrido: sus links y su formulario de voto."""
    problemas = []
    for sujeto, consorcio, html in actividad:
        cliente = cliente_como(sujeto, consorcio)
        for enlace in leer(html).enlaces:
            destino = interna(enlace.url, "/chat")
            if destino is None:
                continue
            if enlace.tipo == "a":
                r, final, malos = seguir(cliente, destino)
                problemas += malos + ([f"{destino} -> {final}: {r.status_code}"] if r.status_code != 200 else [])
            elif enlace.tipo in ("hx-post", "form-post"):
                if not existe_post(destino) or "csrf" not in enlace.campos:
                    problemas.append(f"formulario a {destino} sin ruta o sin csrf")
    assert problemas == []


def test_todo_get_de_la_app_tiene_un_link(recorridos):
    """Una pantalla nueva sin link desde ningún lado rompe este test (o se agrega a la lista de excepciones)."""
    visitadas = {u.split("?")[0] for _, vistas in recorridos.values() for u in vistas}
    gets = {r.path for r in rutas_web() if "GET" in r.methods and "{" not in r.path}
    assert gets - visitadas - SIN_SESION == set()


# ------------------------------------------------------------------ rutas directas


def esperado(sujeto: str | None, consorcio: str | None, ruta: str) -> set[int]:
    """Lo que tiene que dar un GET escrito a mano, según quién y en qué consorcio."""
    if ruta == "/salud":
        return {200}
    if ruta in ("/entrar", "/registro"):
        return {200, 400}  # /registro sin token: la pantalla de invitación inválida
    if sujeto is None:
        return {303}
    membresias = MEMBRESIAS[sujeto]
    rol = membresias.get(consorcio) if consorcio else None
    administra = "administracion" in membresias.values()
    if ruta in ("/", "/consorcios"):
        return {200, 303}
    if ruta == "/crm":
        return {200} if sujeto == "demo-plataforma" else {404}
    if ruta in ("/panel", "/consorcios/nuevo"):
        return {200} if administra else {403}
    if ruta in ("/chat", "/mis-solicitudes"):  # del vecino: la administración va a su bandeja (303)
        return {303} if consorcio is None or rol == "administracion" else {200} if rol else {403}
    if ruta == "/documentos":
        return {303} if consorcio is None else {200} if rol else {403}
    if ruta in ("/bandeja", "/revisar"):
        return {303} if consorcio is None else {200} if rol == "administracion" else {403}
    return {200, 303, 403, 404}


@pytest.mark.parametrize("sujeto", [None, *MEMBRESIAS])
def test_rutas_get_directas(actividad, sujeto):
    """Con cada consorcio, también el ajeno: la cookie firmada a mano no lo deja pasar."""
    problemas = []
    gets = [r.path for r in rutas_web() if "GET" in r.methods]
    propios = list(MEMBRESIAS[sujeto]) if sujeto else []
    ajeno = [c for c in (PALMERAS, ROBLES) if c not in propios][:1]
    for consorcio in ([None] if sujeto is None else [None, *propios, *ajeno]):
        cliente = TestClient(crear_app()) if sujeto is None else cliente_como(sujeto, consorcio)
        for ruta in gets:
            r = cliente.get(ruta, follow_redirects=False)
            esperados = esperado(sujeto, consorcio, ruta)
            if r.status_code not in esperados:
                problemas.append(f"{sujeto}@{consorcio} GET {ruta}: {r.status_code}, se esperaba {sorted(esperados)}")
            if r.status_code == 303 and sujeto is None and r.headers["location"] != "/entrar":
                problemas.append(f"sin sesión GET {ruta} no manda a /entrar")
            a_la_bandeja = consorcio is not None and ruta in ("/chat", "/mis-solicitudes")
            if r.status_code == 303 and sujeto and ruta not in ("/", "/consorcios") \
                    and r.headers["location"] != ("/bandeja" if a_la_bandeja else "/consorcios"):
                problemas.append(f"{sujeto} GET {ruta}: 303 a {r.headers['location']}")
            if sujeto and r.status_code in (403, 404) and not leer(r.text).tiene_menu:
                problemas.append(f"{sujeto}@{consorcio} GET {ruta}: página de error sin menú")
    assert problemas == []


# ------------------------------------------------------------------ parámetros raros


RAROS = {
    "/mis-solicitudes": ["solicitud=abc", "solicitud=999999", "solicitud=", "solicitud=%00", "solicitud=1&solicitud=2"],
    "/bandeja": ["solicitud=abc", "solicitud=999999", "ver=x", "ver=%3Cscript%3E", "ver=resueltas&solicitud=abc"],
    "/revisar": ["turno=abc", "turno=999999", "turno=-1", "ver=x", "ver=revisadas&turno=abc"],
    "/documentos": ["tipo=%3Cscript%3Ealert(1)%3C/script%3E", "tipo=x", "tipo=", "tipo=%00", "tipo=" + "a" * 5000],
    "/chat": ["conversacion=abc", "conversacion=999999", "conversacion=00000000-0000-0000-0000-000000000000",
              "conversacion=%00", "conversacion="],
    "/crm": ["vista=x", "vista=", "dias=x", "dias=5", "consorcio=x", "vista=calidad&dias=7", "vista=uso&dias=90",
             "administracion=x", "administracion=999999", "administracion=sin", "administracion=%00",
             "administracion=" + "9" * 40, "administracion=1&consorcio=x", "administracion=sin&consorcio=demo-palmeras"],
    "/registro": ["token=abc", "token=", "token=%00", "token=" + "a" * 5000],
    "/consorcios": ["x=1"],
}


@pytest.mark.parametrize("sujeto, consorcio", [
    ("demo-admin-dos", PALMERAS), ("demo-prop-palmeras", PALMERAS), ("demo-plataforma", None), (None, None),
])
def test_parametros_raros(actividad, sujeto, consorcio):
    cliente = TestClient(crear_app()) if sujeto is None else cliente_como(sujeto, consorcio)
    problemas = []
    for ruta, consultas in RAROS.items():
        for consulta in consultas:
            r = cliente.get(f"{ruta}?{consulta}", follow_redirects=False)
            if r.status_code >= 500:
                problemas.append(f"{ruta}?{consulta[:40]}: {r.status_code}")
            if "application/json" in r.headers.get("content-type", ""):
                problemas.append(f"{ruta}?{consulta[:40]}: JSON crudo en la web")
            if "script" in consulta and "<script>" in r.text:
                problemas.append(f"{ruta}?{consulta[:40]}: parámetro reflejado sin escapar")
    assert problemas == []


# ------------------------------------------------------------------ CLI y bot


def _subcomandos() -> dict[str, argparse.ArgumentParser]:
    """El parser de `cli.main` sin correr nada: se lo atrapa al momento de leer los argumentos."""
    from consorcio import cli

    class Atrapado(Exception):
        pass

    atrapado = {}

    def parse_args(self, *_a, **_k):
        atrapado["parser"] = self
        raise Atrapado

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(argparse.ArgumentParser, "parse_args", parse_args)
        with pytest.raises(Atrapado):
            cli.main([])
    accion = next(a for a in atrapado["parser"]._actions if isinstance(a, argparse._SubParsersAction))
    return dict(accion.choices)


def test_cli_help_de_cada_subcomando(capsys):
    from consorcio import cli

    subcomandos = _subcomandos()
    assert {"ingestar", "preguntar", "expensas", "documentos", "consultas", "bot"} <= set(subcomandos)
    for nombre in ["--help", *[f"{s} --help" for s in subcomandos]]:
        with pytest.raises(SystemExit) as salida:
            cli.main(nombre.split())
        assert salida.value.code == 0, nombre
    assert "usage" in capsys.readouterr().out


@pytest.mark.parametrize("modulo", ["consorcio.cli", "consorcio.canales.telegram"])
def test_los_imports_perezosos_resuelven(modulo):
    """Un `from .x import y` dentro de una función solo falla al correr ese comando o ese handler."""
    paquete = importlib.import_module(modulo)
    arbol = ast.parse(Path(paquete.__file__).read_text(encoding="utf-8"))
    faltan = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.ImportFrom):
            base = importlib.util.resolve_name("." * nodo.level + (nodo.module or ""), paquete.__package__)
            origen = importlib.import_module(base)
            for nombre in nodo.names:
                if not hasattr(origen, nombre.name):
                    try:
                        importlib.import_module(f"{base}.{nombre.name}")
                    except ImportError:
                        faltan.append(f"{base}.{nombre.name}")
    assert faltan == []


# ------------------------------------------------------------------ API de login


def _token_api(cliente: TestClient, sujeto: str) -> dict:
    r = cliente.post("/auth/login", json={"email": f"{sujeto}@demo.test", "contrasena": CONTRASENA_DEMO})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.mark.parametrize("sujeto", ["demo-admin-palmeras", "demo-prop-palmeras"])
def test_api_de_login_sin_500(actividad, sujeto):
    """Cada GET del OpenAPI con un token válido, en un consorcio propio, uno ajeno y uno inexistente."""
    from consorcio.auth.app import app

    cliente = TestClient(app)
    cabecera = _token_api(cliente, sujeto)
    problemas = []
    for camino, operaciones in app.openapi()["paths"].items():
        if "get" not in operaciones:
            continue
        for consorcio in (PALMERAS, ROBLES, "no-existe"):
            r = cliente.get(camino.replace("{consorcio_id}", consorcio), headers=cabecera)
            if r.status_code >= 500 or r.status_code not in (200, 403, 404):
                problemas.append(f"{sujeto} GET {camino} ({consorcio}): {r.status_code}")
    assert problemas == []
