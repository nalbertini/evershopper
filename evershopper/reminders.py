"""Lettura della lista della spesa da Promemoria.

Tre backend, tutti in sola lettura:
- app: helper Swift dentro bin/EvershopperReminders.app, lanciato con `open`; chiede il
  permesso a nome proprio, quindi funziona da qualsiasi terminale e da launchd (preferito);
- eventkit: lo stesso helper eseguito direttamente; il permesso è quello del terminale;
- jxa: JavaScript for Automation via osascript, senza compilazione ma più lento.
"""

from __future__ import annotations

import json
import logging
import platform
import re
import subprocess
import tempfile
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


# La prima volta si aspetta che l'utente risponda alla richiesta di permesso.
TIMEOUT_S = 180


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)


def _raise_for_code(code: int, err: str, list_name: str | None) -> None:
    if code == 3:
        raise RemindersAccessDenied(
            (err or "Accesso a Promemoria negato")
            + " Abilita «Evershopper Promemoria» in Impostazioni di Sistema → Privacy e sicurezza → Promemoria."
        )
    if code == 4:
        raise ListNotFound(err or f'Lista "{list_name}" non trovata')
    if code != 0:
        raise RemindersError(f"reminders-helper è uscito con codice {code}: {err}")


def parse_output(text: str | dict) -> list[ShoppingItem]:
    data = json.loads(text) if isinstance(text, str) else text
    items = []
    for raw in data.get("items", []):
        title = (raw.get("title") or "").strip()
        if title:
            notes = (raw.get("notes") or "").strip() or None
            items.append(ShoppingItem(id=str(raw.get("id") or title), title=title, notes=notes))
    return items


def _helper_args(list_name: str | None) -> list[str]:
    return ["--list", list_name] if list_name is not None else ["--lists"]


def _via_helper(helper: Path, list_name: str | None, run: Runner) -> dict:
    res = run([str(helper), *_helper_args(list_name)])
    _raise_for_code(res.returncode, (res.stderr or "").strip(), list_name)
    return json.loads(res.stdout)


def _via_app(app: Path, list_name: str | None, run: Runner) -> dict:
    """`open` non restituisce stdout né codice di uscita: il risultato passa da un file."""
    with tempfile.TemporaryDirectory(prefix="evershopper-") as tmp:
        out = Path(tmp) / "out.json"
        res = run(["open", "-W", "-n", "-g", str(app), "--args", *_helper_args(list_name), "--out", str(out)])
        if res.returncode != 0:
            raise RemindersError(f"Impossibile avviare {app.name}: {(res.stderr or '').strip()}")
        if not out.exists():
            raise RemindersError(f"{app.name} non ha prodotto risultati (permesso non concesso in tempo?)")
        data = json.loads(out.read_text(encoding="utf-8"))
    if "error" in data:
        _raise_for_code(int(data.get("code", 5)), data["error"], list_name)
    return data


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


BACKENDS = ("auto", "app", "eventkit", "jxa")


def _choose(backend: str, app: Path | None, helper: Path | None, system: str | None) -> str:
    if (system or platform.system()) != "Darwin":
        raise RemindersError("La lettura di Promemoria funziona solo su macOS (usa --from-json per le prove)")
    if backend not in BACKENDS:
        raise RemindersError(f"Backend sconosciuto: {backend}")
    available = {
        "app": app is not None and app.exists(),
        "eventkit": helper is not None and helper.exists(),
        "jxa": True,
    }
    if backend == "auto":
        return next(b for b in ("app", "eventkit", "jxa") if available[b])
    if not available[backend]:
        raise RemindersError(f"{app if backend == 'app' else helper} non trovato: compila con helpers/build.sh")
    return backend


def read_list(
    list_name: str,
    *,
    backend: str = "auto",
    app: Path | None = None,
    helper: Path | None = None,
    run: Runner = _run,
    system: str | None = None,
) -> list[ShoppingItem]:
    """Elementi non completati della lista, nell'ordine di creazione."""
    chosen = _choose(backend, app, helper, system)
    log.info("Promemoria: lettura di '%s' via %s", list_name, chosen)
    if chosen == "app":
        return parse_output(_via_app(app, list_name, run))
    if chosen == "eventkit":
        return parse_output(_via_helper(helper, list_name, run))
    return _from_jxa(list_name, run)


def list_names(
    *,
    backend: str = "auto",
    app: Path | None = None,
    helper: Path | None = None,
    run: Runner = _run,
    system: str | None = None,
) -> list[str]:
    """Nomi delle liste di Promemoria (utile per la configurazione e per provare il permesso)."""
    chosen = _choose(backend, app, helper, system)
    if chosen == "app":
        return _via_app(app, None, run)["lists"]
    if chosen == "eventkit":
        return _via_helper(helper, None, run)["lists"]
    res = run(["osascript", "-l", "JavaScript", "-e", 'JSON.stringify(Application("Reminders").lists.name())'])
    if res.returncode != 0:
        raise RemindersError(f"osascript è uscito con codice {res.returncode}: {(res.stderr or '').strip()}")
    return json.loads(res.stdout)


def read_json(path: Path) -> list[ShoppingItem]:
    return parse_output(path.read_text(encoding="utf-8"))
