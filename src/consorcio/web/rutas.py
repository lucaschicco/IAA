"""Rutas de la web de pruebas: otro cliente de las tools, sin lógica propia.

Toda ruta usa la identidad que resuelve `identidad_actual` en cada pedido: de lo que manda el cliente
solo se lee el contenido (el mensaje, una respuesta, el id de una solicitud), nunca quién es ni en qué
consorcio opera. Los ids que llegan se validan contra esa identidad en la capa de abajo.

Ningún endpoint declara `Form()` ni `File()`: FastAPI leería el cuerpo antes de correr las dependencias.
Acá el orden es sesión o rol, tamaño, CSRF y recién después el formulario.
"""
from __future__ import annotations

import logging
import re
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.datastructures import FormData, UploadFile

from ..agente.bandeja import (
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
from ..agente.grafo import atender
from ..agente.memoria import conversacion_valida, conversaciones_de, historial
from ..almacen.postgres import listar_documentos
from ..canales.formato import formatear_ingesta
from ..canales.telegram import ruta_entrada
from ..config import config
from ..identidad import Identidad, es_plataforma, listar_usuarios_demo, membresias_de
from ..ingesta.grafo import ingestar_pdf
from .autenticacion import ProveedorIdentidad, exigir_proveedor
from .sesion import (
    COOKIE_PREVIA,
    NOMBRE_COOKIE,
    Sesion,
    SesionActual,
    exigir_administrador,
    identidad_actual,
    nueva_sesion,
    poner_cookie_previa,
    poner_cookie_sesion,
    sesion_actual,
    token_csrf,
    token_csrf_previo,
    verificar_csrf,
    verificar_csrf_previo,
)

log = logging.getLogger("consorcio.web")

router = APIRouter()
plantillas = Jinja2Templates(directory=Path(__file__).resolve().parent / "plantillas")

MAX_FORMULARIO = 64 * 1024  # bytes: de sobra para AGENTE_MAX_MENSAJE con el texto ya codificado
MARGEN_MULTIPART = 64 * 1024  # bytes: bordes y campos que acompañan al PDF
MAX_ID = 2**31 - 1  # lo que entra en la columna: más grande sería un error de la base y no un 422
MAX_NOMBRE = 200
MAGICO_PDF = b"%PDF-"
BLOQUE = 1024 * 1024
RESULTADOS_SIN_VOTO = {"mensaje_invalido", "conversacion_invalida", "tope_excedido", "error"}
MENSAJE_CARGA_FALLIDA = "No se pudo procesar el archivo. Probá de nuevo o avisale al equipo de la plataforma."


def _maximo_pdf() -> int:
    return config.web_max_mb_pdf * 1024 * 1024


def _exigir_longitud(request: Request, maximo: int) -> None:
    """Corta antes de leer el cuerpo. Con `Transfer-Encoding` la longitud no dice cuánto va a llegar."""
    largo = request.headers.get("content-length")
    if largo is None or "transfer-encoding" in request.headers:
        raise HTTPException(status_code=411)
    if not largo.isdecimal():
        raise HTTPException(status_code=400)
    if int(largo) > maximo:
        raise HTTPException(status_code=413)


def limitar_formulario(request: Request) -> None:
    _exigir_longitud(request, MAX_FORMULARIO)


def limitar_carga(request: Request) -> None:
    _exigir_longitud(request, _maximo_pdf() + MARGEN_MULTIPART)


async def formulario(request: Request) -> FormData:
    """Va después de los controles. Queda en `state` porque el selector lo lee en código sincrónico."""
    request.state.formulario = await request.form()
    return request.state.formulario


Miembro = Annotated[Identidad, Depends(identidad_actual)]
Administrador = Annotated[Identidad, Depends(exigir_administrador)]
Formulario = Annotated[FormData, Depends(formulario)]

POST_SESION = [Depends(sesion_actual), Depends(limitar_formulario), Depends(verificar_csrf)]
POST_MIEMBRO = [Depends(identidad_actual), Depends(limitar_formulario), Depends(verificar_csrf)]
POST_ADMINISTRADOR = [Depends(exigir_administrador), Depends(limitar_formulario), Depends(verificar_csrf)]


def _campo(datos: FormData, nombre: str) -> str:
    """Texto de un campo: un archivo donde iba texto cuenta como vacío. Postgres no acepta NUL."""
    valor = datos.get(nombre)
    return valor.replace("\x00", "").strip() if isinstance(valor, str) else ""


def _obligatorio(datos: FormData, nombre: str) -> str:
    valor = _campo(datos, nombre)
    if not valor:
        raise HTTPException(status_code=422)
    return valor


def _id(valor: str) -> int:
    if not valor.isdecimal() or not 0 < int(valor) <= MAX_ID:
        raise HTTPException(status_code=422)
    return int(valor)


def pagina(request: Request, plantilla: str, sesion: Sesion, identidad: Identidad | None = None, **datos) -> Response:
    """Lo que el menú lateral y la barra superior necesitan en toda pantalla con sesión."""
    consorcio = next((m["nombre"] for m in membresias_de(sesion.sujeto_externo)
                      if identidad and m["consorcio_id"] == identidad.consorcio_id), None)
    por_atender = 0
    if identidad and identidad.rol == "administrador":
        por_atender = sum(s["estado"] == "pendiente" for s in listar_solicitudes(identidad))
    return plantillas.TemplateResponse(request, plantilla, {
        "sesion": sesion, "identidad": identidad, "consorcio": consorcio, "csrf": token_csrf(sesion),
        "plataforma": es_plataforma(sesion.sujeto_externo), "por_atender": por_atender, **datos,
    })


def _ir_a(destino: str) -> RedirectResponse:
    return RedirectResponse(destino, status_code=303)


# ------------------------------------------------------------------ entrada y salida


@router.get("/salud")
def salud() -> dict:
    return {"estado": "ok"}


@router.get("/")
def inicio(sesion: SesionActual) -> Response:
    return _ir_a("/consorcios")


@router.get("/entrar", dependencies=[Depends(exigir_proveedor)])
def entrar(request: Request) -> Response:
    nonce = uuid4().hex
    respuesta = plantillas.TemplateResponse(request, "entrar.html", {
        "usuarios": listar_usuarios_demo(), "csrf": token_csrf_previo(nonce),
    })
    poner_cookie_previa(respuesta, nonce)
    return respuesta


@router.post("/entrar", dependencies=[Depends(exigir_proveedor), Depends(limitar_formulario), Depends(verificar_csrf_previo)])
def entrar_post(
    request: Request, proveedor: Annotated[ProveedorIdentidad, Depends(exigir_proveedor)], datos: Formulario
) -> Response:
    sujeto = proveedor.sujeto(request)
    if sujeto is None:
        raise HTTPException(status_code=401)
    respuesta = _ir_a("/consorcios")
    poner_cookie_sesion(respuesta, nueva_sesion(sujeto, proveedor.nombre))
    respuesta.delete_cookie(COOKIE_PREVIA, path="/")
    return respuesta


@router.post("/salir", dependencies=POST_SESION)
def salir() -> Response:
    respuesta = _ir_a("/entrar")
    respuesta.delete_cookie(NOMBRE_COOKIE, path="/")
    return respuesta


def _elegir_consorcio(sesion: Sesion, consorcio_id: str) -> Response:
    respuesta = _ir_a("/chat")
    poner_cookie_sesion(respuesta, replace(sesion, consorcio_id=consorcio_id))
    return respuesta


@router.get("/consorcios")
def consorcios(request: Request, sesion: SesionActual) -> Response:
    membresias = membresias_de(sesion.sujeto_externo)
    if len(membresias) == 1:
        return _elegir_consorcio(sesion, membresias[0]["consorcio_id"])
    if not membresias and es_plataforma(sesion.sujeto_externo):
        return _ir_a("/crm")
    return pagina(request, "consorcios.html", sesion, membresias=membresias)


@router.post("/consorcios", dependencies=POST_SESION)
def consorcios_post(sesion: SesionActual, datos: Formulario) -> Response:
    """Valida contra las membresías: elegir un consorcio ajeno no llega a firmarse en la cookie."""
    elegido = _campo(datos, "consorcio_id")
    if elegido not in {m["consorcio_id"] for m in membresias_de(sesion.sujeto_externo)}:
        raise HTTPException(status_code=403)
    return _elegir_consorcio(sesion, elegido)


# ------------------------------------------------------------------ chat


@router.get("/chat")
def chat(request: Request, sesion: SesionActual, identidad: Miembro, conversacion: str | None = None) -> Response:
    """Una conversación ajena da lo mismo que una que no existe."""
    if conversacion and not conversacion_valida(identidad, conversacion):
        raise HTTPException(status_code=404)
    return pagina(
        request, "chat.html", sesion, identidad, conversacion_id=conversacion or "",
        conversaciones=conversaciones_de(identidad), mensajes=historial(identidad, conversacion) if conversacion else [],
        max_mensaje=config.agente_max_mensaje,
    )


@router.post("/chat/mensaje", dependencies=POST_MIEMBRO)
def chat_mensaje(request: Request, sesion: SesionActual, identidad: Miembro, datos: Formulario) -> Response:
    mensaje = _campo(datos, "mensaje")
    turno = atender(mensaje, identidad, _campo(datos, "conversacion_id") or None, canal="web")
    return plantillas.TemplateResponse(request, "_turno.html", {
        "pregunta": mensaje, "turno": turno, "csrf": token_csrf(sesion), "max_mensaje": config.agente_max_mensaje,
        "con_voto": turno["turno_id"] is not None and turno["resultado"] not in RESULTADOS_SIN_VOTO,
        "conversaciones": conversaciones_de(identidad),  # la conversación nueva tiene que aparecer en el menú
    })


@router.post("/chat/feedback", dependencies=POST_MIEMBRO)
def chat_feedback(request: Request, identidad: Miembro, datos: Formulario) -> Response:
    voto = _campo(datos, "valor")
    if voto not in ("1", "-1"):
        raise HTTPException(status_code=422)
    try:
        registrar_feedback(identidad, _id(_campo(datos, "turno_id")), int(voto))
    except TurnoAjeno as e:
        raise HTTPException(status_code=404) from e
    return plantillas.TemplateResponse(request, "_voto.html", {"valor": int(voto)})


# ------------------------------------------------------------------ documentos


@router.get("/documentos")
def documentos(request: Request, sesion: SesionActual, identidad: Miembro) -> Response:
    return pagina(request, "documentos.html", sesion, identidad, max_mb=config.web_max_mb_pdf,
                   documentos=listar_documentos(identidad.consorcio_id))


def _guardar_pdf(archivo: UploadFile) -> Path:
    """Copia a `datos/entrada`, contando: `Content-Length` es lo que dice el cliente, no lo que mandó."""
    cabecera = archivo.file.read(len(MAGICO_PDF))
    if cabecera != MAGICO_PDF:
        raise HTTPException(status_code=415)
    nombre = re.sub(r"[\x00-\x1f\x7f]", "", archivo.filename or "")[-MAX_NOMBRE:]
    ruta = ruta_entrada(config.ruta_entrada(), uuid4().hex, nombre)
    copiados = len(cabecera)
    with ruta.open("wb") as destino:
        destino.write(cabecera)
        while copiados <= _maximo_pdf() and (bloque := archivo.file.read(BLOQUE)):
            copiados += len(bloque)
            destino.write(bloque)
    if copiados > _maximo_pdf():
        ruta.unlink()
        ruta.parent.rmdir()
        raise HTTPException(status_code=413)
    return ruta


def _texto_carga(final: dict) -> str:
    """Los cortes llevan texto propio: el de Telegram manda a `--forzar`, que acá no existe."""
    estado, nombre = final.get("estado"), final.get("nombre_archivo", "El documento")
    existente = final.get("documento_existente") or {}
    previo = existente.get("titulo") or existente.get("nombre_archivo") or nombre
    if estado == "duplicado":
        return f"Ese PDF ya estaba cargado en este consorcio ({previo}). No se procesó de nuevo."
    if estado == "en_proceso":
        return f"{nombre} ya se está procesando en este consorcio. Alcanza con esperar a que termine."
    if estado == "periodo_duplicado":
        return (f"No se guardó {nombre}: este consorcio ya tiene la liquidación del período "
                f"{existente.get('periodo') or 'de este PDF'}. Para reemplazarla, avisale al equipo de la plataforma.")
    if estado == "tope_excedido":
        return f"No se procesó {nombre}: se alcanzó el tope mensual de gasto en modelos."
    return formatear_ingesta(final)


@router.post("/documentos", dependencies=[Depends(exigir_administrador), Depends(limitar_carga), Depends(verificar_csrf)])
def documentos_post(request: Request, identidad: Administrador, datos: Formulario) -> Response:
    """Sincrónico a propósito, con indicador en la pantalla: la cola de ingesta está fuera de alcance."""
    archivo = datos.get("archivo")
    if not isinstance(archivo, UploadFile):
        raise HTTPException(status_code=422)
    try:
        ruta = _guardar_pdf(archivo)
        mensaje = _texto_carga(ingestar_pdf(str(ruta), consorcio_id=identidad.consorcio_id, usuario_id=identidad.usuario_id))
    except HTTPException:
        raise
    except Exception as e:
        log.warning("falló la carga de un PDF: %s", type(e).__name__)
        return plantillas.TemplateResponse(request, "_error.html", {"mensaje": MENSAJE_CARGA_FALLIDA}, status_code=500)
    return plantillas.TemplateResponse(request, "_carga.html", {
        "mensaje": mensaje, "documentos": listar_documentos(identidad.consorcio_id),
    })


# ------------------------------------------------------------------ solicitudes


@router.get("/mis-solicitudes")
def solicitudes_propias(request: Request, sesion: SesionActual, identidad: Miembro) -> Response:
    return pagina(request, "mis_solicitudes.html", sesion, identidad, solicitudes=mis_solicitudes(identidad))


@router.get("/bandeja")
def bandeja(request: Request, sesion: SesionActual, identidad: Administrador) -> Response:
    solicitudes = listar_solicitudes(identidad)
    return pagina(
        request, "bandeja.html", sesion, identidad,
        pendientes=[s for s in solicitudes if s["estado"] == "pendiente"],
        resueltas=[s for s in solicitudes if s["estado"] != "pendiente"],
    )


def _transicion(funcion, *args) -> Response:
    try:
        funcion(*args)
    except SinPermiso as e:
        raise HTTPException(status_code=403) from e
    except SolicitudInexistente as e:
        raise HTTPException(status_code=404) from e
    except SolicitudYaResuelta as e:
        raise HTTPException(status_code=409) from e
    return _ir_a("/bandeja")


@router.post("/bandeja/{solicitud_id}/resolver", dependencies=POST_ADMINISTRADOR)
def bandeja_resolver(solicitud_id: str, identidad: Administrador, datos: Formulario) -> Response:
    return _transicion(resolver_derivacion, identidad, _id(solicitud_id), _obligatorio(datos, "respuesta"))


@router.post("/bandeja/{solicitud_id}/aprobar", dependencies=POST_ADMINISTRADOR)
def bandeja_aprobar(solicitud_id: str, identidad: Administrador, datos: Formulario) -> Response:
    return _transicion(aprobar_reclamo, identidad, _id(solicitud_id), _campo(datos, "respuesta") or None)


@router.post("/bandeja/{solicitud_id}/editar", dependencies=POST_ADMINISTRADOR)
def bandeja_editar(solicitud_id: str, identidad: Administrador, datos: Formulario) -> Response:
    return _transicion(
        editar_reclamo, identidad, _id(solicitud_id), _obligatorio(datos, "titulo"), _obligatorio(datos, "detalle"),
        _campo(datos, "respuesta") or None,
    )


@router.post("/bandeja/{solicitud_id}/rechazar", dependencies=POST_ADMINISTRADOR)
def bandeja_rechazar(solicitud_id: str, identidad: Administrador, datos: Formulario) -> Response:
    return _transicion(rechazar_reclamo, identidad, _id(solicitud_id), _obligatorio(datos, "respuesta"))
