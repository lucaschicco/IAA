"""Línea de comandos: `consorcio <subcomando>`.

  consorcio ingestar <pdf> [--metodo auto|texto|tesseract|vision] [--paginas 1-3] [--sin-persistir] [--reusar-texto] [--forzar]
                           [--proveedor google_genai --modelo gemini-2.5-flash --modelo-vision ...]
  consorcio clasificar <pdf>            # solo la decisión del clasificador, sin modelo
  consorcio evaluar-ocr <pdf> <ref.md>  # compara la transcripción contra una referencia
  consorcio preguntar "¿...?" [--k 3] [--busqueda tsvector|palabras] [--sin-persistir] [--json]
  consorcio expensas [resumen|rubros|gastos|unidades] [--periodo 2026-07] [--unidad 5-D] [--texto limpieza]
  consorcio evaluar-consultas <preguntas.csv> [--particion dev|test|todas] [--k 3] [--con-modelo]
                           (con modelo real sobre test, guarda docs/evaluaciones/<fecha>-consultas-test.json para el CRM)
  consorcio documentos [--desactivar X | --activar X]   # lista lo que hay en Postgres; X: id o parte del nombre
  consorcio consorcios [--asignar-chat ID --a CONSORCIO] [--crear ID --nombre "..."]   # bases y chats
  consorcio consultas [--consorcio ID] [--origen telegram|cli|evaluacion|web] [--ultimas 20]   # qué se preguntó y qué se contestó
  consorcio gasto                       # gasto del mes en modelos contra TOPE_USD_MENSUAL
  consorcio sembrar-demo                # consorcios y usuarios inventados para probar la web; sin modelo ni bucket
  consorcio migrar                      # migra una base anterior al 26/10 (ids sin consorcio)
  consorcio actualizar-base             # lleva la base a la última revisión de Alembic, venga de donde venga
  (ingestar, preguntar, expensas y evaluar-consultas aceptan --consorcio ID; por defecto CONSORCIO_POR_DEFECTO.
   documentos y consultas sin --consorcio listan todos; --activar/--desactivar usan CONSORCIO_POR_DEFECTO)
  consorcio dibujar-grafo [ingesta|consulta|agente]   # imprime el grafo de LangGraph en Mermaid
  consorcio bot                         # bot de Telegram (long polling); necesita TELEGRAM_BOT_TOKEN
"""
from __future__ import annotations

import argparse
import json
import re
import sys

from .observabilidad import configurar_logging


def main(argv: list[str] | None = None) -> int:
    configurar_logging()
    parser = argparse.ArgumentParser(prog="consorcio", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ingestar", help="procesa un PDF de punta a punta")
    p.add_argument("pdf")
    p.add_argument("--metodo", choices=["auto", "texto", "tesseract", "vision"], default="auto")
    p.add_argument("--paginas", help="rango de páginas a procesar, p. ej. 1-3,7 (por defecto todas)")
    p.add_argument("--sin-persistir", action="store_true", help="no escribe en Postgres")
    p.add_argument("--reusar-texto", action="store_true",
                   help="reutiliza las páginas OCR/visión guardadas de una corrida anterior del mismo archivo")
    p.add_argument("--forzar", action="store_true",
                   help="reprocesa un PDF ya cargado y reemplaza la liquidación del mismo período en otro PDF")
    _arg_consorcio(p)
    _args_modelo(p)

    p = sub.add_parser("clasificar", help="muestra la decisión del clasificador de calidad")
    p.add_argument("pdf")

    p = sub.add_parser("evaluar-ocr", help="mide la transcripción contra un .md de referencia")
    p.add_argument("pdf")
    p.add_argument("referencia")
    p.add_argument("--metodo", choices=["auto", "texto", "tesseract", "vision"], default="auto")
    p.add_argument("--paginas", help="rango de páginas, p. ej. 1-3")
    p.add_argument("--sin-transcripcion", action="store_true", help="no imprime el texto transcripto")
    _args_modelo(p)

    p = sub.add_parser("preguntar", help="responde una consulta con cita verificada (tool 2)")
    p.add_argument("pregunta")
    p.add_argument("--k", type=_positivo, default=3, help="secciones a recuperar")
    p.add_argument("--busqueda", choices=["tsvector", "palabras"], default="tsvector")
    p.add_argument("--sin-persistir", action="store_true", help="no guarda la consulta en Postgres")
    p.add_argument("--json", action="store_true", help="imprime el estado completo en JSON")
    _arg_consorcio(p)
    _args_modelo(p)

    p = sub.add_parser("expensas", help="consulta las liquidaciones por SQL, sin modelo")
    p.add_argument("que", nargs="?", choices=["resumen", "rubros", "gastos", "unidades"], default="resumen")
    p.add_argument("--periodo", type=_periodo, help="AAAA-MM")
    p.add_argument("--unidad", help="piso-depto (5-D, COCH-16) o número de UF")
    p.add_argument("--texto", help="filtra gastos por rubro, proveedor o concepto")
    _arg_consorcio(p)

    p = sub.add_parser("evaluar-consultas", help="recall@k del buscador y, opcionalmente, calidad de las respuestas")
    p.add_argument("csv")
    p.add_argument("--particion", choices=["dev", "test", "todas"], default="dev")
    p.add_argument("--k", type=_positivo, default=3)
    p.add_argument("--con-modelo", action="store_true", help="también responde cada pregunta (gasta tokens)")
    p.add_argument("--sin-detalle", action="store_true")
    _arg_consorcio(p)
    _args_modelo(p)

    p = sub.add_parser("documentos", help="lista los documentos guardados en Postgres")
    p.add_argument("--desactivar", metavar="ID_O_NOMBRE", help="el buscador deja de usar ese documento (no se borra)")
    p.add_argument("--activar", metavar="ID_O_NOMBRE", help="vuelve a incluirlo en el buscador")
    p.add_argument("--consorcio", help="solo ese consorcio; al listar, por defecto todos; con --activar/--desactivar, por defecto CONSORCIO_POR_DEFECTO")
    p = sub.add_parser("consorcios", help="lista las bases (consorcios) y qué chats usa cada una")
    p.add_argument("--asignar-chat", type=int, metavar="CHAT_ID", help="hace que ese chat de Telegram use --a")
    p.add_argument("--a", metavar="CONSORCIO", help="consorcio destino de --asignar-chat")
    p.add_argument("--crear", metavar="CONSORCIO", help="crea un consorcio (slug: letras, números y guiones)")
    p.add_argument("--nombre", help="nombre legible para --crear o --a")
    sub.add_parser("migrar", help="migra una base anterior al 26/10/2026 al esquema con consorcios")
    sub.add_parser("actualizar-base", help="lleva la base a la última revisión de Alembic (respaldar antes)")
    p = sub.add_parser("consultas", help="últimas consultas registradas (pregunta, respuesta, cita, canal)")
    p.add_argument("--consorcio", help="solo esa base (por defecto, todas)")
    p.add_argument("--origen", choices=["telegram", "cli", "evaluacion", "web"])
    p.add_argument("--ultimas", type=_positivo, default=20)
    sub.add_parser("gasto", help="gasto del mes en modelos contra el tope")
    sub.add_parser("sembrar-demo", help="carga consorcios y usuarios inventados para probar la web")
    p = sub.add_parser("dibujar-grafo", help="grafo de LangGraph en Mermaid")
    p.add_argument("cual", nargs="?", choices=["ingesta", "consulta", "agente"], default="ingesta")
    p = sub.add_parser("bot", help="bot de Telegram: recibe PDFs y preguntas por chat")
    _args_modelo(p)

    args = parser.parse_args(argv)
    _aplicar_modelo(args)
    _resolver_consorcio(args)
    if args.cmd in CON_CONSORCIO_EXISTENTE and getattr(args, "consorcio", None):
        from .almacen.postgres import existe_consorcio

        if not existe_consorcio(args.consorcio):  # un typo no se confunde con "sin datos"
            parser.exit(1, f"consorcio: error: no existe el consorcio {args.consorcio!r} "
                           "(ver `consorcio consorcios`)\n")

    if args.cmd == "ingestar":
        from .ingesta.grafo import ingestar_pdf

        metodo = None if args.metodo == "auto" else args.metodo
        final = ingestar_pdf(args.pdf, metodo=metodo, sin_persistir=args.sin_persistir, paginas=args.paginas,
                             reusar_texto=args.reusar_texto, consorcio_id=args.consorcio, forzar=args.forzar)
        _imprimir_resumen(final)
        return 1 if final.get("errores") or final.get("estado") in ("en_proceso", "periodo_duplicado", "tope_excedido") else 0

    if args.cmd == "clasificar":
        from .ingesta.clasificador import clasificar_pdf, herramientas_disponibles

        c = clasificar_pdf(args.pdf)
        print(json.dumps({"herramientas": herramientas_disponibles(), **c.como_dict()}, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "evaluar-ocr":
        from .ingesta.evaluar import evaluar_transcripcion

        metodo = None if args.metodo == "auto" else args.metodo
        res = evaluar_transcripcion(args.pdf, args.referencia, metodo, args.paginas)
        if args.sin_transcripcion:
            res.pop("transcripcion", None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "preguntar":
        from .consulta.grafo import preguntar

        final = preguntar(args.pregunta, k=args.k, metodo_busqueda=args.busqueda, sin_persistir=args.sin_persistir,
                          consorcio_id=args.consorcio)
        if args.json:
            print(json.dumps({k: v for k, v in final.items() if not k.startswith("_")}, ensure_ascii=False, indent=2))
        else:
            print(final.get("mensaje", ""))
            print()
            print(f"[{'derivada' if final.get('derivada') else 'respondida'} · {final.get('motivo')} · "
                  f"{len(final.get('resultados', []))} secciones recuperadas · "
                  f"{json.dumps(final.get('costo'), ensure_ascii=False)} · {final.get('segundos')} s]")
        return 0

    if args.cmd == "expensas":
        from .almacen.postgres import consultar_expensas

        for fila in consultar_expensas(args.que, args.periodo, args.unidad, args.texto, consorcio_id=args.consorcio):
            print(json.dumps(fila, ensure_ascii=False))
        return 0

    if args.cmd == "evaluar-consultas":
        from .consulta.evaluar import evaluar_consultas, guardar_evaluacion

        res = evaluar_consultas(args.csv, args.particion, args.k, args.con_modelo, consorcio_id=args.consorcio)
        ruta = guardar_evaluacion(res)
        if ruta:
            print(f"guardada para el CRM: {ruta}", file=sys.stderr)  # stdout queda como JSON puro
        if args.sin_detalle:
            res["retrieval"].pop("detalle", None)
            if "generacion" in res:
                res["generacion"].pop("detalle", None)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "documentos":
        from .almacen.postgres import (
            activar_documento,
            listar_documentos,
            listar_documentos_de_todos_los_consorcios,
        )

        if args.desactivar or args.activar:
            cambiados = activar_documento(args.desactivar or args.activar, activo=bool(args.activar),
                                          consorcio_id=args.consorcio)
            print(f"{'activados' if args.activar else 'desactivados'}: {cambiados}")
            return 0 if cambiados else 1
        filas = listar_documentos(args.consorcio) if args.consorcio else listar_documentos_de_todos_los_consorcios()
        for d in filas:
            print(json.dumps(d, ensure_ascii=False))
        return 0

    if args.cmd == "consorcios":
        from .almacen.postgres import asegurar_consorcio, asignar_chat, listar_consorcios

        if args.crear:
            asegurar_consorcio(args.crear, args.nombre)
        if args.asignar_chat is not None:
            if not args.a:
                parser.error("--asignar-chat necesita --a CONSORCIO")
            asignar_chat(args.asignar_chat, args.a, args.nombre)
        for c in listar_consorcios():
            print(json.dumps(c, ensure_ascii=False))
        return 0

    if args.cmd == "consultas":
        from .almacen.postgres import listar_consultas, listar_consultas_de_todos_los_consorcios

        if args.consorcio:
            filas = listar_consultas(args.consorcio, args.origen, args.ultimas)
        else:
            filas = listar_consultas_de_todos_los_consorcios(args.origen, args.ultimas)
        for c in reversed(filas):
            print(json.dumps(c, ensure_ascii=False))
        return 0

    if args.cmd == "gasto":
        from .tope import estado_tope

        print(json.dumps(estado_tope(), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "sembrar-demo":
        from .agente.semilla import sembrar_demo

        print(json.dumps(sembrar_demo(), ensure_ascii=False))
        return 0

    if args.cmd == "migrar":
        from .almacen import postgres

        cambios = postgres.migrar_consorcios(_cfg().consorcio_por_defecto)
        print(json.dumps({"documentos_migrados": len(cambios), "cambios": cambios},
                         ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "actualizar-base":
        from .almacen import postgres

        marca = postgres.actualizar_esquema()
        print(json.dumps({"marcada_en": marca, "revision": postgres._revision_head()}, ensure_ascii=False))
        return 0

    if args.cmd == "bot":
        from .canales.telegram import correr_bot

        return correr_bot()

    if args.cmd == "dibujar-grafo":
        if args.cual == "consulta":
            from .consulta.grafo import construir_grafo
        elif args.cual == "agente":
            from .agente.grafo import construir_grafo
        else:
            from .ingesta.grafo import construir_grafo

        print(construir_grafo().get_graph().draw_mermaid())
        return 0

    parser.print_help()
    return 2


# Los que leen o cambian datos de un consorcio que ya tiene que existir. `ingestar` no: crea la base si es nueva;
# `evaluar-consultas` queda como estaba (la auditoría no la marcó y su corrida se reemplaza entera en los tests).
CON_CONSORCIO_EXISTENTE = {"preguntar", "expensas", "documentos", "consultas"}


def _positivo(valor: str) -> int:
    try:
        numero = int(valor)
    except ValueError:
        numero = 0
    if not 0 < numero <= 2**31 - 1:  # más grande, el LIMIT de Postgres revienta
        raise argparse.ArgumentTypeError(f"tiene que ser un entero entre 1 y 2147483647: {valor!r}")
    return numero


def _periodo(valor: str) -> str:
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", valor):
        raise argparse.ArgumentTypeError(f"el período va como AAAA-MM (p. ej. 2026-07): {valor!r}")
    return valor


def _arg_consorcio(p) -> None:
    p.add_argument("--consorcio", metavar="ID", help="base (consorcio) sobre la que opera; por defecto CONSORCIO_POR_DEFECTO")


def _resolver_consorcio(args) -> None:
    """El único lugar de la CLI que aplica `CONSORCIO_POR_DEFECTO`. Listar documentos o consultas sin
    --consorcio no pasa por acá: usa las funciones que listan todos los consorcios."""
    from .config import config

    cambia_documento = args.cmd == "documentos" and (args.desactivar or args.activar)
    if args.cmd in ("ingestar", "preguntar", "expensas", "evaluar-consultas") or cambia_documento:
        args.consorcio = args.consorcio or config.consorcio_por_defecto


def _args_modelo(p) -> None:
    p.add_argument("--proveedor", choices=["google_genai", "anthropic", "openai", "openrouter", "ollama", "fake"],
                   help="pisa LLM_PROVIDER para esta corrida")
    p.add_argument("--modelo", help="pisa LLM_MODEL para esta corrida")
    p.add_argument("--modelo-vision", help="pisa LLM_MODEL_VISION para esta corrida")


def _aplicar_modelo(args) -> None:
    from .config import config

    if getattr(args, "proveedor", None):
        config.llm_provider = args.proveedor
        if not getattr(args, "modelo", None):
            config.llm_model = ""  # usar el default del nuevo proveedor
    if getattr(args, "modelo", None):
        config.llm_model = args.modelo
    if getattr(args, "modelo_vision", None):
        config.llm_model_vision = args.modelo_vision


def _imprimir_resumen(final: dict) -> None:
    ext = final.get("extraccion", {})
    meta = ext.get("metadatos", {})
    liq = final.get("liquidacion")
    resumen = {
        "estado": final.get("estado"),
        "documento_existente": final.get("documento_existente"),
        "documento_id": final.get("documento_id"),
        "archivo": final.get("nombre_archivo"),
        "clasificacion": {k: v for k, v in final.get("clasificacion", {}).items() if k != "paginas"},
        "proveedor_modelo": f"{_cfg().llm_provider}:{_cfg().modelo}",
        "tipo_documento": meta.get("tipo_documento"),
        "titulo": meta.get("titulo"),
        "fecha_documento": meta.get("fecha_documento"),
        "entidades": {k: len(ext.get(k, [])) for k in
                      ("personas", "organizaciones", "direcciones", "eventos", "unidades", "montos", "secciones")},
        "liquidacion": (
            {"periodo": liq.get("periodo"), "vencimientos": [liq.get("primer_vencimiento"), liq.get("segundo_vencimiento")],
             "rubros": len(liq.get("rubros", [])), "gastos": len(liq.get("gastos", [])),
             "unidades": liq.get("filas_prorrateo"), "filas_no_parseadas": liq.get("filas_no_parseadas")}
            if liq else None
        ),
        "costo": final.get("costo"),
        "segundos_total": final.get("segundos_total"),
        "persistido": final.get("persistido"),
        "errores": final.get("errores", []),
    }
    print(json.dumps(resumen, ensure_ascii=False, indent=2))


def _cfg():
    from .config import config

    return config


if __name__ == "__main__":
    sys.exit(main())
