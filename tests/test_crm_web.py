"""La pantalla del CRM: acceso, filtros y lo que nunca puede aparecer en el HTML. Datos de `sembrar-demo`."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from apoyo_web import cliente_como, manda_a_entrar, vaciar_y_sembrar
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from consorcio import crm
from consorcio.agente.semilla import ADMINISTRACION as ADMINISTRACION_DEMO
from consorcio.almacen import postgres
from consorcio.almacen.postgres import (
    Administracion,
    Consorcio,
    Conversacion,
    GastoModelo,
    Mensaje,
    Solicitud,
    Turno,
    Usuario,
)
from consorcio.config import config
from consorcio.web.app import crear_app

PLATAFORMA = "demo-plataforma"
PALMERAS, ROBLES = "demo-palmeras", "demo-robles"
TEXTO_PRIVADO = "TEXTO-PRIVADO-DEL-USUARIO"
PLANTILLA = Path(crm.__file__).parent / "web" / "plantillas" / "crm.html"


@pytest.fixture(scope="module")
def sembrado():
    vaciar_y_sembrar()


@pytest.fixture(autouse=True)
def base(sembrado, monkeypatch, tmp_path):
    with postgres.engine().begin() as con:
        con.exec_driver_sql("TRUNCATE turnos, solicitudes, gastos_modelo, mensajes CASCADE")
    monkeypatch.setattr(crm, "DIR_EVALUACIONES", tmp_path)
    monkeypatch.setattr(config, "web_selector_usuarios", False)
    monkeypatch.setattr(config, "tope_usd_mensual", 20.0)
    return tmp_path


def _id_de(s: Session, sujeto: str) -> int:
    return s.scalar(select(Usuario.id).where(Usuario.sujeto_externo == sujeto))


def _turnos(consorcio: str, cantidad: int, origen: str = "usuario") -> None:
    with Session(postgres.engine()) as s:
        usuario = _id_de(s, "demo-multi")
        s.add_all(Turno(usuario_id=usuario, consorcio_id=consorcio, rol="propietario", canal="web", origen=origen,
                        funcionalidad="expensas", intencion="expensas", resultado="respondida", segundos=0.1,
                        modelo="fake", skill_version="0.1.0") for _ in range(cantidad))
        s.commit()


def _valor_de(html: str, rotulo: str) -> int:
    return int(re.search(rf"<strong>(\d+)</strong><span>{rotulo}</span>", html).group(1))


def test_plataforma_entra(sembrado):
    respuesta = cliente_como(PLATAFORMA).get("/crm")
    assert respuesta.status_code == 200
    assert "text/html" in respuesta.headers["content-type"]


@pytest.mark.parametrize("sujeto, consorcio", [
    ("demo-admin-palmeras", PALMERAS), ("demo-prop-palmeras", PALMERAS), ("demo-prop-robles", ROBLES),
    ("demo-multi", ROBLES), ("demo-multi", None),
])
def test_quien_no_es_de_plataforma_recibe_404(sujeto, consorcio):
    assert cliente_como(sujeto, consorcio).get("/crm").status_code == 404


def test_sujeto_desconocido_va_a_entrar():
    assert manda_a_entrar(cliente_como("sujeto-desconocido").get("/crm"))


def test_sin_sesion_va_a_entrar():
    assert manda_a_entrar(TestClient(crear_app()).get("/crm"))


def test_los_parametros_no_revelan_nada_a_quien_no_es_de_plataforma():
    cliente = cliente_como("demo-admin-palmeras", PALMERAS)
    assert cliente.get("/crm", params={"dias": "5"}).status_code == 404
    assert cliente.get("/crm", params={"consorcio": "no-existe"}).status_code == 404
    assert manda_a_entrar(TestClient(crear_app()).get("/crm", params={"dias": "5"}))


@pytest.mark.parametrize("dias", ["7", "30", "90"])
def test_dias_validos(dias):
    respuesta = cliente_como(PLATAFORMA).get("/crm", params={"dias": dias})
    assert respuesta.status_code == 200
    assert f'<option value="{dias}" selected>' in respuesta.text


@pytest.mark.parametrize("dias", ["0", "8", "365", "-7", "abc", ""])
def test_dias_fuera_de_lista_422(dias):
    assert cliente_como(PLATAFORMA).get("/crm", params={"dias": dias}).status_code == 422


def test_consorcio_inexistente_404():
    assert cliente_como(PLATAFORMA).get("/crm", params={"consorcio": "no-existe"}).status_code == 404


def test_consorcio_vacio_es_todos():
    _turnos(PALMERAS, 2)
    _turnos(ROBLES, 1)
    html = cliente_como(PLATAFORMA).get("/crm", params={"consorcio": ""}).text
    assert _valor_de(html, "turnos en el período") == 3


def test_filtro_de_consorcio_cambia_los_numeros_y_el_tope_sigue_global():
    _turnos(PALMERAS, 2)
    _turnos(ROBLES, 1)
    with Session(postgres.engine()) as s:
        s.add(GastoModelo(consorcio_id=PALMERAS, nodo="n", modelo="m", tokens_entrada=1, tokens_salida=1, costo_usd=1.5))
        s.commit()
    html = cliente_como(PLATAFORMA).get("/crm", params={"consorcio": ROBLES, "dias": "7"}).text
    assert _valor_de(html, "turnos en el período") == 1
    assert f'<option value="{ROBLES}" selected>' in html
    assert "Tope mensual <small>(global)</small>" in html
    assert "no cambia con el filtro de consorcio" in html
    assert "1.5000 de 20.00 USD" in html  # gastado en palmeras, filtrando por robles


def test_turnos_de_evaluacion_no_aparecen():
    _turnos(PALMERAS, 2)
    _turnos(PALMERAS, 5, origen="evaluacion")
    assert _valor_de(cliente_como(PLATAFORMA).get("/crm").text, "turnos en el período") == 2


VISTAS = ("resumen", "calidad", "uso")


def _todas(cliente) -> str:
    """El CRM está partido en vistas: lo que vale para toda la pantalla se mira en las tres."""
    return "".join(cliente.get("/crm", params={"vista": v}).text for v in VISTAS)


def test_vista_desconocida_422():
    assert cliente_como(PLATAFORMA).get("/crm", params={"vista": "otra"}).status_code == 422


def test_cada_vista_conserva_el_filtro():
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": "uso", "dias": "7"}).text
    assert '<input type="hidden" name="vista" value="uso">' in html
    assert 'href="/crm?vista=calidad&amp;dias=7&amp;consorcio="' in html


def test_rotulos_obligatorios():
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": "calidad"}).text
    assert "Es espera, no esfuerzo humano." in html
    assert "no el router" in html
    assert "Telegram no pasa por el router" in html and "su gasto sí entra en el tope" in html
    assert "no se promedian" in html


def test_sin_archivo_de_evaluacion_y_sin_turnos():
    html = _todas(cliente_como(PLATAFORMA))
    assert "sin corrida" in html
    assert "sin datos" in html
    assert "0.0 %" not in html  # sin casos no hay porcentaje, ni siquiera cero


def test_con_datos_muestra_la_evaluacion_y_cada_indicador_por_separado(base):
    (base / "2026-10-26-consultas-test.json").write_text(
        json.dumps({"particion": "test", "generacion": {"n": 13, "tasa_correctas": 0.615}}), encoding="utf-8")
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": "calidad"}).text
    assert "61.5 %" in html
    assert "2026-10-26-consultas-test.json" in html
    assert "sin corrida" not in html


def test_aviso_de_modelos_sin_precio():
    assert "subestimado" not in _todas(cliente_como(PLATAFORMA))
    with Session(postgres.engine()) as s:
        s.add(GastoModelo(consorcio_id=None, nodo="n", modelo="modelo-nuevo", tokens_entrada=7, tokens_salida=3, costo_usd=None))
        s.commit()
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": "uso"}).text
    assert "el gasto está subestimado" in html
    assert "<td>modelo-nuevo</td><td>1</td><td>10</td>" in html


def test_tope_en_cero_no_divide_ni_muestra_barra(monkeypatch):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.0)
    respuesta = cliente_como(PLATAFORMA).get("/crm")
    assert respuesta.status_code == 200
    assert "sin tope configurado" in respuesta.text


def test_tope_alcanzado_avisa(monkeypatch):
    monkeypatch.setattr(config, "tope_usd_mensual", 0.5)
    with Session(postgres.engine()) as s:
        s.add(GastoModelo(consorcio_id=None, nodo="n", modelo="m", tokens_entrada=1, tokens_salida=1, costo_usd=0.6))
        s.commit()
    assert "Tope alcanzado" in cliente_como(PLATAFORMA).get("/crm").text


def test_html_sin_textos_ni_nombres():
    _turnos(PALMERAS, 1)
    with Session(postgres.engine()) as s:
        usuario = _id_de(s, "demo-prop-palmeras")
        nombres = [n for n in s.scalars(select(Usuario.nombre)) if n]
        nombres_consorcio = [n for n in s.scalars(select(Consorcio.nombre)) if n]
        s.add(Solicitud(tipo="derivacion", consorcio_id=PALMERAS, autor_id=usuario, motivo="pedido_usuario",
                        mensaje=TEXTO_PRIVADO, titulo=TEXTO_PRIVADO, detalle=TEXTO_PRIVADO))
        s.add(Conversacion(id="d" * 32, usuario_id=usuario, consorcio_id=PALMERAS))
        s.flush()
        s.add(Mensaje(conversacion_id="d" * 32, rol="usuario", texto=TEXTO_PRIVADO))
        s.commit()
    html = _todas(cliente_como(PLATAFORMA))
    assert TEXTO_PRIVADO not in html
    assert nombres and nombres_consorcio
    assert not [n for n in nombres + nombres_consorcio if n in html]


def test_id_de_consorcio_sale_escapado():
    raro = "<b>raro</b>"
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id=raro, nombre="x"))
        s.commit()
    try:
        html = cliente_como(PLATAFORMA).get("/crm").text
        assert raro not in html
        assert "&lt;b&gt;raro&lt;/b&gt;" in html
    finally:
        with Session(postgres.engine()) as s:
            s.delete(s.get(Consorcio, raro))
            s.commit()


@pytest.mark.parametrize("vista", VISTAS)
def test_html_sin_recursos_externos_ni_scripts_ni_estilos_en_linea(vista):
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": vista}).text
    # El único script es el htmx vendorizado de `base.html`, que el CRM no usa
    assert re.findall(r"<script\b[^>]*>", html.lower()) == ['<script src="/estaticos/htmx.min.js" defer>']
    assert not re.search(r"""(src|href)=["'](https?:)?//""", html)
    assert "style=" not in html.lower()  # la CSP `default-src 'self'` los bloquearía
    assert "<style" not in html.lower()
    assert not re.search(r"chart|d3\.|plotly", html, re.IGNORECASE)
    assert '<link rel="stylesheet" href="/estaticos/crm.css">' in html


def test_lleva_el_encabezado_comun_y_el_filtro_no_lo_confunde():
    cliente = cliente_como(PLATAFORMA)
    html = cliente.get("/crm", params={"consorcio": PALMERAS}).text
    assert 'href="/crm"' in html and 'action="/salir"' in html
    assert f'<option value="{PALMERAS}" selected>' in html
    assert 'class="contexto"' not in html  # el filtro no es el consorcio de la sesión
    assert cliente.post("/salir", data={"csrf": cliente.csrf}, follow_redirects=False).status_code == 303


def test_plantilla_sin_safe():
    assert "|safe" not in PLANTILLA.read_text(encoding="utf-8").replace(" ", "")


def test_css_servido_sin_importar_nada_externo():
    cliente = cliente_como(PLATAFORMA)
    respuesta = cliente.get("/estaticos/crm.css")
    assert respuesta.status_code == 200
    assert "text/css" in respuesta.headers["content-type"]
    assert "url(" not in respuesta.text and "@import" not in respuesta.text


def test_grafico_de_actividad_sin_turnos_avisa_y_con_turnos_dibuja_barras():
    assert "Sin turnos en este período" in cliente_como(PLATAFORMA).get("/crm").text
    _turnos(PALMERAS, 3)
    html = cliente_como(PLATAFORMA).get("/crm").text
    assert "Sin turnos en este período" not in html
    assert 'class="crm-columnas"' in html and 'height="98.00"' in html  # el día con más turnos llena la escala


def test_variacion_contra_el_periodo_previo(monkeypatch):
    real = crm.panel

    def con_previo(*args):
        datos = real(*args)
        return {**datos, "periodo_anterior": {"preguntas": 2, "elevaciones": 0}}

    monkeypatch.setattr(crm, "panel", con_previo)
    _turnos(PALMERAS, 3)
    html = cliente_como(PLATAFORMA).get("/crm").text
    assert "50 %" in html  # de 2 a 3 preguntas
    assert "sin período previo" in html  # con 0 no hay porcentaje que calcular



# ------------------------------------------------------------------ filtro y agrupación por Administración


def _id_demo() -> int:
    with Session(postgres.engine()) as s:
        return s.scalar(select(Administracion.id).where(Administracion.nombre == ADMINISTRACION_DEMO))


@pytest.fixture
def otra_administracion():
    """Un consorcio de otra Administración, que se borra al final."""
    with Session(postgres.engine()) as s:
        adm = Administracion(nombre="Administración Ajena")
        s.add(adm)
        s.flush()
        s.add(Consorcio(id="prueba-ajeno", nombre="Consorcio Ajeno de Prueba", administracion_id=adm.id))
        s.commit()
        yield adm.id
    with postgres.engine().begin() as con:
        con.exec_driver_sql("TRUNCATE turnos CASCADE")
        con.exec_driver_sql("DELETE FROM consorcios WHERE id = 'prueba-ajeno'")
        con.exec_driver_sql("DELETE FROM administraciones WHERE nombre = 'Administración Ajena'")


def test_filtro_por_administracion_lista_solo_sus_consorcios(otra_administracion):
    _turnos(PALMERAS, 2)
    _turnos("prueba-ajeno", 5)
    html = cliente_como(PLATAFORMA).get("/crm", params={"administracion": str(_id_demo())}).text
    assert f'<option value="{_id_demo()}" selected>{ADMINISTRACION_DEMO}</option>' in html
    assert f'<option value="{PALMERAS}"' in html and f'<option value="{ROBLES}"' in html
    assert 'value="prueba-ajeno"' not in html
    assert _valor_de(html, "turnos en el período") == 2  # los 5 del consorcio ajeno no entran
    ajena = cliente_como(PLATAFORMA).get("/crm", params={"administracion": str(otra_administracion)}).text
    assert _valor_de(ajena, "turnos en el período") == 5 and f'value="{PALMERAS}"' not in ajena


def test_sin_administracion_muestra_los_consorcios_sueltos(otra_administracion):
    with Session(postgres.engine()) as s:
        s.add(Consorcio(id="chat-99", nombre="Base del chat de prueba"))
        s.commit()
    try:
        _turnos("chat-99", 1)
        _turnos(PALMERAS, 3)
        html = cliente_como(PLATAFORMA).get("/crm", params={"administracion": "sin"}).text
        assert '<option value="sin" selected>Sin administración</option>' in html
        assert 'value="chat-99"' in html and f'value="{PALMERAS}"' not in html
        assert _valor_de(html, "turnos en el período") == 1
    finally:
        with postgres.engine().begin() as con:
            con.exec_driver_sql("TRUNCATE turnos CASCADE")
            con.exec_driver_sql("DELETE FROM consorcios WHERE id = 'chat-99'")


@pytest.mark.parametrize("valor", ["x", "-1", "1.5", "１", "0x1", "1 ", "%00", "9" * 15, "a" * 300])
def test_administracion_invalida_da_422_con_menu(valor):
    respuesta = cliente_como(PLATAFORMA).get(f"/crm?administracion={valor}")
    assert respuesta.status_code == 422
    assert "text/html" in respuesta.headers["content-type"]
    assert 'class="menu-principal' in respuesta.text or "menu-principal" in respuesta.text


def test_administracion_inexistente_da_404_con_menu():
    respuesta = cliente_como(PLATAFORMA).get("/crm", params={"administracion": "999999"})
    assert respuesta.status_code == 404 and "menu-principal" in respuesta.text


def test_administracion_no_revela_nada_a_quien_no_es_de_plataforma():
    cliente = cliente_como("demo-admin-palmeras", PALMERAS)
    for valor in ("x", "999999", str(_id_demo())):
        assert cliente.get("/crm", params={"administracion": valor}).status_code == 404


def test_consorcio_de_otra_administracion_saca_el_consorcio(otra_administracion):
    cliente = cliente_como(PLATAFORMA)
    r = cliente.get("/crm", params={"vista": "uso", "dias": "7", "administracion": str(otra_administracion),
                                    "consorcio": PALMERAS}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/crm?vista=uso&dias=7&consorcio=&administracion={otra_administracion}"
    assert cliente.get(r.headers["location"]).status_code == 200


def test_los_links_de_las_vistas_conservan_la_administracion():
    html = cliente_como(PLATAFORMA).get("/crm", params={"administracion": "sin", "dias": "7"}).text
    assert 'href="/crm?vista=calidad&amp;dias=7&amp;consorcio=&amp;administracion=sin"' in html


@pytest.mark.parametrize("vista", VISTAS)
def test_con_filtro_de_administracion_sigue_sin_estilos_ni_textos(vista):
    _turnos(PALMERAS, 1)
    html = cliente_como(PLATAFORMA).get("/crm", params={"vista": vista, "administracion": str(_id_demo())}).text
    assert "style=" not in html.lower() and "<style" not in html.lower()
    assert re.findall(r"<script\b[^>]*>", html.lower()) == ['<script src="/estaticos/htmx.min.js" defer>']
    with Session(postgres.engine()) as s:
        nombres = [n for n in [*s.scalars(select(Usuario.nombre)), *s.scalars(select(Consorcio.nombre))] if n]
    assert not [n for n in nombres if n in html]


def test_resumen_y_uso_agrupan_por_administracion():
    _turnos(PALMERAS, 2)
    _turnos(ROBLES, 1)
    with Session(postgres.engine()) as s:
        s.add(GastoModelo(consorcio_id=PALMERAS, nodo="n", modelo="m", tokens_entrada=1234, tokens_salida=56, costo_usd=0.25))
        s.add(GastoModelo(consorcio_id=None, nodo="n", modelo="m", tokens_entrada=1, tokens_salida=1, costo_usd=0.5))
        s.commit()
    cliente = cliente_como(PLATAFORMA)
    resumen = cliente.get("/crm").text
    assert f'<th scope="row">{ADMINISTRACION_DEMO}</th><td>3</td><td>0.2500</td><td>1234</td><td>56</td>' in resumen
    assert "Sin consorcio" in resumen
    uso = cliente.get("/crm", params={"vista": "uso"}).text
    assert f'<td>{PALMERAS}</td><td>2</td><td>0.2500</td><td>1234</td><td>56</td>' in uso
    assert f'<td>{ROBLES}</td><td>1</td><td>0.0000</td>' in uso
    assert 'class="crm-columnas crm-costo"' in uso and 'height="98.00"' in uso  # el día más caro llena la escala
    filtrado = cliente.get("/crm", params={"vista": "uso", "administracion": str(_id_demo())}).text
    assert "Sin consorcio" not in filtrado


def test_metricas_nuevas_sin_casos_dicen_sin_datos():
    html = _todas(cliente_como(PLATAFORMA))
    for rotulo in ("Latencia", "Elevadas después de una respuesta", "Costo por día", "Costo por canal",
                   "Costo de ingesta por documento", "Pendientes hoy", "Tasa de edición", "Tasa de derivación"):
        assert rotulo in html
    assert "Sin gasto con precio en este período" in html
    assert "0.0 %" not in html
    # La semilla no registra costo de ingesta: se dice, no se inventa un promedio
    assert "sin costo registrado" in html
