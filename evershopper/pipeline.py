"""I passi del flusso (offerte, lista, abbinamento), condivisi dai comandi della CLI."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from . import cache, config, keychain, llm, matching, reminders
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

log = logging.getLogger("evershopper")


def fetch_offers(cfg: dict, from_json: list[str] | None = None, save: bool = True):
    """Scarica (o legge da file) le offerte. Restituisce (offerte, pagine, richieste, file di cache)."""
    ev = cfg["everli"]
    ep, store_id = ev["endpoint"], str(ev["store"].get("id") or "")
    check_endpoint(ep, store_id)

    if from_json:
        pages = [json.loads(Path(p).read_text(encoding="utf-8")) for p in from_json]
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
    path = None
    if save:
        path = cache.save(
            config.resolve(cfg["paths"]["cache_dir"]),
            offers,
            pages,
            {"store": ev["store"], "source": source, "requests": n_requests, "pages": len(pages)},
        )
    log.info("fetch ok: %d offerte, %d richieste", len(offers), n_requests)
    return offers, pages, n_requests, path


def load_offers(cfg: dict, path: Path | None = None) -> tuple[str, list[Offer]]:
    """Offerte da un file di cache o dall'ultima cache. Restituisce (data, offerte)."""
    if path:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload.get("fetched_at", path.name), [Offer.from_dict(o) for o in payload["offers"]]
    latest = cache.load_latest(config.resolve(cfg["paths"]["cache_dir"]))
    if not latest:
        raise EverliError("Nessuna offerta in cache: lancia prima `python -m evershopper fetch`")
    payload, offers = latest
    return payload["fetched_at"], offers


def todays_cache(cfg: dict) -> Path | None:
    path = config.resolve(cfg["paths"]["cache_dir"]) / f"offers-{datetime.now():%Y-%m-%d}.json"
    return path if path.exists() else None


def helper_paths(cfg: dict) -> dict:
    rc = cfg["reminders"]
    return {"backend": rc["backend"], "app": config.resolve(rc["app"]), "helper": config.resolve(rc["helper"])}


def load_items(cfg: dict, path: Path | None = None) -> list[reminders.ShoppingItem]:
    if path:
        return reminders.read_json(path)
    rc = cfg["reminders"]
    return reminders.read_list(rc["list"], list_id=rc.get("list_id") or None, **helper_paths(cfg))


def run_matching(cfg: dict, offers: list[Offer], items: list, use_llm: bool | None = None):
    mc = cfg["matching"]
    matcher = matching.Matcher(mc.get("synonyms"), mc.get("exclude"), int(mc.get("max_doubts", 8)))
    results = matcher.match(items, offers)

    lc = mc["llm"]
    use_llm = lc["enabled"] if use_llm is None else use_llm
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
    return results, use_llm


def save_match(cfg: dict, fetched_at: str, results: list) -> Path:
    out_dir = config.resolve(cfg["paths"]["cache_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"match-{datetime.now():%Y-%m-%d}.json"
    out.write_text(json.dumps({"offers_fetched_at": fetched_at, "results": [r.to_dict() for r in results]},
                              ensure_ascii=False, indent=2), encoding="utf-8")
    return out
