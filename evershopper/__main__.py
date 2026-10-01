"""Uso:
    python -m evershopper fetch                       # scarica le offerte da Everli
    python -m evershopper fetch --from-json f1.json   # prova la mappatura su risposte salvate, senza rete
    python -m evershopper show                        # mostra l'ultima cache
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import cache, config, logs
from .everli.client import (
    EverliClient,
    EverliError,
    PlaywrightTransport,
    SessionExpired,
    load_session,
    local_storage_value,
)
from .everli.offers import check_endpoint, iter_pages, offers_from_pages
from .models import Offer
from .notify import notify

log = logging.getLogger("evershopper")

EXIT_SESSION_EXPIRED = 2
EXIT_ERROR = 1


def _print_offers(offers: list[Offer], limit: int) -> None:
    for o in offers[:limit]:
        pct = f"-{o.discount_pct:g}%" if o.discount_pct is not None else "   "
        full = f"{o.price_full:.2f}" if o.price_full is not None else "?"
        disc = f"{o.price_discounted:.2f}" if o.price_discounted is not None else "?"
        label = " · ".join(x for x in (o.brand, o.name, o.format) if x)
        print(f"{pct:>7}  {disc:>7} € (era {full:>6} €)  {label}")
    if len(offers) > limit:
        print(f"… e altre {len(offers) - limit}")


def cmd_fetch(cfg: dict, args) -> int:
    ev = cfg["everli"]
    ep, store_id = ev["endpoint"], str(ev["store"].get("id") or "")
    check_endpoint(ep, store_id)

    if args.from_json:
        pages = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.from_json]
        source, n_requests = "file", 0
    else:
        session_file = config.resolve(ev["session_file"])
        state = load_session(session_file)
        meta_file = config.resolve(ev["meta_file"])
        user_agent = json.loads(meta_file.read_text())["user_agent"] if meta_file.exists() else None
        headers = dict(ep.get("headers") or {})
        auth = ep.get("auth") or {}
        if auth.get("type") == "local_storage_bearer":
            token = local_storage_value(state, auth.get("key", ""))
            if not token:
                raise SessionExpired("Token non trovato nella sessione salvata: rifai il login")
            headers["Authorization"] = f"Bearer {token}"
        lim = ev["limits"]
        with PlaywrightTransport(
            session_file, user_agent=user_agent, extra_headers=headers, timeout_s=lim["timeout"]
        ) as transport:
            client = EverliClient(
                transport,
                max_requests=lim["max_requests"],
                min_delay=lim["min_delay"],
                max_delay=lim["max_delay"],
            )
            pages = [data for _, data in iter_pages(client, ep, store_id)]
        source, n_requests = "everli", client.requests

    offers = offers_from_pages(pages, ep)
    if args.no_cache:
        path = None
    else:
        path = cache.save(
            config.resolve(cfg["paths"]["cache_dir"]),
            offers,
            pages,
            {"store": ev["store"], "source": source, "requests": n_requests, "pages": len(pages)},
        )
    print(f"{len(offers)} offerte da {len(pages)} pagine ({n_requests} richieste)")
    if path:
        print(f"Cache: {path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path}")
    _print_offers(offers, args.limit)
    log.info("fetch ok: %d offerte, %d richieste", len(offers), n_requests)
    return 0


def cmd_show(cfg: dict, args) -> int:
    latest = cache.load_latest(config.resolve(cfg["paths"]["cache_dir"]))
    if not latest:
        print("Nessuna cache: lancia prima `python -m evershopper fetch`")
        return EXIT_ERROR
    payload, offers = latest
    print(f"Offerte del {payload['fetched_at']} – {payload['count']} prodotti")
    _print_offers(offers, args.limit)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evershopper")
    parser.add_argument("--config", type=Path, help="percorso di config.yaml")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="scarica le offerte del supermercato")
    p_fetch.add_argument("--from-json", nargs="+", metavar="FILE", help="usa risposte JSON salvate")
    p_fetch.add_argument("--no-cache", action="store_true", help="non scrivere la cache")
    p_fetch.add_argument("--limit", type=int, default=20, help="offerte da mostrare")
    p_show = sub.add_parser("show", help="mostra l'ultima cache")
    p_show.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    logs.setup(config.resolve(cfg["paths"]["log_dir"]), args.verbose)

    try:
        return {"fetch": cmd_fetch, "show": cmd_show}[args.cmd](cfg, args)
    except SessionExpired as exc:
        log.warning("Sessione scaduta: %s", exc)
        notify("Offerte Everli", "Sessione Everli scaduta: rifai il login con discovery/discover.py")
        return EXIT_SESSION_EXPIRED
    except EverliError as exc:
        log.error("%s", exc)
        notify("Offerte Everli", f"Errore: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
