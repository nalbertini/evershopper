"""Segreti dal Portachiavi di macOS tramite il comando `security`.

Per salvare la chiave API (la chiede senza mostrarla e senza finire nella cronologia):
    security add-generic-password -s evershopper -a anthropic-api-key -w
"""

from __future__ import annotations

import platform
import subprocess


def get_secret(service: str, account: str) -> str | None:
    if platform.system() != "Darwin":
        return None
    res = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        return None
    return res.stdout.strip() or None
