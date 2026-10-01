"""Canali di uscita su macOS: notifica, nota in Note, email con Mail.

Note e Mail si pilotano con JavaScript for Automation; i dati passano come
argomento JSON allo script, così nessun testo viene interpolato nel codice.
La prima volta macOS chiede il permesso di Automazione per Note / Mail.
"""

from __future__ import annotations

import json
import logging
import platform
import subprocess
from typing import Callable

from .notify import notify

log = logging.getLogger(__name__)

Runner = Callable[[list[str]], subprocess.CompletedProcess]


class OutputError(Exception):
    pass


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=120)


NOTE_SCRIPT = """
function run(argv) {
  const d = JSON.parse(argv[0]);
  const Notes = Application("Notes");
  let folder;
  if (d.folder) {
    const found = Notes.folders.whose({name: d.folder});
    if (found.length) {
      folder = found[0];
    } else {
      folder = Notes.Folder({name: d.folder});
      Notes.folders.push(folder);
    }
  } else {
    folder = Notes.defaultAccount.defaultFolder;
  }
  const existing = folder.notes.whose({name: d.title});
  if (existing.length) {
    existing[0].body = d.html;
    return "updated";
  }
  folder.notes.push(Notes.Note({body: d.html}));
  return "created";
}
"""

MAIL_SCRIPT = """
function run(argv) {
  const d = JSON.parse(argv[0]);
  const Mail = Application("Mail");
  const msg = Mail.OutgoingMessage({subject: d.subject, content: d.text, visible: false});
  Mail.outgoingMessages.push(msg);
  msg.toRecipients.push(Mail.Recipient({address: d.to}));
  msg.send();
  return "sent";
}
"""


def _jxa(script: str, data: dict, app_name: str, run: Runner) -> str:
    if platform.system() != "Darwin":
        raise OutputError(f"{app_name} è disponibile solo su macOS")
    res = run(["osascript", "-l", "JavaScript", "-e", script, json.dumps(data, ensure_ascii=False)])
    if res.returncode != 0:
        err = (res.stderr or "").strip()
        if "-1743" in err or "not allowed" in err.lower() or "non autorizzat" in err.lower():
            raise OutputError(
                f"Permesso negato per {app_name}: abilitalo in Impostazioni di Sistema → "
                "Privacy e sicurezza → Automazione"
            )
        raise OutputError(f"{app_name}: osascript è uscito con codice {res.returncode}: {err}")
    return (res.stdout or "").strip()


def send_notification(title: str, message: str) -> None:
    notify(title, message)


def write_note(title: str, html: str, folder: str = "", run: Runner = _run) -> str:
    """Crea la nota `title` o ne sostituisce il contenuto; restituisce "created" o "updated"."""
    return _jxa(NOTE_SCRIPT, {"title": title, "html": html, "folder": folder}, "Note", run)


def send_email(to: str, subject: str, text: str, run: Runner = _run) -> None:
    if not to:
        raise OutputError("Indirizzo email mancante: imposta output.email_to in config.yaml")
    _jxa(MAIL_SCRIPT, {"to": to, "subject": subject, "text": text}, "Mail", run)
