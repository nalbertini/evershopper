"""Esecuzione settimanale con launchd (LaunchAgent dell'utente).

Il LaunchAgent lancia `python -m evershopper run` con il Python dell'ambiente
virtuale, nella cartella del progetto, nel giorno e all'ora di config.yaml.
Se il Mac dorme a quell'ora, launchd esegue al risveglio.
"""

from __future__ import annotations

import os
import plistlib
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from . import ROOT

LABEL = "it.evershopper.weekly"
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

Runner = Callable[[list[str]], subprocess.CompletedProcess]

WEEKDAYS = {"domenica": 0, "lunedi": 1, "martedi": 2, "mercoledi": 3, "giovedi": 4, "venerdi": 5, "sabato": 6}
NAMES = {v: k.replace("edi", "edì") for k, v in WEEKDAYS.items()}


class ScheduleError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def parse_weekday(value) -> int:
    """0 = domenica … 6 = sabato (come launchd); accetta anche i nomi, con o senza accento."""
    if isinstance(value, int) and not isinstance(value, bool):
        if value == 7:
            return 0
        if 0 <= value <= 6:
            return value
    elif isinstance(value, str):
        key = "".join(c for c in unicodedata.normalize("NFKD", value.strip().lower()) if not unicodedata.combining(c))
        if key.isdigit():
            return parse_weekday(int(key))
        if key in WEEKDAYS:
            return WEEKDAYS[key]
    raise ScheduleError(f"Giorno non valido: {value!r} (usa 0–6 con 0 = domenica, oppure il nome)")


def timing(cfg: dict) -> tuple[int, int, int]:
    sc = cfg["schedule"]
    hour, minute = int(sc["hour"]), int(sc["minute"])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleError(f"Ora non valida: {hour}:{minute:02d}")
    return parse_weekday(sc["weekday"]), hour, minute


def next_run(weekday: int, hour: int, minute: int, now: datetime | None = None) -> datetime:
    now = now or datetime.now()
    # datetime.weekday(): lunedì = 0; launchd: domenica = 0.
    days = (weekday - (now.weekday() + 1) % 7) % 7
    candidate = (now + timedelta(days=days)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return candidate if candidate > now else candidate + timedelta(days=7)


def describe(weekday: int, hour: int, minute: int) -> str:
    return f"ogni {NAMES[weekday]} alle {hour:02d}:{minute:02d}"


def plist_path() -> Path:
    return AGENTS_DIR / f"{LABEL}.plist"


def build_plist(cfg: dict, python: str | None = None, root: Path = ROOT, log_dir: Path | None = None) -> dict:
    weekday, hour, minute = timing(cfg)
    log_dir = log_dir or root / "logs"
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "evershopper", "run"],
        "WorkingDirectory": str(root),
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "launchd.log"),
        "StandardErrorPath": str(log_dir / "launchd.log"),
        # launchd parte con un ambiente minimo: osascript, security e open sono in /usr/bin.
        "EnvironmentVariables": {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "it_IT.UTF-8",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
        "RunAtLoad": False,
    }


def _domain() -> str:
    return f"gui/{os.getuid()}"


def install(cfg: dict, *, python: str | None = None, run: Runner = _run, path: Path | None = None,
            log_dir: Path | None = None) -> Path:
    path = path or plist_path()
    python = python or sys.executable
    if ".venv" not in python:
        raise ScheduleError(
            f"Lancia l'installazione con il Python dell'ambiente virtuale (.venv/bin/python), non con {python}"
        )
    data = build_plist(cfg, python, log_dir=log_dir)
    Path(data["StandardOutPath"]).parent.mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        plistlib.dump(data, f)
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])  # se era già caricato; errore ignorato
    res = run(["launchctl", "bootstrap", _domain(), str(path)])
    if res.returncode != 0:
        raise ScheduleError(f"launchctl bootstrap non riuscito: {(res.stderr or res.stdout).strip()}")
    return path


def uninstall(*, run: Runner = _run, path: Path | None = None) -> bool:
    path = path or plist_path()
    run(["launchctl", "bootout", f"{_domain()}/{LABEL}"])
    if path.exists():
        path.unlink()
        return True
    return False


def run_now(*, run: Runner = _run) -> None:
    res = run(["launchctl", "kickstart", f"{_domain()}/{LABEL}"])
    if res.returncode != 0:
        raise ScheduleError("Il LaunchAgent non è caricato: lancia prima `schedule install`")


def status(*, run: Runner = _run, path: Path | None = None) -> dict:
    path = path or plist_path()
    res = run(["launchctl", "print", f"{_domain()}/{LABEL}"])
    info = {"installed": path.exists(), "loaded": res.returncode == 0}
    if res.returncode == 0:
        for key, pattern in (("state", r"^\s*state = (.+)$"), ("runs", r"^\s*runs = (\d+)$"),
                             ("last_exit", r"^\s*last exit code = (.+)$")):
            m = re.search(pattern, res.stdout, re.M)
            if m:
                info[key] = m.group(1).strip()
    if path.exists():
        with path.open("rb") as f:
            info["plist"] = plistlib.load(f)
    return info
