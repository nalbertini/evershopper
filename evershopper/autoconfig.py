"""Ricava la descrizione dell'endpoint delle offerte da una cattura di discover.py.

Tutto avviene in locale sui file già salvati (discovery/captures/): nessuna
richiesta a Everli. Il risultato è una proposta per config.yaml → everli.endpoint,
verificata facendo girare il parser vero sulle risposte catturate.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlsplit

from .everli.offers import get_path, offers_from_pages, parse_number, parse_offer
from .models import Offer

OFFER_HINTS = re.compile(r"(price|prezzo|discount|sconto|promo|offer|offert)", re.I)
NUMERIC_SEGMENT = re.compile(r"/\d+(?=/|$)")

PAGE_PARAMS = ("page", "p", "pagina", "page_number", "pagenumber", "pageindex", "page_index")
OFFSET_PARAMS = ("offset", "skip", "start", "from")
SIZE_PARAMS = ("per_page", "perpage", "limit", "size", "page_size", "pagesize", "count", "take", "rows")
LAST_PAGE_KEYS = ("last_page", "lastpage", "total_pages", "totalpages", "pages", "page_count", "pagecount")

FULL_WORDS = re.compile(r"(original|full|regular|old|list|strike|before|initial|base|was|undiscount|pieno|listino|previous)")
DISC_WORDS = re.compile(r"(discounted|discount_price|offer|promo|sale|final|current|special|now|scontat|actual|selling)")
# Prezzi al kg/litro/unità ("price_per_type", "unit_price", "price_per_kg"…): non sono il prezzo del prodotto.
PER_UNIT = re.compile(r"(per_|_per\b|unit|_kg|kilo|liter|litre|litro|unitario|reference|measure|al_kg|al_litro)")
PCT_WORDS = re.compile(r"(percent|pct|perc|percentage|%|sconto|discount|saving|off)")
DATE_WORDS = re.compile(r"(end|until|expir|valid_?to|to_?date|fine|scadenza|ends|valid_?until|stop)")
DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}")
FORMAT_VALUE = re.compile(r"\d\s*(g|gr|kg|ml|cl|l|lt|pz|x|conf|rotoli|capsule)\b", re.I)

NAME_KEYS = ("name", "title", "display_name", "displayname", "product_name", "productname", "label", "nome")
ID_KEYS = ("id", "product_id", "productid", "sku", "ean", "code", "uuid", "item_id")
FORMAT_KEYS = ("size", "format", "weight", "quantity", "packaging", "unit_label", "measure", "measurement",
               "content", "formato", "peso", "grammage", "volume", "package", "quantity_label", "subtitle")
URL_KEYS = ("url", "link", "permalink", "href", "share_url", "web_url", "slug_url", "path")

# Header inviati dal browser che non servono (o che Playwright imposta da sé).
SKIP_HEADERS = re.compile(r"^(sec-|user-agent$|accept-encoding$|content-length$|host$|connection$|cookie$|priority$)", re.I)


@dataclass
class Proposal:
    endpoint: dict
    store: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    pages: list[Any] = field(default_factory=list)
    offers: list[Offer] = field(default_factory=list)
    calls: int = 0
    sample_item: dict = field(default_factory=dict)


class AutoconfigError(Exception):
    pass


# --- lettura della cattura -------------------------------------------------

def latest_capture(capture_dir: Path) -> Path:
    files = sorted(capture_dir.glob("capture-*.jsonl"))
    if not files:
        raise AutoconfigError("Nessuna cattura trovata: lancia prima discovery/discover.py")
    return files[-1]


def load_records(path: Path, root: Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        body = None
        if rec.get("body_file"):
            f = root / rec["body_file"]
            if f.exists():
                try:
                    body = json.loads(f.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    body = None
        if body is None and rec.get("body_sample"):
            try:
                body = json.loads(rec["body_sample"])
            except json.JSONDecodeError:
                body = None
        rec["body"] = body
        records.append(rec)
    return records


# --- endpoint e lista dei prodotti -----------------------------------------

def find_item_lists(obj: Any, path: str = "", depth: int = 0) -> list[tuple[str, list]]:
    """Tutte le liste di oggetti nel JSON, con il loro percorso."""
    found = []
    if isinstance(obj, list):
        if len(obj) >= 2 and all(isinstance(x, dict) for x in obj[:5]):
            found.append((path or ".", obj))
        if depth < 4 and obj and isinstance(obj[0], (dict, list)):
            found += find_item_lists(obj[0], f"{path}.0" if path else "0", depth + 1)
    elif isinstance(obj, dict) and depth < 5:
        for k, v in obj.items():
            found += find_item_lists(v, f"{path}.{k}" if path else k, depth + 1)
    return found


def _list_score(items: list) -> float:
    text = json.dumps(items[:10], ensure_ascii=False)
    return len(items) * (1 + len(OFFER_HINTS.findall(text)) / 10)


def best_item_list(body: Any) -> tuple[str, list] | None:
    lists = [(p, items) for p, items in find_item_lists(body) if OFFER_HINTS.search(json.dumps(items[:3]))]
    if not lists:
        return None
    # Una lista di sezioni (ognuna con la sua lista di prodotti) va scavalcata: si sceglie la più "ricca".
    return max(lists, key=lambda pi: _list_score(pi[1]))


def choose_endpoint(records: list[dict]) -> tuple[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for rec in records:
        if rec.get("status") != 200 or "json" not in (rec.get("content_type") or ""):
            continue
        if not isinstance(rec.get("body"), (dict, list)):
            continue
        groups[(rec["method"], NUMERIC_SEGMENT.sub("/{id}", rec["endpoint"]))].append(rec)

    def score(item):
        (_, url), recs = item
        with_list = [r for r in recs if best_item_list(r["body"])]
        if not with_list:
            return 0
        size = median(len(best_item_list(r["body"])[1]) for r in with_list)
        bonus = 3 if re.search(r"(offer|promo|offert|discount|sconto)", url, re.I) else 1
        return len(with_list) * size * bonus

    ranked = sorted(groups.items(), key=score, reverse=True)
    if not ranked or score(ranked[0]) == 0:
        raise AutoconfigError("Nessun endpoint JSON con una lista di prodotti con prezzi nella cattura")
    return ranked[0]


# --- paginazione e parametri -----------------------------------------------

def _int(v) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def request_params(rec: dict) -> dict:
    """Parametri della query, più quelli del corpo JSON per le POST."""
    params = dict(rec.get("params") or {})
    if rec.get("method") == "POST" and rec.get("post_data"):
        try:
            body = json.loads(rec["post_data"])
            if isinstance(body, dict):
                params.update({k: v for k, v in body.items() if not isinstance(v, (dict, list))})
        except json.JSONDecodeError:
            pass
    return params


def infer_pagination(recs: list[dict], notes: list[str]) -> tuple[dict, dict, str | None]:
    values: dict[str, list] = defaultdict(list)
    for r in recs:
        for k, v in request_params(r).items():
            values[k].append(v)

    pag = {"type": "none", "param": "page", "start": 1, "size_param": "", "size": 0,
           "last_page_path": "", "max_pages": 20}
    page_param = None
    varying = {k: vs for k, vs in values.items() if len(set(map(str, vs))) > 1}
    numeric = {k: vs for k, vs in varying.items() if all(_int(v) is not None for v in vs)}

    def pick(names):
        return next((k for k in numeric if k.lower() in names), None)

    if (k := pick(PAGE_PARAMS)) or (numeric and not pick(OFFSET_PARAMS) and len(numeric) == 1):
        page_param = k or next(iter(numeric))
        nums = sorted({_int(v) for v in numeric[page_param]})
        is_offset = page_param.lower() in OFFSET_PARAMS or (len(nums) > 1 and nums[1] - nums[0] > 1)
        pag.update(type="offset" if is_offset else "page", param=page_param, start=nums[0])
    elif k := pick(OFFSET_PARAMS):
        page_param = k
        pag.update(type="offset", param=k, start=min(_int(v) for v in numeric[k]))
    if page_param and any(page_param not in request_params(r) for r in recs):
        # La prima pagina spesso non ha il parametro (es. niente "skip"): si parte dall'inizio.
        pag["start"] = 0 if pag["type"] == "offset" else 1
    elif varying:
        notes.append("Il parametro che cambia tra le chiamate non è numerico (forse un cursore): "
                     f"{', '.join(varying)}. Uso solo la prima pagina.")
    else:
        notes.append("Tutte le chiamate avevano gli stessi parametri: niente paginazione rilevata, uso una pagina.")

    fixed = {}
    for k, vs in values.items():
        if k == page_param:
            continue
        common, n = Counter(map(str, vs)).most_common(1)[0]
        if len(set(map(str, vs))) > 1:
            notes.append(f"Il parametro «{k}» cambia tra le chiamate: uso il valore più frequente ({common}).")
        fixed[k] = next(v for v in vs if str(v) == common)
        if k.lower() in SIZE_PARAMS and _int(common):
            pag.update(size_param=k, size=_int(common))
    return pag, fixed, page_param


def find_last_page_path(body: Any) -> str:
    def walk(obj, path, depth):
        if isinstance(obj, dict) and depth < 3:
            for k, v in obj.items():
                p = f"{path}.{k}" if path else k
                if k.lower() in LAST_PAGE_KEYS and _int(v):
                    return p
                if isinstance(v, dict):
                    found = walk(v, p, depth + 1)
                    if found:
                        return found
        return ""
    return walk(body, "", 0)


# --- campi -----------------------------------------------------------------

def flatten(obj: Any, prefix: str = "", depth: int = 0) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict) and depth < 4:
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(flatten(v, p, depth + 1))
            elif isinstance(v, list) and v and isinstance(v[0], dict) and depth < 3:
                out.update(flatten(v[0], f"{p}.0", depth + 1))
            elif not isinstance(v, list):
                out[p] = v
    return out


def _last(path: str) -> str:
    return path.rsplit(".", 1)[-1].lower()


def _share(values: list, test) -> float:
    return sum(1 for v in values if test(v)) / max(len(values), 1)


def infer_fields(items: list[dict], notes: list[str]) -> tuple[dict, float]:
    flat = [flatten(it) for it in items[:50]]
    paths: dict[str, list] = defaultdict(list)
    for f in flat:
        for k, v in f.items():
            paths[k].append(v)
    n = len(flat)
    present = {p: vs for p, vs in paths.items() if len(vs) >= max(1, n * 0.6)}

    def is_text(v):
        return isinstance(v, str) and v.strip() != ""

    def is_num(v):
        return parse_number(v) is not None and not isinstance(v, bool)

    fields = {k: "" for k in ("id", "name", "brand", "format", "price_full", "price_discounted",
                              "discount_pct", "valid_until", "url")}

    def by_keys(keys, test, exclude=()):
        for key in keys:
            cands = [p for p, vs in present.items() if _last(p) == key and p not in exclude and _share(vs, test) > 0.8]
            if cands:
                return min(cands, key=lambda p: p.count("."))  # il più vicino alla radice
        return ""

    fields["id"] = by_keys(ID_KEYS, lambda v: v not in (None, ""))
    fields["name"] = by_keys(NAME_KEYS, lambda v: is_text(v) and len(v) >= 3)
    if not fields["name"]:
        texts = [(p, vs) for p, vs in present.items() if _share(vs, is_text) > 0.8 and "url" not in p.lower()]
        if texts:
            fields["name"] = max(texts, key=lambda pv: len(set(pv[1])) * median(len(str(v)) for v in pv[1]))[0]
    brands = [p for p, vs in present.items() if "brand" in p.lower() or "marca" in p.lower()]
    brands = [p for p in brands if _share(present[p], is_text) > 0.5]
    if brands:
        fields["brand"] = next((p for p in brands if _last(p) == "name"), min(brands, key=len))
    fields["format"] = by_keys(FORMAT_KEYS, lambda v: is_text(v) and bool(FORMAT_VALUE.search(v)),
                               exclude=(fields["name"],))
    fields["url"] = by_keys(URL_KEYS, lambda v: is_text(v) and "/" in v)

    # Percentuale di sconto: serve anche a capire quale coppia di prezzi è quella giusta.
    pct = [p for p, vs in present.items() if PCT_WORDS.search(_last(p)) and not re.search("price|prezzo", _last(p))
           and _share(vs, lambda v: is_num(v) and 0 < abs(parse_number(v)) < 100) > 0.7]
    if pct:
        fields["discount_pct"] = min(pct, key=len)

    # Prezzi: candidati numerici con "price"/"prezzo"/… nel percorso, esclusi quelli al kg/litro/unità.
    prices = [p for p, vs in present.items()
              if re.search(r"(price|prezzo|cost|amount|importo|value|valore)", p, re.I)
              and not PER_UNIT.search(p.lower()) and not PCT_WORDS.search(_last(p))
              and _share(vs, is_num) > 0.7]

    def agreement(full_p: str, disc_p: str) -> float:
        """Quota di prodotti in cui (pieno - scontato) / pieno coincide con la percentuale indicata."""
        ok = tot = 0
        for f in flat:
            pc, a, b = (parse_number(f.get(fields["discount_pct"])), parse_number(f.get(full_p)),
                        parse_number(f.get(disc_p)))
            if pc is None or a is None or b is None or not a or not 0 < abs(pc) < 100:
                continue
            tot += 1
            ok += abs((a - b) / a * 100 - abs(pc)) <= 1.5
        return ok / tot if tot >= 3 else 0.0

    best = None
    if fields["discount_pct"] and len(prices) >= 2:
        best = max(((agreement(a, b), a, b) for a in prices for b in prices if a != b), default=None)
        if best and best[0] >= 0.6:
            fields["price_full"], fields["price_discounted"] = best[1], best[2]
            notes.append(f"Prezzi scelti perché coerenti con «{fields['discount_pct']}» nel {best[0]:.0%} dei prodotti.")
        else:
            best = None
    if best is None:
        full = [p for p in prices if FULL_WORDS.search(p.lower())]
        disc = [p for p in prices if DISC_WORDS.search(p.lower()) and p not in full]
        plain = [p for p in prices if p not in full and p not in disc]
        fields["price_full"] = min(full, key=len) if full else ""
        fields["price_discounted"] = min(disc, key=len) if disc else (min(plain, key=len) if plain else "")
        if fields["discount_pct"] and fields["price_discounted"] and (
                not fields["price_full"] or agreement(fields["price_full"], fields["price_discounted"]) < 0.6):
            # Un solo prezzo affidabile più la percentuale: il prezzo pieno si ricava dallo sconto.
            fields["price_full"] = ""
            notes.append("Prezzo pieno non trovato: lo ricavo da prezzo scontato e percentuale di sconto.")
        elif not fields["price_full"] and len(plain) >= 2:
            # Due prezzi senza nome chiaro: il pieno è quello mediamente più alto.
            a, b = sorted(plain, key=len)[:2]
            hi = median(parse_number(v) for v in present[a] if is_num(v)) >= median(parse_number(v) for v in present[b] if is_num(v))
            fields["price_full"], fields["price_discounted"] = (a, b) if hi else (b, a)

    divisor = 1.0
    discs = [parse_number(v) for v in present.get(fields["price_discounted"], []) if is_num(v)]
    raw = present.get(fields["price_discounted"], [])
    if discs and all(isinstance(v, int) for v in raw) and median(discs) >= 50:
        divisor = 100.0
        notes.append("Prezzi interi e alti: li interpreto come centesimi (price_divisor 100).")

    if fields["price_full"] and fields["price_discounted"]:
        pairs = [(parse_number(a), parse_number(b)) for a, b in zip(present[fields["price_full"]],
                                                                    present[fields["price_discounted"]])]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        if pairs and sum(1 for a, b in pairs if a < b) > len(pairs) / 2:
            fields["price_full"], fields["price_discounted"] = fields["price_discounted"], fields["price_full"]

    dates = [p for p, vs in present.items() if DATE_WORDS.search(_last(p))
             and _share(vs, lambda v: (isinstance(v, str) and bool(DATE_VALUE.match(v)))
                        or (isinstance(v, (int, float)) and not isinstance(v, bool) and v > 1e9)) > 0.7]
    if dates:
        fields["valid_until"] = min(dates, key=len)

    for k in ("name", "price_discounted"):
        if not fields[k]:
            notes.append(f"Campo «{k}» non trovato: va indicato a mano in config.yaml.")
    return fields, divisor


# --- proposta completa -----------------------------------------------------

def propose(records: list[dict]) -> Proposal:
    (method, url_pattern), recs = choose_endpoint(records)
    notes: list[str] = []
    if "{id}" in url_pattern:
        ids = Counter(re.findall(r"/(\d+)(?=/|$)", recs[0]["endpoint"]))
        notes.append(f"L'URL contiene un identificativo numerico ({', '.join(ids)}): uso quello della prima chiamata.")
    url = recs[0]["endpoint"]

    pag, fixed, page_param = infer_pagination(recs, notes)
    sample = next(r["body"] for r in recs if best_item_list(r["body"]))
    items_path, items = best_item_list(sample)
    fields, divisor = infer_fields(items, notes)
    pag["last_page_path"] = find_last_page_path(sample) if pag["type"] == "page" else ""
    sizes = [len(get_path(r["body"], items_path) or []) for r in recs if isinstance(get_path(r["body"], items_path), list)]
    if not pag["size"] and sizes:
        pag["size"] = max(sizes)  # serve solo a capire quando una pagina è l'ultima

    headers = {}
    auth = {"type": "cookie", "key": ""}
    for k, v in (recs[0].get("request_headers") or {}).items():
        if k.lower() == "authorization":
            auth = {"type": "local_storage_bearer", "key": ""}
            notes.append("Le chiamate usano un header Authorization: serve la chiave in localStorage (vedi sotto).")
        elif v != "<redacted>" and not SKIP_HEADERS.match(k) and not k.startswith(":"):
            if k.lower().startswith("x-") or k.lower() in ("accept", "accept-language", "referer", "origin"):
                headers[k.lower()] = v

    parts = urlsplit(url)
    url_tmpl = ""
    if fields["url"]:
        rel = [v for v in (get_path(it, fields["url"]) for it in items) if isinstance(v, str)]
        if rel and all(v.startswith("/") for v in rel):
            url_tmpl = f"{parts.scheme}://{parts.netloc}{{url}}"

    endpoint = {
        "url": url,
        "method": method,
        "params": fixed if method == "GET" else {},
        "body": fixed if method == "POST" else {},
        "headers": headers,
        "auth": auth,
        "items_path": items_path,
        "pagination": pag,
        "fields": fields,
        "price_divisor": divisor,
        "product_url_template": url_tmpl,
        "sorted_by_discount": True,
    }

    # Verifica: il parser vero sulle risposte catturate, in ordine di pagina.
    def page_key(r):
        return _int(request_params(r).get(page_param)) if page_param else 0
    seen, pages = set(), []
    for r in sorted(recs, key=lambda r: page_key(r) or 0):
        key = json.dumps(r["body"], sort_keys=True)[:2000]
        if key not in seen and isinstance(get_path(r["body"], items_path), list):
            seen.add(key)
            pages.append(r["body"])
    offers = offers_from_pages(pages, endpoint) if fields["name"] else []
    endpoint["sorted_by_discount"] = is_sorted_by_discount(pages, endpoint)
    if not endpoint["sorted_by_discount"]:
        notes.append("Le offerte non risultano ordinate per sconto: scarico tutte le pagine (fino ai limiti).")
    if pag["type"] != "none":
        # Senza ordinamento per sconto vanno lette tutte le pagine: margine sulle pagine viste, entro il tetto.
        pag["max_pages"] = min(30, max(20, len(pages) + 5))
    return Proposal(endpoint=endpoint, notes=notes, pages=pages, offers=offers, calls=len(recs),
                    sample_item=items[0] if items else {})


def is_sorted_by_discount(pages: list, endpoint: dict) -> bool:
    """Vero se, nell'ordine delle pagine, lo sconto è (quasi sempre) non crescente."""
    seq = []
    for page in pages:
        items = get_path(page, endpoint["items_path"]) or []
        for it in items:
            o = parse_offer(it, endpoint) if isinstance(it, dict) else None
            if o and o.discount_pct is not None:
                seq.append(o.discount_pct)
    if len(seq) < 5:
        return False
    ok = sum(1 for a, b in zip(seq, seq[1:]) if b <= a + 0.5)
    return ok / (len(seq) - 1) >= 0.9


def quality(p: Proposal) -> dict:
    o = p.offers
    n = max(len(o), 1)
    return {
        # prezzo scontato a zero o sconto ≥ 95%: quasi sempre un campo sbagliato
        "suspicious": sum(1 for x in o if x.price_discounted == 0 or (x.discount_pct or 0) >= 95) / n,
        "offers": len(o),
        "with_prices": sum(1 for x in o if x.price_discounted is not None) / n,
        "with_full": sum(1 for x in o if x.price_full is not None) / n,
        "with_discount": sum(1 for x in o if x.discount_pct) / n,
        "consistent": sum(1 for x in o if x.price_full is None or x.price_discounted is None
                          or x.price_discounted <= x.price_full) / n,
    }


def describe_item(item: dict, width: int = 60) -> list[str]:
    """Campi di un prodotto con tipo ed esempio (sono dati del catalogo, non personali)."""
    out = []
    for k, v in flatten(item).items():
        text = json.dumps(v, ensure_ascii=False)
        out.append(f"  {k:<32} {type(v).__name__:<6} {text[:width] + ('…' if len(text) > width else '')}")
    return out


def api_doc(p: Proposal, capture_name: str, today: str) -> str:
    ep, pag = p.endpoint, p.endpoint["pagination"]
    rows = "\n".join(f"| {k:<16} | `{v}` |" if v else f"| {k:<16} | (non trovato) |" for k, v in ep["fields"].items())
    params = ", ".join(f"`{k}`" for k in (ep["params"] or ep["body"])) or "nessuno"
    headers = ", ".join(f"`{k}`" for k in ep["headers"]) or "nessuno"
    paging = ("una sola pagina" if pag["type"] == "none" else
              f"`{pag['param']}` ({pag['type']}, da {pag['start']})"
              + (f", dimensione `{pag['size_param']}`={pag['size']}" if pag["size_param"] else f", ~{pag['size']} per pagina")
              + (f", ultima pagina in `{pag['last_page_path']}`" if pag["last_page_path"] else ""))
    notes = "\n".join(f"- {n}" for n in p.notes) or "- nessuna"
    return f"""# API interna Everli – note di discovery

> Generato da `python -m evershopper autoconfig` sulla cattura `{capture_name}` ({today}).
> Nessun cookie, token o dato personale in questo file. La configurazione vera è in `config.yaml`.

## Endpoint delle offerte
- Metodo e URL: `{ep['method']} {ep['url']}`
- Parametri fissi: {params}
- Header aggiunti (nomi): {headers}
- Autenticazione: {ep['auth']['type']}
- Paginazione: {paging}
- Lista prodotti: `{ep['items_path']}`
- Prezzi: {'centesimi (÷100)' if ep['price_divisor'] == 100 else 'euro'}
- Chiamate osservate nella cattura: {p.calls}

## Mappatura dei campi
| Campo spec       | Campo JSON |
|------------------|------------|
{rows}

## Note
{notes}
"""
