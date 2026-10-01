"""Fase 1 – Discovery dell'API interna di Everli.

Apre it.everli.com in un Chromium visibile, lascia fare il login a mano,
registra le chiamate XHR/fetch mentre si naviga fino alla pagina offerte
del supermercato e alla fine salva la sessione (storageState) nel Portachiavi.

Uso (sul Mac, dalla root del repo):
    .venv/bin/python discovery/discover.py            # discovery: login + registrazione chiamate
    .venv/bin/python discovery/discover.py --login    # solo nuovo login, quando la sessione è scaduta

Niente credenziali passano dallo script: il login lo fa l'utente nel browser.
Cookie e token negli header vengono oscurati prima di scrivere su disco.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Request, Response, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from evershopper import config, keychain  # noqa: E402

STATE_DIR = ROOT / ".state"
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


def page_url(response: Response) -> str | None:
    """Pagina che ha fatto la richiesta; None per quelle dei service worker, che non hanno pagina."""
    try:
        frame = response.frame
    except Exception:
        return None
    return frame.url if frame else None


def wait_for_enter(context) -> None:
    """Aspetta INVIO nel terminale tenendo vivo Playwright.

    Con un semplice input() l'API sincrona di Playwright resta ferma: gli eventi di rete
    verrebbero elaborati tutti alla fine, quando alcune risposte non sono più leggibili.
    """
    done = threading.Event()
    threading.Thread(target=lambda: (sys.stdin.readline(), done.set()), daemon=True).start()
    while not done.is_set():
        pages = context.pages
        if not pages:
            print("Tutte le schede sono state chiuse: salvo quello che c'è.")
            return
        pages[0].wait_for_timeout(250)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discovery dell'API Everli e login manuale")
    parser.add_argument("--login", action="store_true", help="solo login: non registra le chiamate di rete")
    parser.add_argument("--url", default=START_URL, help=argparse.SUPPRESS)  # per le prove
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)  # per le prove
    args = parser.parse_args()
    login_only = args.login

    kc = config.load()["everli"]["keychain"]
    try:
        saved = keychain.load_session(kc["service"], kc["session_account"])
    except keychain.KeychainError:
        saved = None
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    capture_file = CAPTURE_DIR / f"capture-{stamp}.jsonl"
    bodies_dir = CAPTURE_DIR / f"bodies-{stamp}"
    out = capture_file.open("w", encoding="utf-8")
    count = 0

    def on_response(response: Response) -> None:
        if login_only:
            return
        try:
            record_response(response)
        except Exception as exc:  # una risposta strana non deve fermare la discovery
            print(f"  (risposta ignorata: {str(exc).splitlines()[0]})")

    def record_response(response: Response) -> None:
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
            "page": page_url(response),
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
        browser = p.chromium.launch(headless=args.headless, slow_mo=50)
        context = browser.new_context(
            storage_state=saved,
            locale="it-IT",
            viewport={"width": 1280, "height": 900},
        )
        context.on("response", on_response)
        page = context.new_page()
        page.goto(args.url)
        # Lo user agent si legge subito, finché la pagina è sicuramente aperta;
        # serve anche alle chiamate dirette della fase 2.
        META_FILE.write_text(json.dumps({"user_agent": page.evaluate("navigator.userAgent")}))

        if login_only:
            print("\nBrowser aperto: fai login a mano, poi premi INVIO qui.\n")
        else:
            print(
                "\nBrowser aperto.\n"
                "  1. Fai login a mano (solo la prima volta).\n"
                "  2. Scegli il supermercato abituale e apri la pagina offerte/promozioni.\n"
                "  3. Scorri un po' la lista e passa alla pagina successiva, se c'è.\n"
                "Le chiamate di rete vengono registrate qui sotto.\n"
            )
        print("Premi INVIO qui quando hai finito per salvare la sessione e chiudere…")
        wait_for_enter(context)

        # Nessun file: la sessione va direttamente nel Portachiavi.
        keychain.save_session(kc["service"], kc["session_account"], context.storage_state())
        if context.pages:
            print(f"\nURL finale: {context.pages[0].url}")
        browser.close()

    out.close()
    print(f"Sessione salvata nel Portachiavi (servizio {kc['service']}, account {kc['session_account']})")
    if login_only:
        capture_file.unlink(missing_ok=True)
        return 0
    print(f"{count} chiamate registrate in {capture_file.relative_to(ROOT)}")
    print("Ora lancia: .venv/bin/python discovery/summarize.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
