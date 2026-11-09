"""Bot de Telegram: recibe PDFs y preguntas, los pasa a las tools y contesta.

Corre con *long polling* (`consorcio bot`), así que no necesita IP pública ni HTTPS.
Lo que hace con cada mensaje:

- PDF adjunto   → lo guarda en `datos/entrada/<file_unique_id>/` y corre la ingesta (tool 1); avisa al terminar.
- texto         → `preguntar` (tool 2): respuesta con cita verificada o derivación.
- /expensas     → `consultar_expensas` por SQL (nunca inventa importes).
- /start /ayuda → instrucciones y el id del chat (para la lista blanca).

Cada número o grupo tiene su propia base (consorcio): un chat nuevo arranca con cero documentos,
deriva todo, y "aprende" solo lo que se le sube. Varios números pueden compartir un consorcio con
`consorcio consorcios --asignar-chat ID --a CONSORCIO`. Además, `TELEGRAM_CHATS_PERMITIDOS` limita
quién puede hablarle (vacío = cualquiera, solo para probar).

En grupos, Telegram no entrega los mensajes comunes a un bot con "privacy mode" (el default de
BotFather): o se desactiva (/setprivacy) o se usan `/preguntar ...` y el PDF se manda como respuesta
al bot.

Las funciones puras (autorización, parseo de /expensas, formateo) no tocan la red y se testean
sin bot; los handlers asíncronos solo las llaman. La ingesta y la consulta corren en un hilo
aparte (`asyncio.to_thread`) para no bloquear el polling, y de a una por vez (Ollama cloud
gratuito admite un pedido a la vez).
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path

from ..config import config
from .formato import (  # noqa: F401  (reexportados: test_telegram los importa de acá)
    LIMITE_MENSAJE,
    _pesos,
    _recortar,
    formatear_documentos,
    formatear_expensas,
    formatear_ingesta,
    formatear_respuesta,
)

log = logging.getLogger("consorcio.telegram")

MAX_MB_PDF = 20  # tope de descarga de la Bot API

AYUDA = (
    "Soy el asistente del consorcio (prueba de concepto).\n\n"
    "• Mandame un PDF (reglamento, acta, liquidación) y lo cargo a la base de este chat.\n"
    "• Escribime una pregunta (o /preguntar ...) y te respondo citando el documento, o la derivo a la administración.\n"
    "• /expensas [resumen|rubros|gastos|unidades] [AAAA-MM] [unidad] [texto]\n"
    "  p. ej. /expensas rubros 2026-07   ·   /expensas unidades 2026-07 5-D   ·   /expensas gastos limpieza\n"
    "• /documentos: qué hay cargado en la base de este chat.\n"
)


# ------------------------------------------------------------------ funciones puras


def chats_permitidos(valor: str | None = None) -> set[int]:
    """Parsea TELEGRAM_CHATS_PERMITIDOS ("123, -456"); vacío = sin restricción."""
    crudo = config.telegram_chats_permitidos if valor is None else valor
    return {int(x) for x in re.split(r"[,\s]+", crudo.strip()) if x and re.fullmatch(r"-?\d+", x)}


def chat_autorizado(chat_id: int, permitidos: set[int]) -> bool:
    return not permitidos or chat_id in permitidos


def parsear_expensas(args: list[str]) -> dict:
    """`/expensas rubros 2026-07 5-D limpieza` → que, periodo, unidad, texto (en cualquier orden)."""
    res: dict = {"que": "resumen", "periodo": None, "unidad": None, "texto": None}
    for a in args:
        a = a.strip()
        if a.lower() in ("resumen", "rubros", "gastos", "unidades"):
            res["que"] = a.lower()
        elif re.fullmatch(r"\d{4}-\d{2}", a):
            res["periodo"] = a
        elif re.fullmatch(r"(\d+-[A-Za-z]|COCH-\d+|\d+)", a, re.IGNORECASE) and res["unidad"] is None:
            res["unidad"] = a.upper()
        else:
            res["texto"] = f"{res['texto']} {a}".strip() if res["texto"] else a
    if res["unidad"] and res["que"] == "resumen":
        res["que"] = "unidades"
    if res["texto"] and res["que"] == "resumen":
        res["que"] = "gastos"
    return res


def ruta_entrada(dir_entrada: Path, file_unique_id: str, nombre: str) -> Path:
    """Dónde guardar un PDF recibido: `<dir>/<file_unique_id>/<nombre>`. Cada archivo tiene su carpeta,
    así dos PDF homónimos (de un mismo chat o de otros) no se pisan mientras esperan su turno, y se
    conserva el nombre original, que es el `nombre_archivo` del documento."""
    carpeta = re.sub(r"[^A-Za-z0-9_-]", "_", file_unique_id) or "archivo"
    limpio = Path(nombre.replace("\\", "/")).name
    if limpio in ("", ".", ".."):
        limpio = f"{carpeta}.pdf"
    ruta = Path(dir_entrada) / carpeta / limpio
    ruta.parent.mkdir(parents=True, exist_ok=True)
    return ruta


# ------------------------------------------------------------------ trabajo sincrónico (en hilo)


def _base(chat_id: int, descripcion: str | None = None) -> str:
    from ..almacen.postgres import consorcio_de_chat

    return consorcio_de_chat(chat_id, descripcion)


def procesar_pdf(ruta: Path, chat_id: int) -> str:
    from ..ingesta.grafo import ingestar_pdf

    return formatear_ingesta(ingestar_pdf(str(ruta), consorcio_id=_base(chat_id)))


def responder_pregunta(texto: str, chat_id: int) -> str:
    from ..consulta.grafo import preguntar

    cid = _base(chat_id)
    log.info("pregunta de chat %s (%s): %s", chat_id, cid, texto[:120])
    final = preguntar(texto, origen="telegram", consorcio_id=cid)
    log.info("respuesta a chat %s: %s · %s · %s s", chat_id,
             "derivada" if final.get("derivada") else "respondida", final.get("motivo"), final.get("segundos"))
    return formatear_respuesta(final)


def consultar(args: list[str], chat_id: int) -> str:
    from ..almacen.postgres import consultar_expensas

    p = parsear_expensas(args)
    log.info("expensas de chat %s: %s", chat_id, p)
    filas = consultar_expensas(p["que"], p["periodo"], p["unidad"], p["texto"], consorcio_id=_base(chat_id))
    return formatear_expensas(p["que"], filas)


def listar(chat_id: int) -> str:
    from ..almacen.postgres import listar_documentos

    return formatear_documentos(listar_documentos(_base(chat_id)))


def bienvenida(chat_id: int, descripcion: str | None) -> str:
    from ..almacen.postgres import listar_documentos

    cid = _base(chat_id, descripcion)
    n = len(listar_documentos(cid))
    return (f"{AYUDA}\nEste chat (id {chat_id}) usa la base «{cid}», que tiene {n} documento{'s' if n != 1 else ''}. "
            "Lo que subas acá queda solo en esta base.")


# ------------------------------------------------------------------ bot


def correr_bot() -> int:
    """Arranca el bot y bloquea hasta Ctrl+C. Devuelve 1 si falta el token."""
    if not config.telegram_bot_token:
        log.error("falta TELEGRAM_BOT_TOKEN en .env")
        return 1
    from telegram import Update
    from telegram.constants import ChatAction
    from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

    permitidos = chats_permitidos()
    if not permitidos:
        log.warning("TELEGRAM_CHATS_PERMITIDOS vacío: el bot responde a cualquier chat (solo para probar)")
    candado = asyncio.Lock()  # una tool por vez: Ollama gratuito admite un pedido a la vez
    dir_entrada = config.ruta_entrada()
    dir_entrada.mkdir(parents=True, exist_ok=True)

    async def autorizado(update: Update) -> bool:
        chat = update.effective_chat
        if chat is None:
            return False
        if chat_autorizado(chat.id, permitidos):
            return True
        log.warning("chat no autorizado: %s (%s)", chat.id, chat.effective_name)
        await update.effective_message.reply_text(
            f"Este chat (id {chat.id}) no está autorizado. Hay que agregarlo a TELEGRAM_CHATS_PERMITIDOS."
        )
        return False

    async def con_candado(update: Update, context: ContextTypes.DEFAULT_TYPE, aviso: str, trabajo, *args) -> None:
        msg = update.effective_message
        if candado.locked():
            await msg.reply_text("Estoy procesando otro pedido; sigo con el tuyo apenas termine.")
        async with candado:
            if aviso:
                await msg.reply_text(aviso)
            await context.bot.send_chat_action(chat_id=update.effective_chat.id, action=ChatAction.TYPING)
            try:
                respuesta = await asyncio.to_thread(trabajo, *args)
            except Exception as e:  # el bot no se cae por un PDF roto o un proveedor sin cuota
                log.exception("fallo en %s", getattr(trabajo, "__name__", trabajo))
                respuesta = f"No pude completar el pedido: {type(e).__name__}: {str(e)[:300]}"
        await msg.reply_text(respuesta)

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await autorizado(update):
            chat = update.effective_chat
            await con_candado(update, context, "", bienvenida, chat.id, chat.effective_name)

    async def documento(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await autorizado(update):
            return
        doc = update.effective_message.document
        nombre = doc.file_name or f"{doc.file_unique_id}.pdf"
        if not (nombre.lower().endswith(".pdf") or doc.mime_type == "application/pdf"):
            await update.effective_message.reply_text("Por ahora solo proceso PDF.")
            return
        if doc.file_size and doc.file_size > MAX_MB_PDF * 1024 * 1024:
            await update.effective_message.reply_text(f"El archivo supera los {MAX_MB_PDF} MB que acepta Telegram para bots.")
            return
        ruta = ruta_entrada(dir_entrada, doc.file_unique_id, nombre)
        archivo = await doc.get_file()
        await archivo.download_to_drive(ruta)
        kb = (doc.file_size or 0) // 1024
        log.info("PDF recibido de chat %s: %s (%s KB)", update.effective_chat.id, ruta, kb)
        await con_candado(
            update, context,
            f"Recibí {nombre} ({kb} KB). Lo clasifico y lo cargo; si es un escaneado puede tardar varios minutos. Te aviso.",
            procesar_pdf, ruta, update.effective_chat.id,
        )

    async def texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await autorizado(update):
            return
        pregunta = (update.effective_message.text or "").strip()
        if context.args is not None:  # vino como /preguntar ...
            pregunta = " ".join(context.args).strip()
        if len(pregunta) < 5:
            await update.effective_message.reply_text(AYUDA)
            return
        await con_candado(update, context, "", responder_pregunta, pregunta, update.effective_chat.id)

    async def expensas(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await autorizado(update):
            await con_candado(update, context, "", consultar, list(context.args or []), update.effective_chat.id)

    async def documentos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if await autorizado(update):
            await con_candado(update, context, "", listar, update.effective_chat.id)

    app = Application.builder().token(config.telegram_bot_token).build()
    app.add_handler(CommandHandler(["start", "ayuda", "help"], start))
    app.add_handler(CommandHandler("preguntar", texto))
    app.add_handler(CommandHandler("expensas", expensas))
    app.add_handler(CommandHandler("documentos", documentos))
    app.add_handler(MessageHandler(filters.Document.ALL, documento))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, texto))
    log.info("bot de Telegram escuchando (proveedor %s:%s, chats permitidos: %s)",
             config.llm_provider, config.modelo, sorted(permitidos) or "todos")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
    return 0
