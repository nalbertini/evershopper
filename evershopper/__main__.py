"""Uso:
    python -m evershopper fetch                       # scarica le offerte da Everli
    python -m evershopper fetch --from-json f1.json   # prova la mappatura su risposte salvate, senza rete
    python -m evershopper show                        # mostra l'ultima cache
    python -m evershopper reminders                   # legge la lista della spesa da Promemoria
    python -m evershopper match                       # voci della lista in offerta (ultima cache)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from . import cache, config, keychain, llm, logs, matching, reminders
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


def cmd_reminders(cfg: dict, args) -> int:
    rc = cfg["reminders"]
    list_name = args.list or rc["list"]
    where = {
        "backend": args.backend or rc["backend"],
        "app": config.resolve(rc["app"]),
        "helper": config.resolve(rc["helper"]),
    }
    list_id = args.list_id if args.list_id is not None else rc.get("list_id")
    if args.lists:
        lists = reminders.list_lists(**where)
        width = max((len(x["title"]) for x in lists), default=0)
        print("Liste di Promemoria:")
        for x in lists:
            chosen = x["id"] == list_id if list_id else x["title"] == list_name
            account = f"[{x['source']}]" if x.get("source") else ""
            print(f"  - {x['title']:<{width}}  {account:<12} id: {x['id']}" + ("   ← configurata" if chosen else ""))
        titles = [x["title"] for x in lists]
        if not list_id and titles.count(list_name) > 1:
            print(
                f"\nCi sono {titles.count(list_name)} liste «{list_name}»: senza `list_id` le voci vengono unite.\n"
                "Per usarne una sola copia l'id in config.yaml → reminders.list_id (o usa --list-id)."
            )
        return 0
    if args.from_json:
        items = reminders.read_json(args.from_json)
    else:
        items = reminders.read_list(list_name, list_id=list_id or None, **where)
    if args.json:
        print(json.dumps([i.to_dict() for i in items], ensure_ascii=False, indent=2))
        return 0
    print(f'Lista "{list_name}": {len(items)} voci da comprare')
    for i in items:
        print(f"  - {i.title}" + (f"  ({i.notes})" if i.notes else ""))
    log.info("reminders ok: %d voci", len(items))
    return 0


def _load_items(cfg: dict, path: Path | None) -> list:
    if path:
        return reminders.read_json(path)
    rc = cfg["reminders"]
    return reminders.read_list(
        rc["list"], list_id=rc.get("list_id") or None, backend=rc["backend"],
        app=config.resolve(rc["app"]), helper=config.resolve(rc["helper"]),
    )


def _load_offers(cfg: dict, path: Path | None) -> tuple[str, list[Offer]]:
    if path:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("fetched_at", path.name), [Offer.from_dict(o) for o in payload["offers"]]
    latest = cache.load_latest(config.resolve(cfg["paths"]["cache_dir"]))
    if not latest:
        raise EverliError("Nessuna offerta in cache: lancia prima `python -m evershopper fetch`")
    payload, offers = latest
    return payload["fetched_at"], offers


def _fmt_offer(o: Offer) -> str:
    pct = f"-{o.discount_pct:g}%" if o.discount_pct is not None else "    "
    price = f"{o.price_discounted:.2f} €" if o.price_discounted is not None else "? €"
    was = f" (era {o.price_full:.2f} €)" if o.price_full is not None else ""
    label = " · ".join(x for x in (o.brand, o.name, o.format) if x)
    return f"{pct:>7}  {price}{was}  {label}"


def cmd_match(cfg: dict, args) -> int:
    mc = cfg["matching"]
    fetched_at, offers = _load_offers(cfg, args.offers_json)
    items = _load_items(cfg, args.reminders_json)
    matcher = matching.Matcher(mc.get("synonyms"), mc.get("exclude"), int(mc.get("max_doubts", 8)))
    results = matcher.match(items, offers)

    lc = mc["llm"]
    use_llm = lc["enabled"] if args.llm is None else args.llm
    if use_llm and any(r.doubts for r in results):
        key = keychain.get_secret(lc["keychain_service"], lc["keychain_account"])
        if not key:
            log.warning(
                "Chiave API non trovata nel Portachiavi (servizio %s, account %s): salto la seconda passata",
                lc["keychain_service"], lc["keychain_account"],
            )
        else:
            try:
                moved = llm.resolve(results, api_key=key, model=lc["model"], scope=lc.get("scope", "ambiguous"),
                                    cache_dir=config.resolve(cfg["paths"]["cache_dir"]))
                log.info("Claude ha confermato %d abbinamenti", moved)
            except Exception as exc:  # la seconda passata è opzionale: mai bloccare l'esecuzione
                log.warning("Seconda passata con Claude non riuscita (%s): restano i risultati locali",
                            type(exc).__name__)

    if not args.no_cache:
        out_dir = config.resolve(cfg["paths"]["cache_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"match-{datetime.now():%Y-%m-%d}.json"
        out.write_text(json.dumps({"offers_fetched_at": fetched_at, "results": [r.to_dict() for r in results]},
                                  ensure_ascii=False, indent=2), encoding="utf-8")

    threshold = float(cfg["output"].get("min_discount_pct") or 0)
    in_offer = [r for r in results if any((c.offer.discount_pct or 0) >= threshold for c in r.matches)]
    print(f"Offerte del {fetched_at} · {len(in_offer)} voci su {len(results)} in offerta"
          + (f" (sconto ≥ {threshold:g}%)" if threshold else ""))
    for r in results:
        shown = [c for c in r.matches if (c.offer.discount_pct or 0) >= threshold][: args.limit]
        title = r.item.title + (f" ({r.item.notes})" if r.item.notes else "")
        if not shown and not r.doubts:
            if r.matches:
                best = max(c.offer.discount_pct or 0 for c in r.matches)
                print(f"\n{title} — in offerta solo sotto soglia (max -{best:g}%)")
            else:
                print(f"\n{title} — nessuna offerta")
            continue
        print(f"\n{title}")
        for c in shown:
            print(_fmt_offer(c.offer) + ("   [Claude]" if c.source == "llm" else ""))
        hidden = len(r.matches) - len(shown)
        if hidden > 0:
            print(f"      … altre {hidden} sotto soglia o oltre il limite")
        for c in r.doubts[:3]:
            print(f"      ? {_fmt_offer(c.offer).strip()}  — {c.reason}")
        if len(r.doubts) > 3:
            print(f"      ? … e altri {len(r.doubts) - 3} dubbi")
    if any(r.doubts for r in results) and not use_llm:
        print("\nI dubbi si possono far decidere a Claude con --llm (vedi README).")
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
    p_rem = sub.add_parser("reminders", help="legge la lista della spesa da Promemoria")
    p_rem.add_argument("--list", help="nome della lista (default da config.yaml)")
    p_rem.add_argument("--backend", choices=list(reminders.BACKENDS))
    p_rem.add_argument("--lists", action="store_true", help="elenca le liste disponibili")
    p_rem.add_argument("--list-id", help="identificativo della lista, se ci sono nomi duplicati")
    p_rem.add_argument("--from-json", type=Path, metavar="FILE", help="usa un output salvato dell'helper")
    p_rem.add_argument("--json", action="store_true", help="stampa in JSON")
    p_match = sub.add_parser("match", help="voci della lista in offerta")
    p_match.add_argument("--offers-json", type=Path, metavar="FILE", help="offerte da un file di cache")
    p_match.add_argument("--reminders-json", type=Path, metavar="FILE", help="lista da un file JSON")
    p_match.add_argument("--llm", dest="llm", action="store_true", default=None, help="forza la seconda passata")
    p_match.add_argument("--no-llm", dest="llm", action="store_false", help="salta la seconda passata")
    p_match.add_argument("--no-cache", action="store_true", help="non salvare il risultato")
    p_match.add_argument("--limit", type=int, default=3, help="offerte mostrate per voce")
    args = parser.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    logs.setup(config.resolve(cfg["paths"]["log_dir"]), args.verbose)

    try:
        return {"fetch": cmd_fetch, "show": cmd_show, "reminders": cmd_reminders, "match": cmd_match}[args.cmd](cfg, args)
    except SessionExpired as exc:
        log.warning("Sessione scaduta: %s", exc)
        notify("Offerte Everli", "Sessione Everli scaduta: rifai il login con discovery/discover.py")
        return EXIT_SESSION_EXPIRED
    except reminders.RemindersAccessDenied as exc:
        log.error("%s", exc)
        notify("Offerte Everli", "Accesso a Promemoria negato: controlla Privacy e sicurezza")
        return EXIT_ERROR
    except reminders.RemindersError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except EverliError as exc:
        log.error("%s", exc)
        notify("Offerte Everli", f"Errore: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
