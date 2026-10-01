import json

import pytest

from evershopper import __main__ as cli
from evershopper import autoconfig
from evershopper.everli.offers import offers_from_pages


def rec(endpoint, params, body, method="GET", post=None, ctype="application/json", headers=None):
    return {"method": method, "endpoint": endpoint, "params": params, "status": 200, "content_type": ctype,
            "request_headers": headers or {"accept": "application/json", "cookie": "<redacted>",
                                           "x-app-version": "3.1", "sec-fetch-mode": "cors"},
            "post_data": json.dumps(post) if post else None, "body": body}


def noise():
    return [
        rec("https://spesa.everli.com/api/cart", {}, {"items": [{"id": 1, "qty": 2}, {"id": 2, "qty": 1}]}),
        rec("https://spesa.everli.com/api/config", {}, {"locale": "it"}),
        rec("https://cdn.example/img.webp", {}, None, ctype="image/webp"),
    ]


# Forma A: pagine numerate, prezzi in centesimi dentro un oggetto, link relativo.
def shape_a():
    pages = []
    for page in range(1, 4):
        items = [{"id": page * 100 + i, "name": f"Prodotto {page}-{i}", "brand": {"name": "Marca"},
                  "size": "1 l", "price": {"original": 300, "current": 300 - 120 + page * 20 + i * 2,
                                           "per_kg": 999},
                  "discount_percentage": None, "valid_to": "2026-10-08", "url": f"/p/{page}{i}"}
                 for i in range(20)]
        for it in items:
            it["discount_percentage"] = round((300 - it["price"]["current"]) / 300 * 100)
        items.sort(key=lambda it: -it["discount_percentage"])
        pages.append(rec("https://spesa.everli.com/api/offers",
                         {"page": str(page), "per_page": "20", "sort": "discount"},
                         {"data": items, "meta": {"current_page": page, "last_page": 7}}))
    return pages


# Forma B: offset, prezzi in stringhe con l'euro, data di fine annidata.
def shape_b():
    out = []
    for off in (0, 24):
        items = [{"product_id": f"p{off + i}", "title": f"Passata {off + i}", "brand": "Mutti",
                  "weight": "700 g", "regular_price": "1,79 €", "final_price": f"1,{29 - i % 5} €",
                  "promo": {"ends_at": "2026-10-12T23:59:00Z"}} for i in range(24)]
        out.append(rec("https://spesa.everli.com/api/offers", {"offset": str(off), "limit": "24"},
                       {"products": items, "total": 120}))
    return out


# Forma C: POST con pagina nel corpo, sezioni annidate, prezzi decimali e prezzo al kg da ignorare.
def shape_c():
    out = []
    for page in (1, 2):
        items = [{"sku": f"s{page}{i}", "display_name": f"Pasta {i}", "price": 0.89, "old_price": 1.29,
                  "price_per_kg": 1.78, "package": "500 g"} for i in range(12)]
        out.append(rec("https://spesa.everli.com/api/offers", {}, {"sections": [{"title": "Top", "items": items}]},
                       method="POST", post={"page": page, "store": 42}))
    return out


def test_shape_a_pages_cents_relative_url():
    p = autoconfig.propose(noise() + shape_a())
    ep = p.endpoint
    assert ep["url"] == "https://spesa.everli.com/api/offers" and ep["method"] == "GET"
    assert ep["items_path"] == "data"
    assert ep["pagination"] | {} == ep["pagination"]
    pag = ep["pagination"]
    assert (pag["type"], pag["param"], pag["start"], pag["size_param"], pag["size"]) == ("page", "page", 1, "per_page", 20)
    assert pag["last_page_path"] == "meta.last_page"
    assert ep["params"] == {"per_page": "20", "sort": "discount"}
    f = ep["fields"]
    assert (f["id"], f["name"], f["brand"], f["format"]) == ("id", "name", "brand.name", "size")
    assert (f["price_full"], f["price_discounted"]) == ("price.original", "price.current")
    assert f["discount_pct"] == "discount_percentage" and f["valid_until"] == "valid_to"
    assert ep["price_divisor"] == 100
    assert ep["product_url_template"] == "https://spesa.everli.com{url}"
    assert ep["headers"] == {"accept": "application/json", "x-app-version": "3.1"}
    assert ep["auth"]["type"] == "cookie"
    assert len(p.offers) == 60 and p.offers[0].url.startswith("https://spesa.everli.com/p/")
    q = autoconfig.quality(p)
    assert q["with_prices"] == 1 and q["consistent"] == 1
    assert p.offers[0].price_full == 3.0


def test_shape_b_offset_strings():
    p = autoconfig.propose(noise() + shape_b())
    ep = p.endpoint
    assert ep["items_path"] == "products"
    assert (ep["pagination"]["type"], ep["pagination"]["param"], ep["pagination"]["start"]) == ("offset", "offset", 0)
    f = ep["fields"]
    assert (f["id"], f["name"], f["brand"], f["format"]) == ("product_id", "title", "brand", "weight")
    assert (f["price_full"], f["price_discounted"]) == ("regular_price", "final_price")
    assert f["valid_until"] == "promo.ends_at" and ep["price_divisor"] == 1
    assert p.offers[0].price_full == 1.79 and p.offers[0].discount_pct > 0


def test_shape_c_post_sections_ignores_unit_price():
    p = autoconfig.propose(noise() + shape_c())
    ep = p.endpoint
    assert ep["method"] == "POST" and ep["body"] == {"store": 42}
    assert ep["items_path"] == "sections.0.items"
    f = ep["fields"]
    assert (f["id"], f["name"], f["format"]) == ("sku", "display_name", "package")
    assert (f["price_full"], f["price_discounted"]) == ("old_price", "price")
    assert ep["pagination"]["param"] == "page"
    assert len(p.offers) == 24
    assert all(o.price_discounted == 0.89 for o in p.offers)


def test_cursor_pagination_is_reported():
    recs = shape_a()
    for i, r in enumerate(recs):
        r["params"] = {"after": f"cur{i}xyz"}
    p = autoconfig.propose(recs)
    assert p.endpoint["pagination"]["type"] == "none"
    assert any("cursore" in n for n in p.notes)


def test_sorted_by_discount_detection():
    assert autoconfig.propose(shape_a()).endpoint["sorted_by_discount"] is True
    shuffled = shape_a()
    for r in shuffled:
        r["body"]["data"].sort(key=lambda it: it["discount_percentage"])  # crescente
    assert autoconfig.propose(shuffled).endpoint["sorted_by_discount"] is False


def test_no_offers_endpoint():
    with pytest.raises(autoconfig.AutoconfigError):
        autoconfig.propose(noise())


def test_authorization_header_detected():
    recs = shape_a()
    for r in recs:
        r["request_headers"]["authorization"] = "<redacted>"
    ep = autoconfig.propose(recs).endpoint
    assert ep["auth"]["type"] == "local_storage_bearer" and "authorization" not in ep["headers"]


def test_jwt_key_in_local_storage():
    state = {"origins": [{"origin": "https://spesa.everli.com", "localStorage": [
        {"name": "theme", "value": "dark"}, {"name": "ev_session", "value": "eyJhbGc.eyJzdWIi.c2lnbmF0dXJl"}]}]}
    assert cli._jwt_like_key(state) == "ev_session"
    assert cli._jwt_like_key({"origins": []}) == ""


def test_cli_writes_config_and_doc(tmp_path, monkeypatch, capsys):
    root = tmp_path
    (root / "discovery" / "captures").mkdir(parents=True)
    (root / "docs").mkdir()
    cap = root / "discovery" / "captures" / "capture-20261001-120000.jsonl"
    with cap.open("w") as f:
        for r in noise() + shape_a():
            body = r.pop("body")
            r["body_sample"] = json.dumps(body) if body is not None else None
            f.write(json.dumps(r) + "\n")
    conf = root / "config.yaml"
    conf.write_text(f"paths: {{cache_dir: {root / 'cache'}, log_dir: {root / 'logs'}}}\noutput: {{min_discount_pct: 15}}\n")
    monkeypatch.setattr(cli.config, "ROOT", root)
    assert cli.main(["--config", str(conf), "autoconfig"]) == 0
    out = capsys.readouterr().out
    assert "Endpoint: GET https://spesa.everli.com/api/offers" in out and "60 offerte" in out
    import yaml
    written = yaml.safe_load(conf.read_text())
    assert written["output"]["min_discount_pct"] == 15  # il resto della config resta
    assert written["everli"]["endpoint"]["fields"]["price_discounted"] == "price.current"
    assert (root / "config.yaml.bak").exists()
    doc = (root / "docs" / "everli-api.md").read_text()
    assert "`GET https://spesa.everli.com/api/offers`" in doc and "<redacted>" not in doc

    # la config scritta funziona con il parser vero
    ep = written["everli"]["endpoint"]
    pages = [json.loads(json.loads(line)["body_sample"]) for line in cap.read_text().splitlines()[3:]]
    assert len(offers_from_pages(pages, ep)) == 60


def test_cli_dry_run_and_unreliable(tmp_path, monkeypatch, capsys):
    root = tmp_path
    (root / "discovery" / "captures").mkdir(parents=True)
    cap = root / "discovery" / "captures" / "capture-1.jsonl"
    bad = shape_c()
    for r in bad:
        for it in r["body"]["sections"][0]["items"]:
            it["price"] = "n/d"
            it["old_price"] = "n/d"
    with cap.open("w") as f:
        for r in bad:
            r["body_sample"] = json.dumps(r.pop("body"))
            f.write(json.dumps(r) + "\n")
    conf = root / "config.yaml"
    conf.write_text(f"paths: {{cache_dir: {root / 'cache'}, log_dir: {root / 'logs'}}}\n")
    monkeypatch.setattr(cli.config, "ROOT", root)
    assert cli.main(["--config", str(conf), "autoconfig"]) == cli.EXIT_ERROR
    assert "non è affidabile" in capsys.readouterr().out
    assert "endpoint" not in conf.read_text()
