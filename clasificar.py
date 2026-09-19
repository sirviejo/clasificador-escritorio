#!/usr/bin/env python3
"""
Jev Tidy my Desktop — macOS Desktop file classifier built on TypeSafe's Jev

For every loose file it decides:
  · folder       → destination (screenshots ALWAYS go to the screenshots folder;
                   that rule is code, never the model)
  · subfolder    → inside the destination folder
  · importance   → from disposable to "cannot be lost"
  · sensitive    → passwords, banking data, identity documents…
  · deletable?   → replaceable / temporary / exact duplicate

It never deletes anything: deletion candidates are set aside in a "to delete"
folder for you to review. Every run writes a CSV report to informes/.

Folder names and messages come from locales/<lang>.json (en, es, fr, it).
Run with --help for the options. Configuration: environment variables or a
.env file next to this script (see .env.example).
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

SCRIPT_DIR = Path(__file__).parent.resolve()
DESKTOP = SCRIPT_DIR.parent
LOCALES = SCRIPT_DIR / "locales"
LOG = SCRIPT_DIR / "movimientos.jsonl"
REPORTS = SCRIPT_DIR / "informes"
OCR_SRC = SCRIPT_DIR / "extraer_texto.swift"
OCR_BIN = SCRIPT_DIR / ".bin" / "extraer_texto"

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
BATCH_SIZE = 4           # files per request (all their questions run in parallel)
MIN_AGE_SECONDS = 10     # leave files that are still being written alone
CONTENT_CHARS = 1200     # how much of the file's text the model sees

# Policy (plain code: tune it without calling the API again)
MIN_CONFIDENCE = 0.8     # folder: below this → review folder
MIN_SUB_PROB = 0.5       # subfolder: a harmless preference, a majority is enough
IMPORTANT_FROM = 2.0     # importance (0–3) from which a file is flagged "keep"
SENSITIVE_FROM = 0.7
DELETABLE_MAX_IMPORTANCE = 1.0
DELETABLE_MIN_SIGNAL = 0.6  # min prob. of replaceable or temporary (sets aside, never deletes)

# macOS names screenshots in the system language; the xattr catches the rest
SCREENSHOT_PREFIXES = (
    "screenshot", "screen shot", "screen recording",
    "captura de pantalla", "grabación de pantalla", "grabacion de pantalla",
    "capture d’écran", "capture d'écran", "enregistrement de l’écran", "enregistrement de l'écran",
    "schermata", "registrazione schermo",
)
SCREENSHOT_XATTR = "com.apple.metadata:kMDItemIsScreenCapture"

SKIP_SUFFIXES = {".crdownload", ".download", ".part", ".tmp"}
TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".html", ".xml", ".py", ".js",
                 ".ts", ".sql", ".log", ".yaml", ".yml"}
OCR_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".gif", ".bmp",
                ".webp", ".pdf"}
TEXTUTIL_SUFFIXES = {".docx", ".doc", ".rtf", ".odt"}

# Everything the model reads is English and keyed by internal ids, so judgments
# do not depend on the user's language. locales/*.json maps ids → folder names.
SCREENSHOTS, REVIEW, TO_DELETE, OTHER, NO_SUBFOLDER = "screenshots", "review", "to_delete", "other", "none"
DIR_PREFIX = "dir:"      # option key for a folder that already exists on disk

CATEGORIES = {
    "finance": {
        "what": "Invoices, receipts, bank or card statements, expenses, taxes.",
        "examples": ["INVOICE_0042_ACME.pdf", "hotel-receipt-trip.pdf", "resumen de tarjeta visa.pdf"],
    },
    "marketing": {
        "what": "Campaign reports, SEO, Google Ads, CRM exports, analytics and other marketing material.",
        "examples": ["SEO-audit.html", "channel_performance.csv", "campaign report Q2.pdf"],
    },
    "hr": {
        "what": "Onboarding, resumes, contractor agreements, timesheets and team hours.",
    },
    "documents": {
        "what": "General work documents (Word, PDF, slides, spreadsheets) that are not finance, marketing or HR.",
    },
    "images": {
        "what": "Photos, designs, mockups, logos and generated images. Screenshots do NOT go here.",
        "examples": ["logo-final.png", "mockup-home.png", "pexels-photo-123.jpg"],
    },
    "videos": {
        "what": "Videos, meeting recordings and subtitles.",
    },
    "code": {
        "what": "Source code, scripts, zipped code projects, configuration files.",
        "examples": ["my-project.zip", "deploy.sh", "schema.sql"],
    },
    "installers": {
        "what": "Installers and applications: .dmg, .pkg, .app, .exe.",
    },
    "personal": {
        "what": "Personal documents: ID, passport, travel, holidays, paperwork, own notes.",
    },
    OTHER: {
        "what": "No folder clearly fits, or the available information is not enough to decide.",
    },
}

SUBFOLDERS = {
    SCREENSHOTS: {
        "work": "Work and client tools: dashboards, CRM, reports, ads, spreadsheets, analytics, project managers.",
        "code_errors": "Terminal, code editor, logs, developer consoles, technical error messages, hosting or deploy panels.",
        "design_ui": "Interfaces, mockups, app or website screens saved for their design or as visual reference.",
        "finance_payments": "Payment or transfer confirmations, invoices, online banking, checkouts, prices and subscriptions.",
        "conversations": "Chats, emails, Slack, WhatsApp, comments or messages between people.",
        "maps_places": "Maps, directions, distance radiuses, properties or places.",
        "personal": "Personal matters: paperwork, health, travel, own purchases, social media.",
    },
    "finance": {
        "invoices_issued": "Invoices the user{owner} issues to their clients.",
        "invoices_received": "Invoices from vendors and services that the user has to pay or has paid.",
        "receipts_expenses": "Receipts, tickets and proof of expenses, travel costs and purchases.",
        "bank_cards": "Bank or card statements and transactions, transfers, wires.",
        "taxes": "Tax returns, forms and proof of tax payments.",
    },
    "marketing": {
        "seo": "Audits, keywords, SEO strategy and visibility in search engines or AI.",
        "ads": "Google Ads, Meta Ads and other paid campaigns: reports, previews, budgets.",
        "crm_leads": "HubSpot or other CRM exports, lead lists, contacts and accounts.",
        "social_media": "Social media reports and content.",
        "analytics": "Traffic reports, channel performance and web metrics.",
    },
    "hr": {
        "resumes": "Resumes and candidate profiles.",
        "contracts": "Contracts and agreements with employees or contractors.",
        "timesheets": "Timesheets and time reports.",
        "onboarding": "Onboarding material and new-hire paperwork.",
    },
    "documents": {
        "proposals_contracts": "Business proposals, quotes, contracts and agreements.",
        "presentations": "Decks and presentations.",
        "spreadsheets": "Spreadsheets and tabular work data.",
        "notes_drafts": "Notes, drafts, meeting minutes and loose text.",
    },
    "images": {
        "photos": "Real photographs of people, places or objects.",
        "designs_mockups": "Designs, mockups, wireframes, diagrams and flows.",
        "logos_brand": "Logos, icons and brand material.",
        "ai_generated": "AI-generated images.",
        "stock": "Stock images downloaded from the web.",
    },
    "code": {
        "projects": "Whole projects or repositories, usually zipped.",
        "scripts": "Scripts and loose code files.",
        "data_config": "Configuration files, dumps, schemas and development data.",
    },
    "personal": {
        "identity": "ID cards, passports, licenses and other identity documents.",
        "travel": "Tickets, bookings, itineraries and travel insurance.",
        "health": "Medical tests, prescriptions and health records.",
        "paperwork": "Paperwork, official forms, bylaws and errands.",
    },
}

IMPORTANCE = {
    "instructions": "How bad would it be for the user to lose the file described in `files.%s`?",
    "criteria": [
        "Disposable file: installer, generic download, test, stock image, temporary export or a screenshot with no useful information.",
        "Reference or already-used working material that could be obtained again or redone without much effort.",
        "Own or client work that would take time to redo: designs, reports, documents, code, presentations.",
        "Document that cannot be lost: identity, contracts, invoices and taxes, proof of payment, credentials, legal or medical records.",
    ],
}
NOULS = {
    "replaceable": {
        "instructions": "Can the file described in `files.%s` easily be obtained again if it is deleted?",
        "criteria": {
            "true": "Installer, public download from the web, stock photo, export that can be regenerated from a tool, or an archive of something that exists elsewhere.",
            "false": "Own, original or unique document; nothing suggests another copy exists.",
        },
    },
    "temporary": {
        "instructions": "Does the file described in `files.%s` look temporary, a test, a discarded draft or a copy?",
        "criteria": {
            "true": "Names like test, prueba, tmp, untitled, copy, copia, '(1)', old; or a screenshot of a passing moment with no later value (a transient error, a menu, a loading screen).",
            "false": "Looks like a final file that someone meant to keep.",
        },
    },
    "sensitive": {
        "instructions": "Does the file described in `files.%s` contain, or very likely contain, sensitive data?",
        "criteria": {
            "true": "Passwords, API keys or tokens, ID numbers, bank or card details, account balances, medical data or personal information about third parties.",
            "false": "No sign of private or confidential data.",
        },
    },
}


# ─── Settings: .env, language, arguments ──────────────────────────────────────

def load_env():
    """Environment variables + the .env file next to the script (never committed)."""
    env = {}
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    # Spanish names from the first version keep working
    for old, new in (("PROPIETARIO", "OWNER"), ("IGNORAR", "IGNORE")):
        if old in env:
            env.setdefault(new, env[old])
    for k in ("TYPESAFE_API_KEY", "OWNER", "IGNORE", "LANGUAGE", "SCREENSHOTS_DIR",
              "REVIEW_DIR", "TO_DELETE_DIR"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def system_language():
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                             capture_output=True, text=True, timeout=5).stdout
        for token in out.replace('"', " ").replace(",", " ").split():
            if len(token) >= 2 and token[:2].isalpha():
                return token[:2].lower()
    except (OSError, subprocess.SubprocessError):
        pass
    return (os.environ.get("LANG") or "en")[:2].lower()


def parse_args():
    ap = argparse.ArgumentParser(description="Jev Tidy my Desktop — macOS Desktop file classifier built on TypeSafe.")
    ap.add_argument("--dry", action="store_true", help="show what would happen without moving anything")
    ap.add_argument("--with-content", "--con-contenido", action="store_true",
                    help="read each file (OCR for images, text of PDFs and documents) and send the "
                         "first characters to the API; without it only metadata is sent and "
                         "screenshots are not analyzed")
    ap.add_argument("--folder", "--carpeta", metavar="DIR",
                    help="analyze the loose files of this Desktop folder instead of the Desktop itself")
    ap.add_argument("--limit", "--limite", type=int, metavar="N", help="process only the first N files")
    ap.add_argument("--screenshots-only", "--solo-capturas", action="store_true",
                    help="only move screenshots; never calls the API")
    ap.add_argument("--screenshots-dir", "--capturas", metavar="DIR",
                    help="where screenshots go: a folder name inside the Desktop or any path "
                         "(e.g. ~/Pictures/Screenshots). Overrides SCREENSHOTS_DIR and the locale name")
    ap.add_argument("--lang", metavar="CODE", help="language of folder names and messages: "
                    + ", ".join(sorted(p.stem for p in LOCALES.glob("*.json"))) + " (default: system language)")
    ap.add_argument("--undo", "--deshacer", action="store_true", help="revert the last run")
    return ap.parse_args()


ENV = load_env()
ARGS = parse_args()

LANG = (ARGS.lang or ENV.get("LANGUAGE") or system_language()).lower()
if not (LOCALES / (LANG + ".json")).exists():
    if ARGS.lang:
        sys.exit("No locale '%s' in %s" % (LANG, LOCALES))
    LANG = "en"
L = json.loads((LOCALES / (LANG + ".json")).read_text(encoding="utf-8"))
FOLDER_NAMES = dict(L["folders"])
for fid, key in ((SCREENSHOTS, "SCREENSHOTS_DIR"), (REVIEW, "REVIEW_DIR"), (TO_DELETE, "TO_DELETE_DIR")):
    if ENV.get(key):
        FOLDER_NAMES[fid] = ENV[key]
if ARGS.screenshots_dir:
    FOLDER_NAMES[SCREENSHOTS] = ARGS.screenshots_dir

IGNORE = {n.strip() for n in ENV.get("IGNORE", "").split(",") if n.strip()}
SKIP_NAMES = {"desktop.ini"} | IGNORE


def msg(key, *args):
    text = L["messages"][key]
    return text % args if args else text


def folder_path(fid):
    """Destination directory for a folder id or a 'dir:<name>' option. Absolute or ~ paths are honored."""
    name = fid[len(DIR_PREFIX):] if fid.startswith(DIR_PREFIX) else FOLDER_NAMES[fid]
    return DESKTOP / Path(name).expanduser()


def subfolder_name(fid, sub):
    if sub.startswith(DIR_PREFIX):
        return sub[len(DIR_PREFIX):]
    return L["subfolders"][fid][sub]


def display(path):
    try:
        return str(path.relative_to(DESKTOP))
    except ValueError:
        return str(path)


# ─── Screenshots: deterministic rule ──────────────────────────────────────────

def is_screenshot(path):
    # the filesystem may hand back accents decomposed (NFD)
    if unicodedata.normalize("NFC", path.name).lower().startswith(SCREENSHOT_PREFIXES):
        return True
    # macOS tags screenshots with an xattr that survives renames
    try:
        out = subprocess.run(["xattr", str(path)], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return SCREENSHOT_XATTR in out.splitlines()


# ─── Exact duplicates: also decided by code ───────────────────────────────────

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(files, roots):
    """{file: original} for files that are byte-identical to another one under roots."""
    by_size = {}
    skip = (SCRIPT_DIR, folder_path(TO_DELETE))
    for top in roots:
        for root, dirs, names in os.walk(str(top)):
            dirs[:] = [d for d in dirs if not d.startswith(".") and Path(root, d) not in skip]
            for n in names:
                if n.startswith("."):
                    continue
                p = Path(root, n)
                try:
                    by_size.setdefault(p.stat().st_size, set()).add(p)
                except OSError:
                    pass
    dupes, hashes, pending = {}, {}, set(files)
    for p in files:
        same_size = [q for q in by_size.get(p.stat().st_size, ()) if q != p]
        if not same_size or p.stat().st_size == 0:
            continue
        for q in [p] + same_size:
            if q not in hashes:
                try:
                    hashes[q] = sha256(q)
                except OSError:
                    hashes[q] = None
        twins = [q for q in same_size if hashes[q] and hashes[q] == hashes[p]]
        # of two identical pending files, the alphabetically first one is kept
        originals = [q for q in twins if q not in pending or str(q) < str(p)]
        if originals:
            dupes[p] = min(originals, key=str)
    return dupes


# ─── State the model sees ─────────────────────────────────────────────────────

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
    """Compiles extraer_texto.swift on first use. None when Swift is not available."""
    if OCR_BIN.exists() and OCR_BIN.stat().st_mtime >= OCR_SRC.stat().st_mtime:
        return OCR_BIN
    if not shutil.which("swiftc"):
        return None
    print(msg("compiling"))
    OCR_BIN.parent.mkdir(exist_ok=True)
    done = subprocess.run(["swiftc", "-O", str(OCR_SRC), "-o", str(OCR_BIN)],
                          capture_output=True, text=True)
    if done.returncode != 0:
        print(msg("compile_failed", done.stderr.strip()[:200]))
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
    """{file: start of its content} for the file types that can be read."""
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
        "name": path.name,
        "extension": path.suffix.lower(),
        "kind": mdls(path, "kMDItemKind"),
        "is_screenshot": screenshot,
        "size": human_size(stat.st_size),
        "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d"),
        "downloaded_from": mdls(path, "kMDItemWhereFroms"),
        "title": mdls(path, "kMDItemTitle"),
        "file_text": text,
    }
    return {k: v for k, v in info.items() if v not in (None, "")}


def sample_names(directory, n):
    return [p.name for p in sorted(directory.iterdir()) if not p.name.startswith(".")][:n]


def build_categories():
    """Predefined categories plus the user's own Desktop folders, described by their contents."""
    criteria = {k: v for k, v in CATEGORIES.items() if k != OTHER}
    taken = {folder_path(fid) for fid in FOLDER_NAMES} | {SCRIPT_DIR}
    for d in sorted(DESKTOP.iterdir()):
        if not d.is_dir() or d.name.startswith(".") or d in taken or d.name in IGNORE:
            continue
        criteria[DIR_PREFIX + d.name] = {
            "what": "The user's existing folder named '%s'. Choose it only when the file clearly "
                    "belongs to that client, project or topic." % d.name,
            "contains": sample_names(d, 6),
        }
    criteria[OTHER] = CATEGORIES[OTHER]
    return criteria


def build_subfolders(categories):
    """{folder option: subfolder options}; predefined ones plus subfolders that already exist."""
    owner = " (%s)" % ENV["OWNER"] if ENV.get("OWNER") else ""
    subs = {}
    for fid in [SCREENSHOTS] + [c for c in categories if c != OTHER]:
        options = {k: v.format(owner=owner) for k, v in SUBFOLDERS.get(fid, {}).items()}
        known = {subfolder_name(fid, s) for s in options}
        directory = folder_path(fid)
        if directory.is_dir():
            for d in sorted(directory.iterdir()):
                if d.is_dir() and not d.name.startswith(".") and d.name not in known:
                    options[DIR_PREFIX + d.name] = {
                        "what": "Existing subfolder '%s'." % d.name, "contains": sample_names(d, 5)}
        if options:
            options[NO_SUBFOLDER] = "No subfolder clearly fits; the file stays at the top of the folder."
            subs[fid] = options
    return subs


# ─── TypeSafe ─────────────────────────────────────────────────────────────────

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
            raise RuntimeError(msg("no_connection", e.reason))


def judge_batch(api_key, categories, subfolders, batch):
    """Every question about every file of the batch in a single request.

    Subfolder questions are speculative: one per possible folder, and the code
    only reads the one for the folder that ends up chosen.
    """
    state = {"files": {}}
    questions = {}
    for i, item in enumerate(batch):
        fid = "f%d" % i
        state["files"][fid] = describe(item["path"], item["screenshot"], item["text"])
        if item["screenshot"]:
            sub_folders = [SCREENSHOTS]
        else:
            sub_folders = list(subfolders)
            questions[fid + "|folder"] = {
                "type": "choice",
                "instructions": "In which Desktop folder should the file described in `files.%s` be "
                                "stored? Decide from its name, kind, origin and text when available." % fid,
                "criteria": categories,
            }
        for folder in sub_folders:
            if folder in subfolders:
                label = folder[len(DIR_PREFIX):] if folder.startswith(DIR_PREFIX) else folder
                questions["%s|sub|%s" % (fid, folder)] = {
                    "type": "choice",
                    "instructions": "Assuming the file described in `files.%s` is stored in the '%s' "
                                    "folder, which of its subfolders does it go in?" % (fid, label),
                    "criteria": subfolders[folder],
                }
        questions[fid + "|importance"] = {
            "type": "score",
            "instructions": IMPORTANCE["instructions"] % fid,
            "criteria": IMPORTANCE["criteria"],
        }
        for name, q in NOULS.items():
            questions["%s|%s" % (fid, name)] = {
                "type": "noul", "instructions": q["instructions"] % fid, "criteria": q["criteria"],
            }

    answers = system_one(api_key, state, questions)
    for i, item in enumerate(batch):
        prefix = "f%d|" % i
        item["answers"] = {k[len(prefix):]: v for k, v in answers.items() if k.startswith(prefix)}
    return batch


# ─── Policy: from judgments to a decision ─────────────────────────────────────

def decide(item):
    """Fills item with folder, subfolder, verdict and reason."""
    a = item["answers"]
    item["importance"] = a["importance"]["score"]
    for name in NOULS:
        item[name] = a[name]["noul"]

    item["folder_note"] = ""
    if item["screenshot"]:
        item["folder"], item["confidence"] = SCREENSHOTS, 1.0
    else:
        choice = a["folder"]
        item["folder"], item["confidence"] = choice["choice"], choice["confidence"]
        if choice["choice"] == OTHER or choice["confidence"] < MIN_CONFIDENCE:
            top = sorted(choice["probabilities"].items(), key=lambda kv: -kv[1])[:2]
            item["folder_note"] = msg("unsure") + ": " + ", ".join(
                "%s %.0f%%" % (k[len(DIR_PREFIX):] if k.startswith(DIR_PREFIX) else k, v * 100)
                for k, v in top)
            item["folder"] = REVIEW

    sub = a.get("sub|" + item["folder"])
    item["subfolder"] = ""
    if sub and sub["choice"] != NO_SUBFOLDER and sub["probabilities"][sub["choice"]] >= MIN_SUB_PROB:
        item["subfolder"] = subfolder_name(item["folder"], sub["choice"])

    signal = max(item["replaceable"], item["temporary"])
    if item.get("duplicate_of"):
        item["verdict"], item["reason"] = "delete", msg("reason_duplicate", display(item["duplicate_of"]))
    elif item["importance"] >= IMPORTANT_FROM or item["sensitive"] >= SENSITIVE_FROM:
        reasons = []
        if item["importance"] >= IMPORTANT_FROM:
            reasons.append(msg("reason_important"))
        if item["sensitive"] >= SENSITIVE_FROM:
            reasons.append(msg("reason_sensitive"))
        item["verdict"], item["reason"] = "keep", " + ".join(reasons)
    elif item["importance"] <= DELETABLE_MAX_IMPORTANCE and signal >= DELETABLE_MIN_SIGNAL:
        item["verdict"] = "delete"
        item["reason"] = msg("reason_replaceable" if item["replaceable"] >= item["temporary"]
                             else "reason_temporary")
    else:
        item["verdict"], item["reason"] = "file", msg("reason_none")
    return item


# ─── Move / undo ──────────────────────────────────────────────────────────────

def safe_dest(dest_dir, filename):
    dest = dest_dir / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    counter = 1
    while dest.exists():
        dest = dest_dir / ("%s (%d)%s" % (stem, counter, suffix))
        counter += 1
    return dest


def move(path, dest_dir, run_id, note=""):
    print("  %s%-36s ← %s%s" % ("[dry] " if ARGS.dry else "", display(dest_dir) + "/", path.name,
                                 "  (%s)" % note if note else ""))
    if ARGS.dry or path.parent == dest_dir:
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
        print(msg("nothing_to_undo"))
        return
    last = entries[-1]["run"]
    restored = 0
    for e in reversed([e for e in entries if e["run"] == last]):
        src, dst = Path(e["to"]), Path(e["from"])
        if src.exists() and not dst.exists():
            shutil.move(str(src), str(dst))
            restored += 1
        else:
            print("  " + msg("undo_failed", src.name))
    remaining = [e for e in entries if e["run"] != last]
    LOG.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in remaining),
                   encoding="utf-8")
    print(msg("undone", last, restored))


def write_report(items, run_id):
    REPORTS.mkdir(exist_ok=True)
    report = REPORTS / ("informe-%s%s.csv" % (run_id, "-dry" if ARGS.dry else ""))
    with open(report, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "verdict", "reason", "destination", "folder_confidence",
                    "importance_0to3", "sensitive", "replaceable", "temporary"])
        for it in items:
            w.writerow([it["path"].name, it["verdict"], it["reason"], display(it["dest"]),
                        "%.2f" % it["confidence"], "%.1f" % it["importance"], "%.2f" % it["sensitive"],
                        "%.2f" % it["replaceable"], "%.2f" % it["temporary"]])
    return report


# ─── Main ─────────────────────────────────────────────────────────────────────

def source_dir():
    if not ARGS.folder:
        return DESKTOP
    src = (DESKTOP / Path(ARGS.folder).expanduser()).resolve()
    inside = DESKTOP in src.parents or src == folder_path(SCREENSHOTS).resolve()
    if not src.is_dir() or not inside:
        sys.exit(msg("bad_folder"))
    return src


def pending_files(src):
    files = []
    for p in sorted(src.iterdir()):
        if (not p.is_file() or p.name.startswith((".", "~$")) or p.name in SKIP_NAMES
                or p.suffix.lower() in SKIP_SUFFIXES):
            continue
        files.append(p)
    return files


def main():
    if ARGS.undo:
        return undo()

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    src = source_dir()
    shots_dir = folder_path(SCREENSHOTS)
    now = time.time()
    items = []
    for p in pending_files(src):
        shot = src == shots_dir.resolve() or is_screenshot(p)
        # screenshots show up complete; anything else may still be downloading
        if shot or now - p.stat().st_mtime >= MIN_AGE_SECONDS:
            items.append({"path": p, "screenshot": shot, "text": None})
    if ARGS.limit:
        items = items[:ARGS.limit]

    api_key = None if ARGS.screenshots_only else ENV.get("TYPESAFE_API_KEY")
    if not ARGS.screenshots_only and not api_key:
        print(msg("no_key", SCRIPT_DIR / ".env") + "\n")

    # No API, or screenshots without content (their name says nothing): fixed rule and done
    direct, judged = [], []
    for it in items:
        if it["screenshot"] and (not api_key or not ARGS.with_content):
            direct.append(it)
        elif api_key:
            judged.append(it)
    if direct:
        print(msg("screenshots", len(direct)))
        for it in direct:
            move(it["path"], shots_dir, run_id, msg("screenshot_note"))
    if not judged:
        return

    print(msg("analyzing", len(judged)))
    if ARGS.with_content:
        texts = extract_texts([it["path"] for it in judged])
        for it in judged:
            it["text"] = texts.get(it["path"])
    roots = {DESKTOP, shots_dir} if shots_dir.is_dir() else {DESKTOP}
    dupes = find_duplicates([it["path"] for it in judged], roots)
    for it in judged:
        it["duplicate_of"] = dupes.get(it["path"])

    categories = build_categories()
    subfolders = build_subfolders(categories)
    batches = [judged[i:i + BATCH_SIZE] for i in range(0, len(judged), BATCH_SIZE)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda b: judge_batch(api_key, categories, subfolders, b), batches))

    counts = {"keep": 0, "file": 0, "delete": 0}
    for it in judged:
        decide(it)
        counts[it["verdict"]] += 1
        note = "%s: %s · %s %.1f/3" % (L["verdicts"][it["verdict"]], it["reason"],
                                      msg("importance"), it["importance"])
        if it["folder_note"]:
            note += " · " + it["folder_note"]
        if it["verdict"] == "delete":
            it["dest"] = folder_path(TO_DELETE)
        else:
            it["dest"] = folder_path(it["folder"]) / it["subfolder"]
        move(it["path"], it["dest"], run_id, note)

    print("\n" + msg("summary", counts["keep"], counts["file"], counts["delete"],
                     display(folder_path(TO_DELETE))))
    print(msg("report", write_report(judged, run_id)))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as e:
        sys.exit("Error: %s" % e)
