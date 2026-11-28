"""Prueba de punta a punta contra la web local, como lo haría un navegador (solo biblioteca estándar).

Uso, desde la raíz del repo y con la web levantada en E2E_BASE (por defecto http://127.0.0.1:8001):
    E2E_CLAVE=... python -u scripts/e2e_web.py <ronda> [--sin-carga]

1. Entra como administración, sube los PDFs de E2E_DOCS (todos juntos; si pasan el máximo de 10 la web responde
   422 y se mandan en lotes) y espera a que termine la cola de cargas. `--sin-carga` saltea este paso.
2. Hace cada pregunta de E2E_DOCS/evaluacion/preguntas.csv (columnas id, rol, pregunta, ...): rol `vecino`
   por el chat, cualquier otro por /consultar.
3. Escribe E2E_DOCS/evaluacion/resultados-<ronda>.json, para calificarlo contra criterios.md.

Los dos usuarios tienen que existir, tener membresía en E2E_CONSORCIO y compartir la contraseña E2E_CLAVE.
La carpeta por defecto (datos/entrada/doc-manzanares) está ignorada por git: tiene documentos reales.
"""
import csv
import html as h
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8001")
REPO = Path(__file__).resolve().parents[1]
DOCS = Path(os.environ.get("E2E_DOCS", REPO / "datos" / "entrada" / "doc-manzanares"))
EVAL = DOCS / "evaluacion"
CONSORCIO = os.environ.get("E2E_CONSORCIO", "manzanares")
ADMIN = os.environ.get("E2E_ADMIN", "admin-manzanares@test.com")
VECINA = os.environ.get("E2E_VECINO", "vecina-manzanares@test.com")
CLAVE = os.environ["E2E_CLAVE"]


class SinRedirigir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class LocalSegura(http.cookiejar.DefaultCookiePolicy):
    """Como un navegador: en 127.0.0.1 las cookies `Secure` viajan aunque sea http."""
    def return_ok_secure(self, cookie, request):
        return True


def cliente():
    jar = http.cookiejar.CookieJar(policy=LocalSegura())
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar), SinRedirigir())


def pedir(op, ruta, datos=None, cuerpo=None, tipo=None, timeout=600):
    if datos is not None:
        cuerpo, tipo = urllib.parse.urlencode(datos).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(BASE + ruta, data=cuerpo)
    if tipo:
        req.add_header("Content-Type", tipo)
    req.add_header("HX-Request", "true")
    try:
        r = op.open(req, timeout=timeout)
        return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")


def csrf(t):
    m = re.search(r'name="csrf" value="([^"]+)"', t)
    return m.group(1) if m else ""


def seguir(op, ruta, datos=None):
    s, hd, t = pedir(op, ruta, datos)
    for _ in range(5):
        if s not in (302, 303):
            break
        ruta = hd["location"].replace(BASE, "")
        s, hd, t = pedir(op, ruta)
    return s, ruta, t


def entrar(email):
    op = cliente()
    _, _, t = pedir(op, "/entrar")
    s, destino, t = seguir(op, "/entrar", {"email": email, "contrasena": CLAVE, "csrf": csrf(t)})
    if destino.startswith(("/consorcios", "/panel")):  # todavía sin consorcio elegido
        _, _, t = seguir(op, "/consorcios")
        s, destino, t = seguir(op, "/consorcios", {"consorcio_id": CONSORCIO, "csrf": csrf(t)})
    assert s == 200, (email, s, destino)
    return op, destino, t


def texto(fragmento):
    sin_tags = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", fragmento, flags=re.S)
    sin_tags = re.sub(r"<[^>]+>", " ", sin_tags)
    return re.sub(r"\s+", " ", h.unescape(sin_tags)).strip()


def multipart(campos, archivos):
    borde = uuid.uuid4().hex
    partes = []
    for k, v in campos.items():
        partes.append(f'--{borde}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for nombre, contenido in archivos:
        partes.append(f'--{borde}\r\nContent-Disposition: form-data; name="archivo"; filename="{nombre}"\r\n'
                      f'Content-Type: application/pdf\r\n\r\n'.encode() + contenido + b"\r\n")
    partes.append(f"--{borde}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={borde}"


def cargar(op, t_docs):
    pdfs = sorted(DOCS.glob("*.pdf"))
    archivos = [(p.name, p.read_bytes()) for p in pdfs]
    registro = {"archivos": [p.name for p in pdfs], "lotes": []}
    token = csrf(t_docs)
    # todos juntos: si superan el máximo, la web lo rechaza y se manda en lotes
    cuerpo, tipo = multipart({"csrf": token}, archivos)
    s, _, t = pedir(op, "/documentos", cuerpo=cuerpo, tipo=tipo)
    registro["todos_juntos"] = {"status": s, "archivos": len(archivos)}
    print(f"carga de {len(archivos)} juntos -> {s}")
    lotes = [archivos] if s == 200 else [archivos[:10], archivos[10:]]
    if s != 200:
        for lote in lotes:
            cuerpo, tipo = multipart({"csrf": token}, lote)
            t0 = time.time()
            s, _, t = pedir(op, "/documentos", cuerpo=cuerpo, tipo=tipo)
            registro["lotes"].append({"status": s, "archivos": len(lote), "segundos_respuesta": round(time.time() - t0, 1),
                                      "texto": texto(t)[:400]})
            print(f"lote de {len(lote)} -> {s} en {time.time() - t0:.1f}s")
    t0 = time.time()
    while True:
        s, _, t = pedir(op, "/documentos/cargas")
        if 'hx-get="/documentos/cargas"' not in t:
            break
        print(f"  pendientes... {time.time() - t0:.0f}s", flush=True)
        time.sleep(10)
    registro["segundos_hasta_terminar"] = round(time.time() - t0, 1)
    registro["estado_final"] = [texto(li) for li in re.findall(r'<li class="carga.*?</li>', t, re.S)]
    for linea in registro["estado_final"]:
        print("  ", linea[:160])
    return registro


def preguntar_vecina(op, t_chat, pregunta):
    t0 = time.time()
    s, _, t = pedir(op, "/chat/mensaje", {"mensaje": pregunta, "conversacion_id": "", "csrf": csrf(t_chat)})
    m = re.search(r'<div class="mensaje asistente">\s*<div class="texto">(.*?)</div>', t, re.S)
    return {"status": s, "segundos": round(time.time() - t0, 1), "respuesta": texto(m.group(1) if m else t)[:3000]}


def preguntar_admin(op, t_consultar, pregunta):
    t0 = time.time()
    s, _, t = pedir(op, "/consultar", {"pregunta": pregunta, "csrf": csrf(t_consultar)})
    m = re.search(r'<p class="resultado">(.*?)</p>', t, re.S)
    return {"status": s, "segundos": round(time.time() - t0, 1), "respuesta": texto(m.group(1) if m else t)[:3000]}


def main():
    ronda = sys.argv[1]
    resultado = {"ronda": ronda, "inicio": time.strftime("%Y-%m-%d %H:%M:%S")}
    admin, _, _ = entrar(ADMIN)
    if "--sin-carga" not in sys.argv:
        _, _, t_docs = seguir(admin, "/documentos")
        resultado["carga"] = cargar(admin, t_docs)
    preguntas = list(csv.DictReader(open(EVAL / "preguntas.csv", encoding="utf-8")))
    vecina, _, t_chat = entrar(VECINA)
    _, _, t_consultar = seguir(admin, "/consultar")
    resultado["respuestas"] = []
    for p in preguntas:
        r = preguntar_vecina(vecina, t_chat, p["pregunta"]) if p["rol"] == "vecino" else \
            preguntar_admin(admin, t_consultar, p["pregunta"])
        resultado["respuestas"].append({**p, **r})
        print(f"{p['id']} [{r['status']}, {r['segundos']}s] {r['respuesta'][:130]}", flush=True)
    resultado["fin"] = time.strftime("%Y-%m-%d %H:%M:%S")
    salida = EVAL / f"resultados-{ronda}.json"
    salida.write_text(json.dumps(resultado, ensure_ascii=False, indent=1), encoding="utf-8")
    print("guardado en", salida)


if __name__ == "__main__":
    main()
