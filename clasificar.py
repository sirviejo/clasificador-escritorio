#!/usr/bin/env python3
"""
clasificar.py — Clasificador del Escritorio con TypeSafe (Jev)

Para cada archivo decide:
  · categoría      → carpeta destino (las capturas SIEMPRE van a Capturas/;
                     eso lo decide el código, nunca el modelo)
  · subcarpeta     → dentro de la carpeta destino
  · importancia    → de descartable a "no se puede perder"
  · datos sensibles→ contraseñas, datos bancarios, documentos de identidad…
  · ¿se puede borrar? → reemplazable / temporal / duplicado exacto

Nunca borra nada: los candidatos a borrar se apartan en Para_Borrar/ para que
los revises. Cada corrida deja un informe CSV en informes/.

Uso:
    python3 clasificar.py                  → clasifica y mueve
    python3 clasificar.py --dry            → muestra qué haría, sin mover nada
    python3 clasificar.py --con-contenido  → lee el archivo (OCR de imágenes, texto
                                             de PDFs y documentos) y envía el inicio
                                             a la API. Sin esto solo se envía el
                                             nombre, y las capturas no se analizan.
    python3 clasificar.py --carpeta Capturas → analiza los archivos sueltos de esa
                                             carpeta en vez de los del Escritorio
    python3 clasificar.py --limite 20      → procesa solo los primeros 20 archivos
    python3 clasificar.py --solo-capturas  → solo mueve capturas (no usa la API)
    python3 clasificar.py --deshacer       → revierte la última corrida

Configuración: variables de entorno o archivo .env junto a este script
(ver .env.example). Solo TYPESAFE_API_KEY es obligatoria.
"""

import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# ─── Configuración ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
DESKTOP = SCRIPT_DIR.parent
CAPTURAS = "Capturas"
NEEDS_REVIEW = "Needs_Review"
PARA_BORRAR = "Para_Borrar"
LOG = SCRIPT_DIR / "movimientos.jsonl"
INFORMES = SCRIPT_DIR / "informes"
OCR_SRC = SCRIPT_DIR / "extraer_texto.swift"
OCR_BIN = SCRIPT_DIR / ".bin" / "extraer_texto"

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
BATCH_SIZE = 4           # archivos por request (todas sus preguntas van en paralelo)
MIN_AGE_SECONDS = 10     # no tocar archivos que se están escribiendo
CONTENT_CHARS = 1200     # cuánto texto del archivo ve el modelo

# Política (todo esto es código: se puede ajustar sin volver a consultar la API)
MIN_CONFIDENCE = 0.8     # categoría: por debajo de esto → Needs_Review
MIN_SUB_PROB = 0.5       # subcarpeta: es una preferencia inofensiva, alcanza con mayoría
IMPORTANTE_DESDE = 2.0   # importancia (0–3) a partir de la cual se marca "conservar"
SENSIBLE_DESDE = 0.7
BORRABLE_IMPORTANCIA_MAX = 1.0
BORRABLE_SENAL_MIN = 0.6  # prob. mínima de reemplazable o temporal (solo aparta, no borra)



def load_env():
    """Variables de entorno + archivo .env junto al script (que no se versiona)."""
    env = {}
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    for k in ("TYPESAFE_API_KEY", "PROPIETARIO", "IGNORAR"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


ENV = load_env()
ARGS = sys.argv[1:]
DRY_RUN = "--dry" in ARGS
SOLO_CAPTURAS = "--solo-capturas" in ARGS
CON_CONTENIDO = "--con-contenido" in ARGS

SCREENSHOT_PREFIXES = (
    "captura de pantalla", "grabación de pantalla", "grabacion de pantalla",
    "screenshot", "screen shot", "screen recording",
)
SCREENSHOT_XATTR = "com.apple.metadata:kMDItemIsScreenCapture"

# IGNORAR en .env: archivos o carpetas del Escritorio que el clasificador no toca
IGNORAR = {n.strip() for n in ENV.get("IGNORAR", "").split(",") if n.strip()}
SKIP_NAMES = {"desktop.ini"} | IGNORAR
SKIP_SUFFIXES = {".crdownload", ".download", ".part", ".tmp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".html", ".xml", ".py", ".js",
                 ".ts", ".sql", ".log", ".yaml", ".yml"}
OCR_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".gif", ".bmp",
                ".webp", ".pdf"}
TEXTUTIL_SUFFIXES = {".docx", ".doc", ".rtf", ".odt"}

# Carpetas destino. Las carpetas que ya existen en el Escritorio y no están
# acá se agregan solas como opciones (descritas por lo que contienen).
CATEGORIES = {
    "Finanzas_Facturacion": {
        "what": "Facturas, recibos, invoices, resúmenes de tarjeta o banco, gastos, impuestos.",
        "examples": ["INVOICE_0042_ACME.pdf", "recibo-hotel-viaje.pdf", "resumen de tarjeta visa.pdf"],
    },
    "Marketing_Reportes": {
        "what": "Reportes de campañas, SEO, Google Ads, HubSpot, analytics y exports de marketing.",
        "examples": ["auditoria-SEO.html", "channel_performance.csv", "campaign report Q2.pdf"],
    },
    "RRHH_Timesheets": {
        "what": "Onboarding, currículums, contratos de contractors, timesheets y horas del equipo.",
    },
    "Documentos": {
        "what": "Documentos de trabajo generales (Word, PDF, presentaciones, planillas) que no son de finanzas, marketing ni RRHH.",
    },
    "Imagenes": {
        "what": "Fotos, diseños, mockups, logos e imágenes generadas. Las capturas de pantalla NO van acá.",
        "examples": ["logo-final.png", "mockup-home.png", "pexels-photo-123.jpg"],
    },
    "Videos": {
        "what": "Videos, grabaciones de reuniones y subtítulos.",
    },
    "Codigo": {
        "what": "Código fuente, scripts, proyectos comprimidos de código, archivos de configuración.",
        "examples": ["mi-proyecto.zip", "deploy.sh", "schema.sql"],
    },
    "Instaladores": {
        "what": "Instaladores y aplicaciones: .dmg, .pkg, .app, .exe.",
    },
    "Personal": {
        "what": "Documentos personales: DNI, pasaporte, viajes, vacaciones, trámites, notas propias.",
    },
    "otro": {
        "what": "Ninguna carpeta encaja claramente o el nombre no alcanza para decidir.",
    },
}

# Subcarpetas por carpeta. Las subcarpetas que ya existen en disco se suman solas.
SUBFOLDERS = {
    CAPTURAS: {
        "Trabajo": "Herramientas de trabajo y de clientes: dashboards, CRM, reportes, anuncios, planillas, analytics, gestores de proyectos.",
        "Codigo_Errores": "Terminal, editor de código, logs, consolas de desarrollador, mensajes de error técnicos, paneles de hosting o deploy.",
        "Diseno_UI": "Interfaces, mockups, pantallas de apps o sitios web guardadas por su diseño o como referencia visual.",
        "Finanzas_Pagos": "Comprobantes de pago o transferencia, facturas, home banking, checkouts, precios y suscripciones.",
        "Conversaciones": "Chats, emails, Slack, WhatsApp, comentarios o mensajes entre personas.",
        "Mapas_Lugares": "Mapas, direcciones, radios de distancia, propiedades o lugares.",
        "Personal": "Asuntos personales: trámites, salud, viajes, compras propias, redes sociales.",
    },
    "Finanzas_Facturacion": {
        "Facturas_Emitidas": "Facturas o invoices que el usuario%s emite a sus clientes." % (
            " (%s)" % ENV["PROPIETARIO"] if ENV.get("PROPIETARIO") else ""),
        "Facturas_Recibidas": "Facturas o invoices de proveedores y servicios que el usuario tiene que pagar o pagó.",
        "Recibos_Gastos": "Recibos, tickets y comprobantes de gastos, viáticos y compras.",
        "Banco_Tarjetas": "Resúmenes y movimientos de banco o tarjeta, transferencias, wires.",
        "Impuestos": "Declaraciones, formularios y comprobantes impositivos.",
    },
    "Marketing_Reportes": {
        "SEO": "Auditorías, keywords, estrategia de SEO y visibilidad en buscadores o IA.",
        "Ads": "Google Ads, Meta Ads y otras campañas pagas: reportes, previews, presupuestos.",
        "CRM_Leads": "Exports de HubSpot u otro CRM, listas de leads, contactos y cuentas.",
        "Redes_Sociales": "Reportes y contenido de redes sociales.",
        "Analytics": "Reportes de tráfico, performance por canal y métricas web.",
    },
    "RRHH_Timesheets": {
        "CVs": "Currículums y perfiles de candidatos.",
        "Contratos": "Contratos y acuerdos con empleados o contractors.",
        "Timesheets": "Planillas de horas y reportes de tiempo.",
        "Onboarding": "Material de ingreso y documentación de alta.",
    },
    "Documentos": {
        "Propuestas_Contratos": "Propuestas comerciales, cotizaciones, contratos y acuerdos.",
        "Presentaciones": "Decks y presentaciones.",
        "Planillas": "Hojas de cálculo y datos tabulares de trabajo.",
        "Notas_Borradores": "Notas, borradores, minutas y textos sueltos.",
    },
    "Imagenes": {
        "Fotos": "Fotografías reales de personas, lugares u objetos.",
        "Disenos_Mockups": "Diseños, mockups, wireframes, diagramas y flujos.",
        "Logos_Marca": "Logos, íconos y material de marca.",
        "Generadas_IA": "Imágenes generadas con IA.",
        "Stock": "Imágenes de bancos de stock descargadas de la web.",
    },
    "Codigo": {
        "Proyectos": "Proyectos o repositorios completos, normalmente comprimidos.",
        "Scripts": "Scripts y archivos de código sueltos.",
        "Datos_Config": "Archivos de configuración, dumps, esquemas y datos para desarrollo.",
    },
    "Personal": {
        "Identidad": "DNI, pasaporte, licencias y otros documentos de identidad.",
        "Viajes": "Pasajes, reservas, itinerarios y seguros de viaje.",
        "Salud": "Estudios, recetas y documentación médica.",
        "Tramites": "Trámites, formularios oficiales, estatutos y gestiones.",
    },
}
SIN_SUBCARPETA = "ninguna"

IMPORTANCIA = {
    "instructions": "¿Qué tan grave sería para el usuario perder el archivo descrito en `archivos.%s`?",
    "criteria": [
        "Archivo descartable: instalador, descarga genérica, prueba, imagen de stock, export temporal o captura sin información útil.",
        "Material de referencia o de trabajo ya usado, que se podría volver a conseguir o rehacer sin mucho esfuerzo.",
        "Trabajo propio o de un cliente que costaría tiempo rehacer: diseños, reportes, documentos, código, presentaciones.",
        "Documento que no se puede perder: identidad, contratos, facturas e impuestos, comprobantes de pago, credenciales, registros legales o médicos.",
    ],
}
NOULS = {
    "reemplazable": {
        "instructions": "¿El archivo descrito en `archivos.%s` se puede volver a obtener fácilmente si se borra?",
        "criteria": {
            "true": "Instalador, descarga pública de la web, foto de stock, export que se regenera desde una herramienta, o comprimido de algo que existe en otro lado.",
            "false": "Documento propio, original o único; no hay indicio de que exista otra copia.",
        },
    },
    "temporal": {
        "instructions": "¿El archivo descrito en `archivos.%s` parece temporal, de prueba, un borrador descartado o una copia?",
        "criteria": {
            "true": "Nombres como test, prueba, tmp, untitled, copia, '(1)', old; o una captura de un momento puntual sin valor posterior (un error pasajero, un menú, una pantalla de carga).",
            "false": "Parece un archivo definitivo que alguien quiso guardar.",
        },
    },
    "sensible": {
        "instructions": "¿El archivo descrito en `archivos.%s` contiene o muy probablemente contiene datos sensibles?",
        "criteria": {
            "true": "Contraseñas, API keys o tokens, números de documento, datos bancarios o de tarjeta, saldos de cuentas, datos médicos o información personal de terceros.",
            "false": "No hay indicio de datos privados o confidenciales.",
        },
    },
}


# ─── Capturas: regla determinística ───────────────────────────────────────────

def is_screenshot(path):
    if path.name.lower().startswith(SCREENSHOT_PREFIXES):
        return True
    # macOS marca las capturas con un xattr que sobrevive a los renombres
    try:
        out = subprocess.run(["xattr", str(path)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return SCREENSHOT_XATTR in out.splitlines()


# ─── Duplicados exactos: también lo decide el código ──────────────────────────

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(files):
    """{archivo: original} para los archivos idénticos byte a byte a otro del Escritorio."""
    by_size = {}
    for root, dirs, names in os.walk(str(DESKTOP)):
        dirs[:] = [d for d in dirs if not d.startswith(".")
                   and Path(root, d) not in (SCRIPT_DIR, DESKTOP / PARA_BORRAR)]
        for n in names:
            if n.startswith("."):
                continue
            p = Path(root, n)
            try:
                by_size.setdefault(p.stat().st_size, []).append(p)
            except OSError:
                pass
    dupes, hashes, pending = {}, {}, set(files)
    for p in files:
        same_size = [q for q in by_size.get(p.stat().st_size, []) if q != p]
        if not same_size or p.stat().st_size == 0:
            continue
        for q in [p] + same_size:
            if q not in hashes:
                try:
                    hashes[q] = sha256(q)
                except OSError:
                    hashes[q] = None
        twins = [q for q in same_size if hashes[q] and hashes[q] == hashes[p]]
        # entre dos pendientes idénticos se conserva el primero por orden alfabético
        originals = [q for q in twins if q not in pending or str(q) < str(p)]
        if originals:
            dupes[p] = min(originals, key=str)
    return dupes


# ─── Estado que ve el modelo ──────────────────────────────────────────────────

def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return "%d %s" % (n, unit)
        n /= 1024.0
    return "%.1f TB" % n


def mdls(path, attr):
    try:
        out = subprocess.run(["mdls", "-raw", "-name", attr, str(path)],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return None if out in ("", "(null)") else " ".join(out.split())


def ocr_tool():
    """Compila extraer_texto.swift la primera vez. None si no hay Swift."""
    if OCR_BIN.exists() and OCR_BIN.stat().st_mtime >= OCR_SRC.stat().st_mtime:
        return OCR_BIN
    if not shutil.which("swiftc"):
        return None
    print("Compilando extraer_texto (solo la primera vez)…")
    OCR_BIN.parent.mkdir(exist_ok=True)
    done = subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print("  no se pudo compilar, sigo sin OCR: %s" % done.stderr.strip()[:200])
        return None
    return OCR_BIN


def ocr_chunk(tool, chunk):
    try:
        out = subprocess.run([str(tool), str(CONTENT_CHARS)] + [str(p) for p in chunk],
                             capture_output=True, text=True, timeout=300).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    texts = {}
    for line in out.splitlines():
        try:
            row = json.loads(line)
            texts[row["path"]] = row["text"]
        except ValueError:
            pass
    return texts


def extract_texts(files):
    """{archivo: inicio del contenido} para los tipos de archivo que se pueden leer."""
    texts = {}
    for p in files:
        suffix = p.suffix.lower()
        try:
            if suffix in TEXT_SUFFIXES:
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    texts[p] = fh.read(CONTENT_CHARS)
            elif suffix in TEXTUTIL_SUFFIXES:
                texts[p] = subprocess.run(
                    ["textutil", "-convert", "txt", "-stdout", str(p)],
                    capture_output=True, text=True, timeout=30).stdout[:CONTENT_CHARS]
        except (OSError, subprocess.SubprocessError):
            pass
    visual = [p for p in files if p.suffix.lower() in OCR_SUFFIXES]
    tool = ocr_tool() if visual else None
    if tool:
        chunks = [visual[i:i + 10] for i in range(0, len(visual), 10)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for found in pool.map(lambda c: ocr_chunk(tool, c), chunks):
                texts.update({Path(k): v for k, v in found.items()})
    return {p: t.strip() for p, t in texts.items() if t and t.strip()}


def describe(path, screenshot, text):
    stat = path.stat()
    info = {
        "nombre": path.name,
        "extension": path.suffix.lower(),
        "tipo": mdls(path, "kMDItemKind"),
        "es_captura_de_pantalla": screenshot,
        "tamaño": human_size(stat.st_size),
        "modificado": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        "descargado_de": mdls(path, "kMDItemWhereFroms"),
        "titulo": mdls(path, "kMDItemTitle"),
        "texto_del_archivo": text,
    }
    return {k: v for k, v in info.items() if v not in (None, "")}


def build_categories():
    criteria = dict(CATEGORIES)
    for d in sorted(DESKTOP.iterdir()):
        if (not d.is_dir() or d.name.startswith(".") or d.name in criteria
                or d.name in (CAPTURAS, NEEDS_REVIEW, PARA_BORRAR, SCRIPT_DIR.name) or d.name in IGNORAR):
            continue
        sample = [p.name for p in sorted(d.iterdir()) if not p.name.startswith(".")][:6]
        criteria[d.name] = {
            "what": "Carpeta existente del usuario llamada '%s'. Elegila solo si el archivo "
                    "pertenece claramente a ese cliente, proyecto o tema." % d.name,
            "contiene": sample,
        }
    criteria["otro"] = criteria.pop("otro")  # "otro" al final
    return criteria


def build_subfolders(categories):
    """{carpeta: opciones de subcarpeta}; solo carpetas con al menos una subcarpeta."""
    subs = {}
    for folder in [CAPTURAS] + [c for c in categories if c != "otro"]:
        options = dict(SUBFOLDERS.get(folder, {}))
        folder_dir = DESKTOP / folder
        if folder_dir.is_dir():
            for d in sorted(folder_dir.iterdir()):
                if d.is_dir() and not d.name.startswith(".") and d.name not in options:
                    sample = [p.name for p in sorted(d.iterdir()) if not p.name.startswith(".")][:5]
                    options[d.name] = {"what": "Subcarpeta existente '%s'." % d.name, "contiene": sample}
        if options:
            options[SIN_SUBCARPETA] = "Ninguna subcarpeta encaja claramente; el archivo queda en la raíz de la carpeta."
            subs[folder] = options
    return subs


# ─── TypeSafe ─────────────────────────────────────────────────────────────────

def load_api_key():
    return ENV.get("TYPESAFE_API_KEY") or None


def system_one(api_key, state, questions):
    body = json.dumps({"model": MODEL, "state": state, "questions": questions}).encode()
    for attempt in range(5):
        req = urllib.request.Request(API_URL, data=body, method="POST", headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read())["answers"]
        except urllib.error.HTTPError as e:
            if e.code in (429, 529, 500, 502, 503) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("TypeSafe HTTP %s: %s" % (e.code, e.read().decode(errors="replace")[:300]))
        except urllib.error.URLError as e:
            if attempt < 4:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("No se pudo conectar con TypeSafe: %s" % e.reason)


def judge_batch(api_key, categories, subfolders, batch):
    """Todas las preguntas de todos los archivos del lote en un solo request.

    Las preguntas de subcarpeta son especulativas: se pregunta una por cada
    carpeta posible y el código usa solo la de la carpeta que resulte elegida.
    """
    state = {"archivos": {}}
    questions = {}
    for i, item in enumerate(batch):
        fid = "f%d" % i
        state["archivos"][fid] = describe(item["path"], item["screenshot"], item["text"])
        if item["screenshot"]:
            sub_folders = [CAPTURAS]
        else:
            sub_folders = list(subfolders)
            questions[fid + ".categoria"] = {
                "type": "choice",
                "instructions": "¿En qué carpeta del Escritorio debería guardarse el archivo "
                                "descrito en `archivos.%s`? Decidí por su nombre, tipo, origen "
                                "y texto si está disponible." % fid,
                "criteria": categories,
            }
        for folder in sub_folders:
            if folder in subfolders:
                questions["%s.sub.%s" % (fid, folder)] = {
                    "type": "choice",
                    "instructions": "Suponiendo que el archivo descrito en `archivos.%s` se guarda en "
                                    "la carpeta '%s', ¿en cuál de sus subcarpetas va?" % (fid, folder),
                    "criteria": subfolders[folder],
                }
        questions[fid + ".importancia"] = {
            "type": "score",
            "instructions": IMPORTANCIA["instructions"] % fid,
            "criteria": IMPORTANCIA["criteria"],
        }
        for name, q in NOULS.items():
            questions["%s.%s" % (fid, name)] = {
                "type": "noul", "instructions": q["instructions"] % fid, "criteria": q["criteria"],
            }

    answers = system_one(api_key, state, questions)
    for i, item in enumerate(batch):
        fid = "f%d" % i
        item["answers"] = {k[len(fid) + 1:]: v for k, v in answers.items() if k.startswith(fid + ".")}
    return batch


# ─── Política: de juicios a decisión ──────────────────────────────────────────

def decide(item):
    """Completa item con carpeta, subcarpeta, veredicto y motivo."""
    a = item["answers"]
    item["importancia"] = a["importancia"]["score"]
    for name in NOULS:
        item[name] = a[name]["noul"]

    if item["screenshot"]:
        item["carpeta"], item["confianza"] = CAPTURAS, 1.0
    else:
        cat = a["categoria"]
        item["carpeta"], item["confianza"] = cat["choice"], cat["confidence"]
        if cat["choice"] == "otro" or cat["confidence"] < MIN_CONFIDENCE:
            top = sorted(cat["probabilities"].items(), key=lambda kv: -kv[1])[:2]
            item["motivo_carpeta"] = "dudoso: " + ", ".join("%s %.0f%%" % (k, v * 100) for k, v in top)
            item["carpeta"] = NEEDS_REVIEW

    sub = a.get("sub." + item["carpeta"])
    item["subcarpeta"] = ""
    if sub and sub["choice"] != SIN_SUBCARPETA and sub["probabilities"][sub["choice"]] >= MIN_SUB_PROB:
        item["subcarpeta"] = sub["choice"]

    senal = max(item["reemplazable"], item["temporal"])
    if item.get("duplicado_de"):
        item["veredicto"] = "borrar"
        item["motivo"] = "duplicado exacto de %s" % item["duplicado_de"].relative_to(DESKTOP)
    elif item["importancia"] >= IMPORTANTE_DESDE or item["sensible"] >= SENSIBLE_DESDE:
        item["veredicto"] = "conservar"
        reasons = []
        if item["importancia"] >= IMPORTANTE_DESDE:
            reasons.append("importante")
        if item["sensible"] >= SENSIBLE_DESDE:
            reasons.append("datos sensibles")
        item["motivo"] = " + ".join(reasons)
    elif item["importancia"] <= BORRABLE_IMPORTANCIA_MAX and senal >= BORRABLE_SENAL_MIN:
        item["veredicto"] = "borrar"
        item["motivo"] = "reemplazable" if item["reemplazable"] >= item["temporal"] else "temporal o de prueba"
    else:
        item["veredicto"] = "guardar"
        item["motivo"] = "sin motivo para borrar ni para destacar"
    return item


# ─── Mover / deshacer ─────────────────────────────────────────────────────────

def safe_dest(dest_dir, filename):
    dest = dest_dir / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while dest.exists():
        dest = dest_dir / ("%s (%d)%s" % (stem, counter, suffix))
        counter += 1
    return dest


def move(path, folder, run_id, note=""):
    dest_dir = DESKTOP / folder
    print("  %s%-36s ← %s%s" % ("[dry] " if DRY_RUN else "", folder + "/", path.name,
                                 "  (%s)" % note if note else ""))
    if DRY_RUN or path.parent == dest_dir:
        return
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = safe_dest(dest_dir, path.name)
    shutil.move(str(path), str(dest))
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run": run_id, "from": str(path), "to": str(dest),
                             "note": note}, ensure_ascii=False) + "\n")


def undo():
    entries = []
    if LOG.exists():
        entries = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not entries:
        print("No hay movimientos registrados.")
        return
    last = entries[-1]["run"]
    restored = 0
    for e in reversed([e for e in entries if e["run"] == last]):
        src, dst = Path(e["to"]), Path(e["from"])
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            restored += 1
        else:
            print("  no se pudo restaurar: %s" % src.name)
    remaining = [e for e in entries if e["run"] != last]
    LOG.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in remaining),
                   encoding="utf-8")
    print("Corrida %s revertida: %d archivos devueltos a su lugar." % (last, restored))


def write_report(items, run_id):
    INFORMES.mkdir(exist_ok=True)
    report = INFORMES / ("informe-%s%s.csv" % (run_id, "-dry" if DRY_RUN else ""))
    with open(report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["archivo", "veredicto", "motivo", "carpeta", "subcarpeta", "confianza_carpeta",
                    "importancia_0a3", "sensible", "reemplazable", "temporal"])
        for it in items:
            w.writerow([it["path"].name, it["veredicto"], it["motivo"], it["carpeta"], it["subcarpeta"],
                        "%.2f" % it["confianza"], "%.1f" % it["importancia"], "%.2f" % it["sensible"],
                        "%.2f" % it["reemplazable"], "%.2f" % it["temporal"]])
    return report


# ─── Main ─────────────────────────────────────────────────────────────────────

def source_dir():
    if "--carpeta" in ARGS:
        src = (DESKTOP / ARGS[ARGS.index("--carpeta") + 1]).resolve()
        if not src.is_dir() or DESKTOP not in src.parents:
            sys.exit("Error: --carpeta tiene que ser una carpeta dentro del Escritorio.")
        return src
    return DESKTOP


def pending_files(src):
    files = []
    for p in sorted(src.iterdir()):
        if (not p.is_file() or p.name.startswith((".", "~$")) or p.name in SKIP_NAMES
                or p.suffix.lower() in SKIP_SUFFIXES):
            continue
        files.append(p)
    return files


def main():
    if "--deshacer" in ARGS:
        return undo()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    src = source_dir()
    now = time.time()
    items = []
    for p in pending_files(src):
        shot = src == DESKTOP / CAPTURAS or is_screenshot(p)
        # las capturas aparecen ya completas; el resto puede estar descargándose
        if shot or now - p.stat().st_mtime >= MIN_AGE_SECONDS:
            items.append({"path": p, "screenshot": shot, "text": None})

    if "--limite" in ARGS:
        items = items[:int(ARGS[ARGS.index("--limite") + 1])]

    api_key = None if SOLO_CAPTURAS else load_api_key()
    if not SOLO_CAPTURAS and not api_key:
        print("Falta TYPESAFE_API_KEY (creala en https://console.typesafe.ai/ y guardala en\n"
              "%s como TYPESAFE_API_KEY=...). Solo muevo las capturas.\n" % (SCRIPT_DIR / ".env"))

    # Sin API, o capturas sin contenido (el nombre no dice nada): regla fija y listo
    direct, judged = [], []
    for it in items:
        if it["screenshot"] and (not api_key or not CON_CONTENIDO):
            direct.append(it)
        elif api_key:
            judged.append(it)
    if direct:
        print("Capturas: %d" % len(direct))
        for it in direct:
            move(it["path"], CAPTURAS, run_id, "captura")
    if not judged:
        return

    print("Analizando %d archivos…" % len(judged))
    if CON_CONTENIDO:
        texts = extract_texts([it["path"] for it in judged])
        for it in judged:
            it["text"] = texts.get(it["path"])
    dupes = find_duplicates([it["path"] for it in judged])
    for it in judged:
        it["duplicado_de"] = dupes.get(it["path"])

    categories = build_categories()
    subfolders = build_subfolders(categories)
    batches = [judged[i:i + BATCH_SIZE] for i in range(0, len(judged), BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda b: judge_batch(api_key, categories, subfolders, b), batches))

    for it in judged:
        decide(it)
        note = "%s: %s · importancia %.1f/3" % (it["veredicto"], it["motivo"], it["importancia"])
        if it.get("motivo_carpeta"):
            note += " · " + it["motivo_carpeta"]
        if it["veredicto"] == "borrar":
            dest = PARA_BORRAR
        else:
            dest = it["carpeta"] + ("/" + it["subcarpeta"] if it["subcarpeta"] else "")
        move(it["path"], dest, run_id, note)

    counts = {}
    for it in judged:
        counts[it["veredicto"]] = counts.get(it["veredicto"], 0) + 1
    print("\nImportantes (conservar): %d · Guardados sin más: %d · Se pueden borrar: %d "
          "(apartados en %s/, nada se borra solo)"
          % (counts.get("conservar", 0), counts.get("guardar", 0), counts.get("borrar", 0), PARA_BORRAR))
    print("Informe: %s" % write_report(judged, run_id))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit("Error: %s" % e)
