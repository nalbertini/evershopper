"""Notifiche macOS via osascript, in Home Assistant dentro l'add-on; altrove si limita a stampare."""

from __future__ import annotations

import json
import logging
import platform
import subprocess


def notify(title: str, message: str) -> None:
    print(f"[{title}] {message}")
    from . import ha
    if ha.active():
        try:
            ha.notify(title, message)
        except Exception as exc:  # una notifica persa non deve fermare l'esecuzione: resta il log
            logging.getLogger("evershopper").error("Notifica di Home Assistant non inviata: %s", exc)
        return
    if platform.system() != "Darwin":
        return
    # json.dumps produce una stringa con virgolette e escape validi anche per AppleScript.
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
