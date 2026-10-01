"""Controllo generale prima di lasciare girare lo script da solo."""

from __future__ import annotations

import importlib.util
import platform
import subprocess
import sys
from datetime import datetime

from . import ROOT, cache, config, keychain, schedule
from .everli.offers import EndpointNotConfigured, check_endpoint

OK, FAIL, SKIP = "✓", "✗", "–"

# File che non devono mai finire nel repository.
PRIVATE_PATHS = (".state/", "discovery/captures/", "cache/", "logs/", "config.yaml", "bin/")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)


def checks(cfg: dict):
    """Genera (stato, descrizione, suggerimento)."""
    ev, rc, lc = cfg["everli"], cfg["reminders"], cfg["matching"]["llm"]
    macos = platform.system() == "Darwin"

    yield (OK if sys.version_info >= (3, 11) else FAIL, f"Python {platform.python_version()}",
           "serve Python 3.11 o successivo")
    in_venv = sys.prefix != sys.base_prefix
    yield (OK if in_venv else FAIL, "Ambiente virtuale" + (f" ({sys.prefix})" if in_venv else ""),
           "usa .venv/bin/python")
    for mod, needed in (("playwright", True), ("yaml", True), ("anthropic", lc["enabled"])):
        found = importlib.util.find_spec(mod) is not None
        yield (OK if found else (FAIL if needed else SKIP), f"Pacchetto {mod}",
               ".venv/bin/python -m pip install -r requirements.txt")

    yield (OK if config.DEFAULT.exists() else SKIP, "config.yaml",
           "facoltativo: cp config.example.yaml config.yaml per cambiare le impostazioni")
    try:
        check_endpoint(ev["endpoint"], str(ev["store"].get("id") or ""))
        yield OK, "Endpoint Everli configurato", ""
    except EndpointNotConfigured as exc:
        yield FAIL, "Endpoint Everli", f"{exc} – serve la discovery (fase 1)"

    kc = ev["keychain"]
    if macos:
        has_session = keychain.load_session(kc["service"], kc["session_account"]) is not None
        yield (OK if has_session else FAIL, "Sessione Everli nel Portachiavi",
               ".venv/bin/python discovery/discover.py --login")
    else:
        yield SKIP, "Sessione Everli nel Portachiavi", "solo su macOS"
    meta = config.resolve(ev["meta_file"])
    yield (OK if meta.exists() else FAIL, "User agent del browser (.state/everli-meta.json)",
           "lo salva discovery/discover.py")

    app, helper = config.resolve(rc["app"]), config.resolve(rc["helper"])
    yield (OK if app.exists() else FAIL, f"Helper Promemoria ({app.name})", "sh helpers/build.sh")
    if not rc.get("list_id"):
        yield SKIP, f"Lista «{rc['list']}» scelta per nome", "con nomi duplicati imposta reminders.list_id"
    else:
        yield OK, f"Lista «{rc['list']}» scelta per id", ""
    if cfg["output"].get("mark_reminders") and not helper.exists() and not app.exists():
        yield FAIL, "Etichette sui promemoria", "servono l'helper: sh helpers/build.sh"

    if lc["enabled"]:
        key = keychain.get_secret(lc["keychain_service"], lc["keychain_account"])
        yield (OK if key else FAIL, "Chiave API Claude nel Portachiavi",
               f"security add-generic-password -s {lc['keychain_service']} -a {lc['keychain_account']} -w")
    else:
        yield SKIP, "Seconda passata con Claude disattivata", ""

    channels = cfg["output"].get("channels") or []
    if "email" in channels and not cfg["output"].get("email_to"):
        yield FAIL, "Canale email", "imposta output.email_to"
    else:
        yield OK, "Canali di uscita: " + ", ".join(channels or ["nessuno"]), ""

    latest = cache.load_latest(config.resolve(cfg["paths"]["cache_dir"]))
    if latest:
        fetched = latest[0]["fetched_at"]
        try:
            age = (datetime.now() - datetime.fromisoformat(fetched)).days
        except ValueError:
            age = None
        yield (OK if age is not None and age <= 7 else SKIP, f"Ultime offerte in cache: {fetched}",
               "verranno riscaricate alla prossima esecuzione")
    else:
        yield SKIP, "Nessuna offerta in cache", "python -m evershopper fetch"

    if macos:
        info = schedule.status()
        if info["installed"] and info["loaded"]:
            cal = info["plist"]["StartCalendarInterval"]
            when = schedule.describe(cal["Weekday"], cal["Hour"], cal["Minute"])
            last = info.get("last_exit")
            note = f", ultimo codice {last}" if last and last != "(never exited)" else ""
            yield OK, f"LaunchAgent attivo: {when}{note}", ""
            python = info["plist"]["ProgramArguments"][0]
            if python != sys.executable:
                yield FAIL, f"Il LaunchAgent usa un altro Python ({python})", "rilancia schedule install"
        else:
            yield FAIL, "LaunchAgent non installato", ".venv/bin/python -m evershopper schedule install"
    else:
        yield SKIP, "LaunchAgent", "solo su macOS"

    tracked = _git("ls-files").stdout.splitlines()
    leaked = sorted({p for p in tracked for priv in PRIVATE_PATHS if p == priv.rstrip("/") or p.startswith(priv)})
    yield (OK if not leaked else FAIL, "Nessun file privato nel repository",
           "togli dal repository: " + ", ".join(leaked[:5]))
    secrets = _git("grep", "-l", "-E", r"sk-ant-[A-Za-z0-9_-]{10,}", "--", ".").stdout.split()
    yield (OK if not secrets else FAIL, "Nessuna chiave API nei file del repository",
           "chiave trovata in: " + ", ".join(secrets[:5]))
    perms = config.resolve(".state")
    if perms.exists():
        mode = perms.stat().st_mode & 0o777
        yield (OK if mode & 0o077 == 0 else FAIL, f"Permessi di .state ({oct(mode)})", "chmod 700 .state")


def run(cfg: dict) -> bool:
    ok = True
    for status, what, hint in checks(cfg):
        print(f" {status} {what}" + (f"\n     → {hint}" if status == FAIL and hint else ""))
        ok &= status != FAIL
    print("\nTutto pronto." if ok else "\nCi sono punti da sistemare (✗).")
    return ok

