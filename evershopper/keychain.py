"""Segreti nel Portachiavi di macOS tramite il comando `security`.

Si usa sempre `security`, sia per scrivere sia per leggere: un elemento creato da
`security` viene riletto dallo stesso programma senza finestre di conferma, quindi
anche quando lo script gira da solo con launchd.

Chiave API (la chiede senza mostrarla e senza finire nella cronologia):
    security add-generic-password -s evershopper -a anthropic-api-key -w
Sessione Everli: la salva discovery/discover.py dopo il login manuale.
"""

from __future__ import annotations

import base64
import json
import platform
import subprocess
from typing import Callable

Runner = Callable[[list[str]], subprocess.CompletedProcess]

# Codice di `security` per "elemento non trovato".
_NOT_FOUND = 44


class KeychainError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def _check_macos(system: str | None) -> None:
    if (system or platform.system()) != "Darwin":
        raise KeychainError("Il Portachiavi è disponibile solo su macOS")


def get_secret(service: str, account: str, *, run: Runner = _run, system: str | None = None) -> str | None:
    if (system or platform.system()) != "Darwin":
        return None
    res = run(["security", "find-generic-password", "-s", service, "-a", account, "-w"])
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None


def set_secret(service: str, account: str, value: str, *, label: str | None = None,
               run: Runner = _run, system: str | None = None) -> None:
    """Crea o aggiorna (-U) l'elemento. Il valore passa come argomento a `security`,
    visibile per un istante solo ai processi dello stesso utente."""
    _check_macos(system)
    cmd = ["security", "add-generic-password", "-U", "-s", service, "-a", account]
    if label:
        cmd += ["-l", label]
    res = run(cmd + ["-w", value])
    if res.returncode != 0:
        raise KeychainError(f"Impossibile salvare nel Portachiavi: {(res.stderr or '').strip()}")


def delete_secret(service: str, account: str, *, run: Runner = _run, system: str | None = None) -> bool:
    _check_macos(system)
    res = run(["security", "delete-generic-password", "-s", service, "-a", account])
    if res.returncode not in (0, _NOT_FOUND):
        raise KeychainError(f"Impossibile cancellare dal Portachiavi: {(res.stderr or '').strip()}")
    return res.returncode == 0


# La sessione Everli è un JSON di cookie: si salva in base64 perché `security -w`
# restituisce in esadecimale i valori con caratteri non stampabili.

def save_session(service: str, account: str, state: dict, **kw) -> None:
    raw = json.dumps(state, separators=(",", ":"), ensure_ascii=False).encode()
    set_secret(service, account, base64.b64encode(raw).decode(), label="Evershopper – sessione Everli", **kw)


def load_session(service: str, account: str, **kw) -> dict | None:
    value = get_secret(service, account, **kw)
    if not value:
        return None
    try:
        return json.loads(base64.b64decode(value))
    except ValueError:
        return None
