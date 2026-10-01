"""Uso:
    python -m evershopper fetch                       # scarica le offerte da Everli
    python -m evershopper fetch --from-json f1.json   # prova la mappatura su risposte salvate, senza rete
    python -m evershopper show                        # mostra l'ultima cache
    python -m evershopper reminders                   # legge la lista della spesa da Promemoria
    python -m evershopper match                       # voci della lista in offerta (ultima cache)
    python -m evershopper run                         # tutto il flusso + notifica/nota/email (fase 5)
    python -m evershopper run --offline --dry-run     # prova senza Everli e senza inviare niente
    python -m evershopper schedule install            # esecuzione settimanale con launchd (fase 6)
    python -m evershopper doctor                      # controlla che sia tutto pronto
    python -m evershopper autoconfig                  # ricava l'endpoint dall'ultima discovery
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import autoconfig, cache, config, doctor, keychain, logs, outputs, pipeline, report, reminders, schedule
from .everli.client import ConnectionFailed, EverliError, SessionExpired
from .models import Offer
from .notify import notify

log = logging.getLogger("evershopper")

EXIT_SESSION_EXPIRED = 2
EXIT_ERROR = 1
RETRY_DELAY_S = 90
EXIT_MEANING = {"0": "ok", "1": "errore (vedi logs/evershopper.log)", "2": "sessione Everli scaduta"}


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
    offers, pages, n_requests, path = pipeline.fetch_offers(cfg, args.from_json, save=not args.no_cache)
    print(f"{len(offers)} offerte da {len(pages)} pagine ({n_requests} richieste)")
    if path:
        print(f"Cache: {path.relative_to(config.ROOT) if path.is_relative_to(config.ROOT) else path}")
    _print_offers(offers, args.limit)
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


def _fmt_offer(o: Offer) -> str:
    pct = f"-{o.discount_pct:g}%" if o.discount_pct is not None else "    "
    price = f"{o.price_discounted:.2f} €" if o.price_discounted is not None else "? €"
    was = f" (era {o.price_full:.2f} €)" if o.price_full is not None else ""
    label = " · ".join(x for x in (o.brand, o.name, o.format) if x)
    return f"{pct:>7}  {price}{was}  {label}"


def cmd_match(cfg: dict, args) -> int:
    fetched_at, offers = pipeline.load_offers(cfg, args.offers_json)
    items = pipeline.load_items(cfg, args.reminders_json)
    results, use_llm = pipeline.run_matching(cfg, offers, items, args.llm)
    if not args.no_cache:
        pipeline.save_match(cfg, fetched_at, results)

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


def _age_days(fetched_at: str) -> int | None:
    try:
        return (datetime.now() - datetime.fromisoformat(fetched_at)).days
    except ValueError:
        return None


def _deliver(cfg: dict, rep: report.Report, text: str, html: str, items: list, from_file: bool) -> int:
    """Invia il riepilogo sui canali configurati; restituisce quanti canali sono falliti."""
    oc = cfg["output"]
    channels = oc.get("channels") or []
    channels = [channels] if isinstance(channels, str) else channels
    failures = 0
    for ch in channels:
        try:
            if ch == "notification":
                outputs.send_notification(*report.notification(rep))
            elif ch == "note":
                res = outputs.write_note(oc["title"], html, oc.get("note_folder") or "")
                print(f"Nota «{oc['title']}» {'aggiornata' if res == 'updated' else 'creata'} in Note")
            elif ch == "email":
                outputs.send_email(oc.get("email_to") or "", report.header(rep), text)
                print(f"Email inviata a {oc['email_to']}")
            else:
                raise outputs.OutputError(f"Canale sconosciuto: {ch} (validi: notification, note, email)")
        except outputs.OutputError as exc:
            log.error("%s", exc)
            failures += 1

    if oc.get("mark_reminders"):
        list_ids = sorted({i.list_id for i in items if i.list_id}) or [x for x in [cfg["reminders"].get("list_id")] if x]
        if from_file:
            log.warning("Lista letta da file: etichette sui promemoria non aggiornate")
        elif not list_ids:
            log.warning("Nessun identificativo di lista: etichette sui promemoria non aggiornate")
        else:
            try:
                n = reminders.mark(report.mark_texts(rep), list_ids, marker=oc["marker"],
                                   **pipeline.helper_paths(cfg))
                print(f"Promemoria aggiornati: {n}")
            except reminders.RemindersError as exc:
                log.error("Etichette sui promemoria: %s", exc)
                failures += 1
    return failures


def cmd_run(cfg: dict, args) -> int:
    oc = cfg["output"]
    warnings = []
    today = pipeline.todays_cache(cfg)
    if args.offers_json:
        fetched_at, offers = pipeline.load_offers(cfg, args.offers_json)
        warnings.append(f"Offerte da {args.offers_json.name}, non scaricate da Everli")
    elif args.offline:
        fetched_at, offers = pipeline.load_offers(cfg)
    elif today and not args.refresh:
        log.info("Offerte di oggi già in cache (%s): nessuna richiesta a Everli", today.name)
        fetched_at, offers = pipeline.load_offers(cfg, today)
    else:
        try:
            _, _, _, path = pipeline.fetch_offers(cfg)
        except ConnectionFailed as exc:
            # Al risveglio dal sonno la rete può non essere ancora pronta: un solo nuovo tentativo.
            log.warning("%s: riprovo una volta tra %d s", exc, RETRY_DELAY_S)
            time.sleep(RETRY_DELAY_S)
            _, _, _, path = pipeline.fetch_offers(cfg)
        fetched_at, offers = pipeline.load_offers(cfg, path)
    age = _age_days(fetched_at)
    if age is not None and age > 7 and not args.offers_json:
        warnings.append(f"Le offerte sono di {age} giorni fa: potrebbero essere scadute")

    items = pipeline.load_items(cfg, args.reminders_json)
    results, _ = pipeline.run_matching(cfg, offers, items, args.llm)
    pipeline.save_match(cfg, fetched_at, results)

    rep = report.build(
        results,
        title=oc["title"],
        offers_date=fetched_at,
        store=cfg["everli"]["store"].get("name") or "",
        threshold=float(oc.get("min_discount_pct") or 0),
        max_per_item=int(oc.get("max_per_item") or 2),
    )
    rep.warnings = warnings
    text, html = report.to_text(rep), report.to_html(rep)
    out_dir = config.resolve(cfg["paths"]["cache_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"{datetime.now():%Y-%m-%d}"
    (out_dir / f"report-{stamp}.txt").write_text(text, encoding="utf-8")
    (out_dir / f"report-{stamp}.html").write_text(html, encoding="utf-8")
    print(text)

    if args.dry_run:
        print("Prova (--dry-run): nessuna notifica, nota, email o etichetta.")
        return 0
    failures = _deliver(cfg, rep, text, html, items, from_file=args.reminders_json is not None)
    log.info("run ok: %d voci in offerta, %d canali falliti", len(rep.lines), failures)
    return EXIT_ERROR if failures else 0


def cmd_schedule(cfg: dict, args) -> int:
    if args.action == "install":
        path = schedule.install(cfg, log_dir=config.resolve(cfg["paths"]["log_dir"]))
        weekday, hour, minute = schedule.timing(cfg)
        print(f"LaunchAgent installato: {path}")
        print(f"Esecuzione {schedule.describe(weekday, hour, minute)} · "
              f"prossima: {schedule.next_run(weekday, hour, minute):%d/%m/%Y %H:%M}")
        print("Prova subito sotto launchd con: .venv/bin/python -m evershopper schedule run-now")
    elif args.action == "uninstall":
        removed = schedule.uninstall()
        print("LaunchAgent rimosso" if removed else "LaunchAgent non installato")
    elif args.action == "run-now":
        schedule.run_now()
        print("Avviato: segui l'esecuzione con  tail -f logs/launchd.log logs/evershopper.log")
    else:
        info = schedule.status()
        if not info["installed"]:
            print("LaunchAgent non installato: .venv/bin/python -m evershopper schedule install")
            return EXIT_ERROR
        cal = info["plist"]["StartCalendarInterval"]
        weekday, hour, minute = cal["Weekday"], cal["Hour"], cal["Minute"]
        print(f"Installato: {schedule.describe(weekday, hour, minute)} · "
              f"prossima: {schedule.next_run(weekday, hour, minute):%d/%m/%Y %H:%M}")
        print(f"Caricato in launchd: {'sì' if info['loaded'] else 'no'}"
              + (f" · stato: {info['state']}" if info.get("state") else ""))
        if info.get("runs"):
            code = info.get("last_exit", "?")
            print(f"Esecuzioni: {info['runs']} · ultimo codice di uscita: {code} ({EXIT_MEANING.get(code, '?')})")
        if (weekday, hour, minute) != schedule.timing(cfg):
            print("Attenzione: config.yaml ha un orario diverso, rilancia `schedule install` per applicarlo")
    return 0


def _jwt_like_key(state: dict | None) -> str:
    """Nome della chiave di localStorage che sembra contenere un token (per l'header Authorization)."""
    for origin in (state or {}).get("origins", []):
        for item in origin.get("localStorage", []):
            name, value = item.get("name", ""), str(item.get("value", ""))
            if re.fullmatch(r"[\w-]+\.[\w-]+\.[\w-]+", value.strip('"')) or (
                    re.search("token|auth", name, re.I) and len(value) > 20):
                return name
    return ""


def cmd_autoconfig(cfg: dict, args) -> int:
    capture_dir = config.ROOT / "discovery" / "captures"
    capture = args.capture or autoconfig.latest_capture(capture_dir)
    records = autoconfig.load_records(capture, config.ROOT)
    prop = autoconfig.propose(records)
    ep = prop.endpoint
    if ep["auth"]["type"] == "local_storage_bearer":
        kc = cfg["everli"]["keychain"]
        key = _jwt_like_key(keychain.load_session(kc["service"], kc["session_account"]))
        ep["auth"]["key"] = key
        prop.notes.append(f"Token letto da localStorage, chiave «{key}»." if key else
                          "Chiave del token in localStorage non trovata: va indicata in endpoint.auth.key.")
    q = autoconfig.quality(prop)

    print(f"Cattura: {capture.name}")
    print(f"Endpoint: {ep['method']} {ep['url']}  ({prop.calls} chiamate, {len(prop.pages)} pagine diverse)")
    pag = ep["pagination"]
    print("Paginazione: " + ("nessuna" if pag["type"] == "none" else
          f"{pag['type']} con «{pag['param']}» da {pag['start']}, ~{pag['size']} per pagina"))
    print(f"Lista prodotti: {ep['items_path']}  ·  prezzi in {'centesimi' if ep['price_divisor'] == 100 else 'euro'}"
          f"  ·  ordinate per sconto: {'sì' if ep['sorted_by_discount'] else 'no'}")
    print("Campi:")
    for k, v in ep["fields"].items():
        print(f"  {k:<17} {v or '—'}")
    for n in prop.notes:
        print(f"Nota: {n}")
    print(f"\nVerifica sulle risposte catturate: {q['offers']} offerte · con prezzo {q['with_prices']:.0%}"
          f" · con prezzo pieno {q['with_full']:.0%} · con sconto {q['with_discount']:.0%}"
          f" · prezzi coerenti {q['consistent']:.0%} · sospette (prezzo 0 o sconto ≥95%) {q['suspicious']:.0%}")
    _print_offers(prop.offers, args.limit)
    print("\nStruttura di un prodotto (campo, tipo, esempio):")
    print("\n".join(autoconfig.describe_item(prop.sample_item)))

    ok = q["offers"] > 0 and q["with_prices"] >= 0.8 and q["consistent"] >= 0.9 and q["suspicious"] < 0.05
    if not ok:
        print("\nIl risultato non è affidabile: non scrivo config.yaml. Incolla questo output a Claude.")
        return EXIT_ERROR
    if args.dry_run:
        print("\nProva (--dry-run): config.yaml non modificato.")
        return 0

    path = args.config or config.DEFAULT
    current = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    current = current or {}
    if path.exists():
        path.with_name(path.name + ".bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    current.setdefault("everli", {})["endpoint"] = ep
    path.write_text("# Endpoint ricavato da `python -m evershopper autoconfig`; il resto viene da config.example.yaml\n"
                    + yaml.safe_dump(current, sort_keys=False, allow_unicode=True), encoding="utf-8")
    doc = config.ROOT / "docs" / "everli-api.md"
    doc.write_text(autoconfig.api_doc(prop, capture.name, f"{datetime.now():%Y-%m-%d}"), encoding="utf-8")
    print(f"\nScritti {path.name}" + (" (copia del precedente in .bak)" if path.with_name(path.name + '.bak').exists() else "")
          + " e docs/everli-api.md")
    print("Prossimo passo: .venv/bin/python -m evershopper fetch   (scarica le offerte vere, poche richieste)")
    return 0


def cmd_doctor(cfg: dict, args) -> int:
    return 0 if doctor.run(cfg) else EXIT_ERROR


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
    p_run = sub.add_parser("run", help="tutto il flusso: offerte, lista, abbinamento, riepilogo")
    p_run.add_argument("--offline", action="store_true", help="usa l'ultima cache, senza contattare Everli")
    p_run.add_argument("--refresh", action="store_true", help="riscarica anche se c'è già la cache di oggi")
    p_run.add_argument("--dry-run", action="store_true", help="mostra il riepilogo senza inviarlo")
    p_run.add_argument("--offers-json", type=Path, metavar="FILE",
                       help="offerte da un file (es. examples/offerte-esempio.json), senza Everli")
    p_run.add_argument("--reminders-json", type=Path, metavar="FILE", help="lista da un file JSON")
    p_run.add_argument("--llm", dest="llm", action="store_true", default=None, help="forza la seconda passata")
    p_run.add_argument("--no-llm", dest="llm", action="store_false", help="salta la seconda passata")
    p_sched = sub.add_parser("schedule", help="esecuzione settimanale con launchd")
    p_sched.add_argument("action", choices=["install", "uninstall", "status", "run-now"])
    sub.add_parser("doctor", help="controlla configurazione, permessi, Portachiavi e launchd")
    p_auto = sub.add_parser("autoconfig", help="ricava l'endpoint delle offerte dall'ultima discovery")
    p_auto.add_argument("--capture", type=Path, metavar="FILE", help="cattura da usare (default: l'ultima)")
    p_auto.add_argument("--dry-run", action="store_true", help="mostra la proposta senza scrivere config.yaml")
    p_auto.add_argument("--limit", type=int, default=10, help="offerte mostrate in anteprima")
    args = parser.parse_args(argv)

    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ERROR
    logs.setup(config.resolve(cfg["paths"]["log_dir"]), args.verbose)

    try:
        return {"fetch": cmd_fetch, "show": cmd_show, "reminders": cmd_reminders, "match": cmd_match,
                "run": cmd_run, "schedule": cmd_schedule, "doctor": cmd_doctor,
                "autoconfig": cmd_autoconfig}[args.cmd](cfg, args)
    except SessionExpired as exc:
        log.warning("Sessione scaduta: %s", exc)
        notify("Offerte Everli", "Sessione Everli scaduta: rifai il login con discovery/discover.py --login")
        return EXIT_SESSION_EXPIRED
    except reminders.RemindersAccessDenied as exc:
        log.error("%s", exc)
        notify("Offerte Everli", "Accesso a Promemoria negato: controlla Privacy e sicurezza")
        return EXIT_ERROR
    except reminders.RemindersError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except autoconfig.AutoconfigError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except schedule.ScheduleError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    except EverliError as exc:
        log.error("%s", exc)
        notify("Offerte Everli", f"Errore: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
