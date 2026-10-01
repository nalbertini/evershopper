"""Fase 1 – Discovery dell'API interna di Everli.

Apre it.everli.com in un Chromium visibile, lascia fare il login a mano,
registra le chiamate XHR/fetch mentre si naviga fino alla pagina offerte
del supermercato e alla fine salva la sessione (storageState).

Uso (sul Mac, dalla root del repo):
    python discovery/discover.py

Niente credenziali passano dallo script: il login lo fa l'utente nel browser.
Cookie e token negli header vengono oscurati prima di scrivere su disco.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Request, Response, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / ".state"
SESSION_FILE = STATE_DIR / "everli-session.json"
META_FILE = STATE_DIR / "everli-meta.json"
CAPTURE_DIR = ROOT / "discovery" / "captures"

START_URL = "https://it.everli.com/"
# Domini di terze parti (analytics, ads, chat…) che non ci interessano.
IGNORED_HOSTS = re.compile(
    r"(google|doubleclick|facebook|hotjar|segment|sentry|datadog|criteo|"
    r"tiktok|bing|clarity|onetrust|cookielaw|intercom|zendesk|braze|"
    r"amplitude|mixpanel|branch\.io|appsflyer)",
    re.I,
)
SENSITIVE_HEADERS = re.compile(r"(cookie|authorization|token|session|secret|csrf|xsrf)", re.I)
SENSITIVE_PARAMS = re.compile(r"(token|session|secret|password|auth|key)", re.I)
BODY_SAMPLE_CHARS = 4000
# Le risposte JSON con queste chiavi vengono salvate intere, per provare la
# mappatura della fase 2 senza rete: python -m evershopper fetch --from-json …
OFFER_HINTS = re.compile(r"(price|prezzo|discount|sconto|promo|offer|offert)", re.I)


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    return {k: ("<redacted>" if SENSITIVE_HEADERS.search(k) else v) for k, v in headers.items()}


def redact_url(url: str) -> tuple[str, dict[str, str]]:
    parts = urlsplit(url)
    params = {
        k: ("<redacted>" if SENSITIVE_PARAMS.search(k) else v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
    }
    return f"{parts.scheme}://{parts.netloc}{parts.path}", params


def main() -> int:
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    capture_file = CAPTURE_DIR / f"capture-{stamp}.jsonl"
    bodies_dir = CAPTURE_DIR / f"bodies-{stamp}"
    out = capture_file.open("w", encoding="utf-8")
    count = 0

    def on_response(response: Response) -> None:
        nonlocal count
        request: Request = response.request
        if request.resource_type not in ("xhr", "fetch"):
            return
        if IGNORED_HOSTS.search(urlsplit(request.url).netloc):
            return
        content_type = response.headers.get("content-type", "")
        body_sample = body_file = None
        if "json" in content_type:
            try:
                text = response.text()
                body_sample = text[:BODY_SAMPLE_CHARS]
                if OFFER_HINTS.search(text):
                    bodies_dir.mkdir(exist_ok=True)
                    body_file = bodies_dir / f"{count + 1:04d}.json"
                    body_file.write_text(text, encoding="utf-8")
            except Exception as exc:  # body non disponibile (redirect, stream chiuso…)
                body_sample = f"<unavailable: {exc}>"
        endpoint, params = redact_url(request.url)
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "page": response.frame.url if response.frame else None,
            "method": request.method,
            "endpoint": endpoint,
            "params": params,
            "status": response.status,
            "content_type": content_type,
            "request_headers": redact_headers(request.headers),
            "post_data": request.post_data[:BODY_SAMPLE_CHARS] if request.post_data else None,
            "body_sample": body_sample,
            "body_file": str(body_file.relative_to(ROOT)) if body_file else None,
        }
        out.write(json.dumps(record, ensure_ascii=False) + "\n")
        out.flush()
        count += 1
        print(f"  [{response.status}] {request.method} {endpoint}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=50)
        context = browser.new_context(
            storage_state=str(SESSION_FILE) if SESSION_FILE.exists() else None,
            locale="it-IT",
            viewport={"width": 1280, "height": 900},
        )
        context.on("response", on_response)
        page = context.new_page()
        page.goto(START_URL)

        print(
            "\nBrowser aperto.\n"
            "  1. Fai login a mano (solo la prima volta).\n"
            "  2. Scegli il supermercato abituale e apri la pagina offerte/promozioni.\n"
            "  3. Scorri un po' la lista e passa alla pagina successiva, se c'è.\n"
            "Le chiamate di rete vengono registrate qui sotto.\n"
        )
        input("Premi INVIO qui quando hai finito per salvare la sessione e chiudere…\n")

        context.storage_state(path=str(SESSION_FILE))
        os.chmod(SESSION_FILE, 0o600)
        # Stesso user agent anche per le chiamate dirette della fase 2.
        META_FILE.write_text(json.dumps({"user_agent": page.evaluate("navigator.userAgent")}))
        print(f"\nURL finale: {page.url}")
        browser.close()

    out.close()
    print(f"Sessione salvata in {SESSION_FILE.relative_to(ROOT)} (permessi 600, ignorata da git)")
    print(f"{count} chiamate registrate in {capture_file.relative_to(ROOT)}")
    print("Ora lancia: python discovery/summarize.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
