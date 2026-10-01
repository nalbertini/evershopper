"""Scarica e normalizza le offerte secondo la descrizione dell'endpoint in config.yaml."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from ..models import Offer
from .client import EverliClient, EverliError

log = logging.getLogger(__name__)

_MISSING = object()


class EndpointNotConfigured(EverliError):
    pass


def get_path(obj: Any, path: str, default: Any = None) -> Any:
    """Legge un percorso a punti, es. 'price.original' o 'images.0.url'; '.' è l'oggetto stesso."""
    if not path:
        return default
    if path == ".":
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part, _MISSING)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            idx = int(part)
            cur = cur[idx] if -len(cur) <= idx < len(cur) else _MISSING
        else:
            cur = _MISSING
        if cur is _MISSING:
            return default
    return cur


def parse_number(value: Any, divisor: float = 1) -> float | None:
    """Accetta 129, 1.29, '1,29 €', '€ 1.299,00', '-20%'."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return round(value / divisor, 2)
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return round(float(s) / divisor, 2)
    except ValueError:
        return None


def parse_offer(item: dict, ep: dict) -> Offer | None:
    fields, div = ep["fields"], ep.get("price_divisor") or 1

    def f(name):
        return get_path(item, fields.get(name) or "")

    name = f("name")
    if not name:
        return None
    full = parse_number(f("price_full"), div)
    disc = parse_number(f("price_discounted"), div)
    pct = parse_number(f("discount_pct"))
    if pct is not None:
        pct = abs(pct)
    elif full and disc is not None and full > disc:
        pct = round((full - disc) / full * 100, 1)
    if full is None and disc and pct and 0 < pct < 100:
        # Solo prezzo scontato e percentuale: il prezzo pieno si ricava.
        full = round(disc / (1 - pct / 100), 2)

    oid = f("id")
    url = f("url")
    template = ep.get("product_url_template") or ""
    if template and ("{url}" in template and url or "{url}" not in template and not url and oid is not None):
        # {url} completa un link relativo ("/p/123"), {id} costruisce il link dall'identificativo.
        url = template.format(id=oid, url=url or "")
    until = f("valid_until")
    if isinstance(until, (int, float)) and not isinstance(until, bool) and until > 1e9:
        until = datetime.fromtimestamp(until / 1000 if until > 1e12 else until, timezone.utc).date().isoformat()
    brand = f("brand")
    return Offer(
        id=str(oid if oid is not None else name),
        name=str(name).strip(),
        brand=str(brand).strip() if brand else None,
        format=str(f("format")).strip() if f("format") else None,
        price_full=full,
        price_discounted=disc,
        discount_pct=pct,
        valid_until=str(until) if until else None,
        url=url,
    )


def _fill(value: Any, store_id: str) -> Any:
    if isinstance(value, str):
        return value.replace("{store_id}", store_id)
    if isinstance(value, dict):
        return {k: _fill(v, store_id) for k, v in value.items()}
    return value


def check_endpoint(ep: dict, store_id: str) -> None:
    missing = [k for k in ("url", "items_path") if not ep.get(k)]
    if not (ep.get("fields") or {}).get("name"):
        missing.append("fields.name")
    if "{store_id}" in str(ep.get("url", "")) + str(ep.get("params", "")) and not store_id:
        missing.append("store.id")
    if missing:
        raise EndpointNotConfigured(
            "Endpoint Everli da definire in config.yaml (vedi docs/everli-api.md): manca "
            + ", ".join(missing)
        )


def iter_pages(client: EverliClient, ep: dict, store_id: str):
    """Restituisce (numero_pagina, json) una pagina alla volta, rispettando il tetto."""
    pag = ep.get("pagination") or {}
    ptype = pag.get("type", "none")
    url = _fill(ep["url"], store_id)
    base_params = _fill(dict(ep.get("params") or {}), store_id)
    body = _fill(ep.get("body") or None, store_id)
    headers = dict(ep.get("headers") or {})
    size = int(pag.get("size") or 0)
    if pag.get("size_param") and size:
        base_params[pag["size_param"]] = size
    max_pages = int(pag.get("max_pages") or 1) if ptype != "none" else 1
    cursor = int(pag.get("start", 1 if ptype == "page" else 0))

    for n in range(max_pages):
        if n and client.remaining <= 0:
            log.warning("Tetto di richieste raggiunto dopo %d pagine: risultati parziali", n)
            return
        params = dict(base_params)
        if ptype in ("page", "offset"):
            target = body if ep.get("method", "GET").upper() == "POST" and body is not None else params
            target[pag.get("param", ptype)] = cursor
        data = client.fetch_json(ep.get("method", "GET").upper(), url, params, headers, body)
        yield n + 1, data

        items = get_path(data, ep["items_path"]) or []
        last_page = get_path(data, pag.get("last_page_path") or "")
        if ptype == "none" or not items or (size and len(items) < size):
            return
        if ptype == "page":
            if last_page is not None and cursor >= int(last_page):
                return
            cursor += 1
        else:
            cursor += len(items)
    log.info("Raggiunto max_pages=%d", max_pages)


def offers_from_pages(pages: list[Any], ep: dict) -> list[Offer]:
    offers: dict[str, Offer] = {}
    for data in pages:
        items = get_path(data, ep["items_path"])
        if not isinstance(items, list):
            raise EverliError(f"items_path '{ep['items_path']}' non punta a una lista")
        for item in items:
            offer = parse_offer(item, ep) if isinstance(item, dict) else None
            if offer:
                offers.setdefault(offer.id, offer)
    return sorted(offers.values(), key=lambda o: -(o.discount_pct or 0))
