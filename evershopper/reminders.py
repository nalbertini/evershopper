"""Lettura della lista della spesa da Promemoria.

Due backend, entrambi in sola lettura:
- eventkit: helper Swift compilato (helpers/build.sh → bin/reminders-helper), preferito;
- jxa: JavaScript for Automation via osascript, senza compilazione ma più lento.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class RemindersError(Exception):
    pass


class RemindersAccessDenied(RemindersError):
    pass


class ListNotFound(RemindersError):
    pass


@dataclass
class ShoppingItem:
    id: str
    title: str
    notes: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=60)


def parse_output(text: str) -> list[ShoppingItem]:
    data = json.loads(text)
    items = []
    for raw in data.get("items", []):
        title = (raw.get("title") or "").strip()
        if title:
            notes = (raw.get("notes") or "").strip() or None
            items.append(ShoppingItem(id=str(raw.get("id") or title), title=title, notes=notes))
    return items


def _from_eventkit(helper: Path, list_name: str, run: Runner) -> list[ShoppingItem]:
    res = run([str(helper), "--list", list_name])
    err = (res.stderr or "").strip()
    if res.returncode == 3:
        raise RemindersAccessDenied(err or "Accesso a Promemoria negato")
    if res.returncode == 4:
        raise ListNotFound(err or f'Lista "{list_name}" non trovata')
    if res.returncode != 0:
        raise RemindersError(f"reminders-helper è uscito con codice {res.returncode}: {err}")
    return parse_output(res.stdout)


JXA_SCRIPT = """
const name = %s;
const app = Application("Reminders");
const lists = app.lists.whose({name: name});
if (lists.length === 0) {
  throw new Error("LIST_NOT_FOUND " + app.lists.name().join(", "));
}
const rs = lists[0].reminders.whose({completed: false});
const ids = rs.id(), titles = rs.name(), notes = rs.body();
JSON.stringify({list: name, items: ids.map((id, i) => ({id: id, title: titles[i], notes: notes[i]}))});
"""


def _from_jxa(list_name: str, run: Runner) -> list[ShoppingItem]:
    script = JXA_SCRIPT % json.dumps(list_name)
    res = run(["osascript", "-l", "JavaScript", "-e", script])
    err = (res.stderr or "").strip()
    if res.returncode != 0:
        if "LIST_NOT_FOUND" in err:
            available = re.sub(r"\s*\(-?\d+\)\s*$", "", err.split("LIST_NOT_FOUND", 1)[1]).strip()
            raise ListNotFound(f'Lista "{list_name}" non trovata. Liste disponibili: {available}')
        if "-1743" in err or "not allowed" in err.lower() or "non autorizzat" in err.lower():
            raise RemindersAccessDenied(
                "Accesso a Promemoria negato: abilitalo in Impostazioni di Sistema → "
                "Privacy e sicurezza → Automazione"
            )
        raise RemindersError(f"osascript è uscito con codice {res.returncode}: {err}")
    return parse_output(res.stdout)


def read_list(
    list_name: str,
    *,
    backend: str = "auto",
    helper: Path | None = None,
    run: Runner = _run,
    system: str | None = None,
) -> list[ShoppingItem]:
    """Elementi non completati della lista, nell'ordine di creazione."""
    if (system or platform.system()) != "Darwin":
        raise RemindersError("La lettura di Promemoria funziona solo su macOS (usa --from-json per le prove)")
    if backend not in ("auto", "eventkit", "jxa"):
        raise RemindersError(f"Backend sconosciuto: {backend}")
    helper_ok = helper is not None and helper.exists()
    if backend == "eventkit" and not helper_ok:
        raise RemindersError(f"Helper non trovato ({helper}): compila con helpers/build.sh")
    if backend == "eventkit" or (backend == "auto" and helper_ok):
        log.info("Promemoria: lettura di '%s' via EventKit", list_name)
        return _from_eventkit(helper, list_name, run)
    log.info("Promemoria: lettura di '%s' via osascript (JXA)", list_name)
    return _from_jxa(list_name, run)


def read_json(path: Path) -> list[ShoppingItem]:
    return parse_output(path.read_text(encoding="utf-8"))
