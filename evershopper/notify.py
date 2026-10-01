"""Notifiche macOS via osascript; altrove si limita a stampare."""

from __future__ import annotations

import json
import platform
import subprocess


def notify(title: str, message: str) -> None:
    print(f"[{title}] {message}")
    if platform.system() != "Darwin":
        return
    # json.dumps produce una stringa con virgolette e escape validi anche per AppleScript.
    script = f"display notification {json.dumps(message)} with title {json.dumps(title)}"
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
