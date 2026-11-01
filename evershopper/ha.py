"""Dentro l'add-on di Home Assistant (c'è SUPERVISOR_TOKEN): lista della spesa da una To-do di HA,
notifiche, sensore con le voci in offerta e la pagina dell'ultimo riepilogo nella barra laterale (Ingress).

Il sensore HA lo dimentica al riavvio: `serve` lo riscrive all'avvio e ogni 5 minuti dall'ultimo riepilogo.
"""

from __future__ import annotations

import base64
import http.server
import json
import logging
import os
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable

from .reminders import RemindersError, ShoppingItem

log = logging.getLogger("evershopper")
API = "http://supervisor/core/api/"
SENSOR = "sensor.evershopper_offerte"
SENSOR_FILE = "ha-sensor.json"
REPORT_FILE = "report-ultimo.html"
EVERY = 300
PORT = 8791

Post = Callable[[str, dict], dict | list | None]


def active() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _post(path: str, body: dict) -> dict | list | None:
    req = urllib.request.Request(API + path, json.dumps(body).encode(), method="POST", headers={
        "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as res:
        return json.loads(res.read() or b"null")


def read_todo(entity: str, post: Post | None = None) -> list[ShoppingItem]:
    """Voci da comprare (non spuntate) della To-do `entity`, es. todo.spesa."""
    try:
        data = (post or _post)("services/todo/get_items?return_response",
                    {"entity_id": entity, "status": "needs_action"})
    except Exception as exc:
        raise RemindersError(f"Lista {entity} non letta da Home Assistant: {exc}") from None
    raw = ((data or {}).get("service_response") or {}).get(entity)
    if raw is None:
        raise RemindersError(f"Lista {entity} non trovata in Home Assistant (Impostazioni → Entità)")
    return [ShoppingItem(id=i.get("uid") or i["summary"], title=i["summary"].strip(),
                         notes=(i.get("description") or "").strip() or None, list_id=entity)
            for i in raw.get("items", []) if (i.get("summary") or "").strip()]


def notify(title: str, message: str, post: Post | None = None) -> None:
    """Notifica persistente in HA e, se impostato, push sul telefono (opzione notify_service)."""
    post = post or _post
    post("services/persistent_notification/create",
         {"title": title, "message": message, "notification_id": "evershopper"})
    service = os.environ.get("EVERSHOPPER_NOTIFY", "").strip()
    if service:
        post("services/" + service.replace(".", "/", 1), {"title": title, "message": message})


def sensor(rep, when: datetime) -> dict:
    return {"state": str(len(rep.lines)), "attributes": {
        "friendly_name": "Spesa: voci in offerta", "icon": "mdi:sale", "unit_of_measurement": "voci",
        "negozio": rep.store, "offerte_del": rep.offers_date, "aggiornato": when.isoformat(timespec="minutes"),
        "voci": [{"voce": line.item.title, "sconto": line.best,
                  "prezzo": line.offers[0].price_discounted, "prodotto": line.offers[0].name}
                 for line in rep.lines],
        "da_verificare": [item.title for item, _ in rep.doubts]}}


def save(cache_dir: Path, rep, html: str, post: Post | None = None) -> None:
    """Dopo un `run`: salva sensore e pagina per `serve` e scrive subito il sensore."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    body = sensor(rep, datetime.now())
    (cache_dir / SENSOR_FILE).write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    (cache_dir / REPORT_FILE).write_text(html, encoding="utf-8")
    (post or _post)("states/" + SENSOR, body)


def decode_session(blob: str) -> tuple[dict, str]:
    """L'opzione everli_session: base64 di {"state": storageState, "user_agent": …} (da `export-session`)."""
    data = json.loads(base64.b64decode(blob))
    return data["state"], data["user_agent"]


PAGE = """<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Spesa</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:720px;margin:0 auto;padding:16px;
background:#fff;color:#1c1c1c}a{color:#0b6bcb}@media (prefers-color-scheme:dark){body{background:#111;color:#eee}
a{color:#7ab8ff}}</style></head><body>%s</body></html>"""


def _handler(cache_dir: Path):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            page = cache_dir / REPORT_FILE
            body = page.read_text(encoding="utf-8") if page.exists() else \
                "<p>Ancora nessun riepilogo: arriva dopo la prima esecuzione pianificata.</p>"
            data = (PAGE % body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    return Handler


def serve(cache_dir: Path, next_run: Callable[[], datetime], run: Callable[[], int]) -> None:
    """Pagina su PORT (solo Ingress), sensore ogni 5 minuti, `run` all'orario pianificato. Non ritorna."""
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT), _handler(cache_dir))
    threading.Thread(target=server.serve_forever, name="evershopper-page", daemon=True).start()

    def publish() -> None:
        failing = False
        while True:
            path = cache_dir / SENSOR_FILE
            try:
                if path.exists():
                    _post("states/" + SENSOR, json.loads(path.read_text(encoding="utf-8")))
                failing = False
            except Exception:
                if not failing:  # una volta sola finché non torna a funzionare
                    log.exception("Sensore di Home Assistant non aggiornato")
                failing = True
            time.sleep(EVERY)
    threading.Thread(target=publish, name="evershopper-sensor", daemon=True).start()

    while True:
        when = next_run()
        log.warning("Prossima esecuzione: %s", f"{when:%d/%m/%Y %H:%M}")
        time.sleep(max(0.0, (when - datetime.now()).total_seconds()))
        code = run()
        log.warning("Esecuzione terminata con codice %d", code)
        time.sleep(61)  # mai due esecuzioni nello stesso minuto
