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

from ..agente import revision
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
from ..almacen.postgres import MOTIVOS_REVISION, VEREDICTOS, listar_documentos
from ..canales.formato import formatear_ingesta
from ..canales.telegram import ruta_entrada
from ..config import config
from ..identidad import Identidad, es_plataforma, listar_usuarios_demo, membresias_de
from ..ingesta.grafo import ingestar_pdf
from .autenticacion import LoginFallido, datos_invitacion, proveedor_para, registrar
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
    if identidad and identidad.es_administracion:
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


def _pantalla_previa(request: Request, plantilla: str, status_code: int = 200, **datos) -> Response:
    """Pantallas sin sesión (entrar, registro): su CSRF se ata a una cookie previa nueva en cada render."""
    nonce = uuid4().hex
    respuesta = plantillas.TemplateResponse(request, plantilla, {
        "csrf": token_csrf_previo(nonce), "selector": config.web_selector_usuarios, **datos,
    }, status_code=status_code)
    poner_cookie_previa(respuesta, nonce)
    return respuesta


def _iniciar_sesion(sujeto: str, proveedor: str) -> Response:
    respuesta = _ir_a("/consorcios")
    poner_cookie_sesion(respuesta, nueva_sesion(sujeto, proveedor))
    respuesta.delete_cookie(COOKIE_PREVIA, path="/")
    return respuesta


def _usuarios_demo() -> list[dict]:
    return listar_usuarios_demo() if config.web_selector_usuarios else []


@router.get("/entrar")
def entrar(request: Request) -> Response:
    return _pantalla_previa(request, "entrar.html", usuarios=_usuarios_demo())


@router.post("/entrar", dependencies=[Depends(limitar_formulario), Depends(verificar_csrf_previo)])
def entrar_post(request: Request, datos: Formulario) -> Response:
    proveedor = proveedor_para(request)
    try:
        sujeto = proveedor.sujeto(request)
    except LoginFallido as e:
        return _pantalla_previa(request, "entrar.html", e.status_code, usuarios=_usuarios_demo(), error=e.mensaje,
                                email=_campo(datos, "email")[:320])
    if sujeto is None:
        raise HTTPException(status_code=401)
    return _iniciar_sesion(sujeto, proveedor.nombre)


@router.get("/registro")
def registro(request: Request, token: str = "") -> Response:
    """El link de la invitación. Una inválida da la misma pantalla que una vencida."""
    invitacion = datos_invitacion(token)
    if invitacion is None:
        return _pantalla_previa(request, "registro.html", 400, invitacion=None, token="")
    return _pantalla_previa(request, "registro.html", invitacion=invitacion, token=token)


@router.post("/registro", dependencies=[Depends(limitar_formulario), Depends(verificar_csrf_previo)])
def registro_post(request: Request, datos: Formulario) -> Response:
    try:
        sujeto = registrar(request)
    except LoginFallido as e:
        token = _campo(datos, "token")
        return _pantalla_previa(request, "registro.html", e.status_code, invitacion=datos_invitacion(token),
                                token=token, error=e.mensaje)
    return _iniciar_sesion(sujeto, "login")


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
        # la columna de la derecha: lo último del vecino y del consorcio, para tenerlo a mano
        solicitudes=mis_solicitudes(identidad, limite=3), documentos=listar_documentos(identidad.consorcio_id)[:3],
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
def documentos(request: Request, sesion: SesionActual, identidad: Miembro, tipo: str = "") -> Response:
    """`tipo` filtra la lista; uno que no existe deja la lista vacía y no es un error.

    La administración ve una sección por cada consorcio que administra (el de la sesión primero) para revisar
    rápido lo cargado en cada uno. Solo los que administra: en los demás es vecina y ve solo el de la sesión."""
    membresias = membresias_de(sesion.sujeto_externo)
    if identidad.es_administracion:
        consorcios = [m for m in membresias if m["rol"] == "administracion"]
        consorcios.sort(key=lambda m: m["consorcio_id"] != identidad.consorcio_id)
    else:
        consorcios = [m for m in membresias if m["consorcio_id"] == identidad.consorcio_id]
    grupos = [{**m, "actual": m["consorcio_id"] == identidad.consorcio_id,
               "documentos": listar_documentos(m["consorcio_id"])} for m in consorcios]
    tipos: dict[str, int] = {}
    for g in grupos:
        for d in g["documentos"]:
            tipos[d["tipo"] or "otro"] = tipos.get(d["tipo"] or "otro", 0) + 1
    total = sum(len(g["documentos"]) for g in grupos)
    actuales = next((g["documentos"] for g in grupos if g["actual"]), [])
    if tipo:
        for g in grupos:
            g["documentos"] = [d for d in g["documentos"] if (d["tipo"] or "otro") == tipo]
    return pagina(request, "documentos.html", sesion, identidad, max_mb=config.web_max_mb_pdf, grupos=grupos,
                  total=total, tipos=dict(sorted(tipos.items())), tipo=tipo,
                  recientes=[d for d in actuales if d["activo"]][:3])


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
def solicitudes_propias(request: Request, sesion: SesionActual, identidad: Miembro, solicitud: str = "") -> Response:
    """Lista y detalle: `solicitud` elige cuál se ve al lado; sin ella (o si no es tuya), la más reciente."""
    solicitudes = mis_solicitudes(identidad)
    return pagina(request, "mis_solicitudes.html", sesion, identidad, solicitudes=solicitudes,
                  elegida=_elegida(solicitudes, "id", solicitud))


@router.get("/bandeja")
def bandeja(request: Request, sesion: SesionActual, identidad: Administrador,
            ver: str = "pendientes", solicitud: str = "") -> Response:
    ver = "resueltas" if ver == "resueltas" else "pendientes"
    solicitudes = listar_solicitudes(identidad)
    pendientes = [s for s in solicitudes if s["estado"] == "pendiente"]
    resueltas = [s for s in solicitudes if s["estado"] != "pendiente"]
    lista = resueltas if ver == "resueltas" else pendientes
    return pagina(request, "bandeja.html", sesion, identidad, ver=ver, lista=lista,
                  pendientes=len(pendientes), resueltas=len(resueltas), elegida=_elegida(lista, "id", solicitud))


def _elegida(filas: list[dict], clave: str, valor: str) -> dict | None:
    """La fila pedida por la URL, o la primera. Solo busca en `filas`, que ya vienen filtradas por permiso."""
    return next((f for f in filas if str(f[clave]) == valor), filas[0] if filas else None)


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


# ------------------------------------------------------------------ revisión de respuestas


@router.get("/revisar")
def revisar_respuestas(request: Request, sesion: SesionActual, identidad: Administrador,
                       ver: str = "pendientes", turno: str = "") -> Response:
    ver = "revisadas" if ver == "revisadas" else "pendientes"
    respuestas = revision.listar(identidad, ver)
    return pagina(request, "revisar.html", sesion, identidad, ver=ver, respuestas=respuestas,
                  elegida=_elegida(respuestas, "turno_id", turno), motivos=MOTIVOS_REVISION, veredictos=VEREDICTOS)


@router.post("/revisar/{turno_id}", dependencies=POST_ADMINISTRADOR)
def revisar_post(turno_id: str, identidad: Administrador, datos: Formulario) -> Response:
    try:
        revision.revisar(identidad, _id(turno_id), _campo(datos, "veredicto"), _campo(datos, "motivo") or None,
                         _campo(datos, "respuesta_correcta") or None)
    except revision.RevisionInvalida as e:
        raise HTTPException(status_code=422) from e
    except revision.RespuestaInexistente as e:
        raise HTTPException(status_code=404) from e
    except SinPermiso as e:
        raise HTTPException(status_code=403) from e
    return _ir_a("/revisar?ver=revisadas" if _campo(datos, "ver") == "revisadas" else "/revisar")
