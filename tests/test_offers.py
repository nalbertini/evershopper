import json

import pytest

from evershopper import cache
from evershopper.everli.client import (
    EverliClient,
    EverliError,
    HttpResult,
    RequestBudgetExceeded,
    SessionExpired,
    local_storage_value,
)
from evershopper.everli.offers import (
    EndpointNotConfigured,
    check_endpoint,
    get_path,
    iter_pages,
    offers_from_pages,
    parse_number,
)

# Struttura inventata: quella vera esce dalla discovery.
ENDPOINT = {
    "url": "https://api.example/stores/{store_id}/promotions",
    "method": "GET",
    "params": {"sort": "discount"},
    "items_path": "data.products",
    "pagination": {"type": "page", "param": "page", "start": 1, "size": 2, "size_param": "per_page",
                   "last_page_path": "meta.last_page", "max_pages": 10},
    "fields": {"id": "id", "name": "name", "brand": "brand.name", "format": "unit",
               "price_full": "price.original", "price_discounted": "price.current",
               "discount_pct": "", "valid_until": "promo.ends_at", "url": ""},
    "price_divisor": 100,
    "product_url_template": "https://it.everli.com/p/{id}",
}


def page(products, last_page=3):
    return {"data": {"products": products}, "meta": {"last_page": last_page}}


def product(pid, name, full, current, brand="Marca"):
    return {"id": pid, "name": name, "brand": {"name": brand}, "unit": "1 L",
            "price": {"original": full, "current": current}, "promo": {"ends_at": "2026-10-08"}}


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, params, headers, body):
        self.calls.append((method, url, dict(params)))
        r = self.responses.pop(0)
        if isinstance(r, HttpResult):
            return r
        return HttpResult(200, {"content-type": "application/json"}, json.dumps(r))


def client(responses, **kw):
    sleeps = []
    c = EverliClient(FakeTransport(responses), sleep=sleeps.append, jitter=lambda a, b: (a + b) / 2, **kw)
    return c, sleeps


def test_get_path():
    obj = {"a": {"b": [{"c": 1}]}}
    assert get_path(obj, "a.b.0.c") == 1
    assert get_path(obj, "a.x", "d") == "d"
    assert get_path(obj, "a.b.5.c") is None
    assert get_path(obj, "") is None


@pytest.mark.parametrize("raw,div,expected", [
    (129, 100, 1.29), (1.29, 1, 1.29), ("1,29 €", 1, 1.29), ("€ 1.299,00", 1, 1299.0),
    ("-20%", 1, -20.0), ("", 1, None), (None, 1, None), ("n/d", 1, None),
])
def test_parse_number(raw, div, expected):
    assert parse_number(raw, div) == expected


def test_pagination_stops_at_last_page_and_waits_between_calls():
    c, sleeps = client([
        page([product(1, "Latte", 200, 150), product(2, "Pasta", 100, 90)]),
        page([product(3, "Caffè", 500, 250), product(4, "Burro", 300, 270)]),
        page([product(5, "Uova", 300, 240), product(6, "Riso", 200, 180)]),
    ])
    pages = [d for _, d in iter_pages(c, ENDPOINT, "123")]
    assert len(pages) == 3
    calls = c.transport.calls
    assert calls[0][1] == "https://api.example/stores/123/promotions"
    assert [p["page"] for _, _, p in calls] == [1, 2, 3]
    assert calls[0][2]["per_page"] == 2 and calls[0][2]["sort"] == "discount"
    assert sleeps == [4.0, 4.0]  # pausa solo tra una richiesta e l'altra

    offers = offers_from_pages(pages, ENDPOINT)
    assert [o.name for o in offers][:2] == ["Caffè", "Latte"]  # ordinate per sconto
    caffe = offers[0]
    assert (caffe.price_full, caffe.price_discounted, caffe.discount_pct) == (5.0, 2.5, 50.0)
    assert caffe.brand == "Marca" and caffe.format == "1 L"
    assert caffe.url == "https://it.everli.com/p/3"


def test_pagination_stops_on_short_page():
    c, _ = client([page([product(1, "Latte", 200, 150)], last_page=None)])
    assert len(list(iter_pages(c, ENDPOINT, "123"))) == 1


def test_request_budget_gives_partial_results():
    full = page([product(1, "A", 200, 100), product(2, "B", 200, 100)], last_page=None)
    c, _ = client([full, full, full], max_requests=2)
    assert len(list(iter_pages(c, ENDPOINT, "123"))) == 2
    with pytest.raises(RequestBudgetExceeded):
        c.fetch_json("GET", "https://api.example")


def test_offset_pagination():
    ep = {**ENDPOINT, "pagination": {"type": "offset", "param": "offset", "start": 0, "size": 2, "max_pages": 5}}
    c, _ = client([
        page([product(1, "A", 2, 1), product(2, "B", 2, 1)]),
        page([]),
    ])
    list(iter_pages(c, ep, "123"))
    assert [p["offset"] for _, _, p in c.transport.calls] == [0, 2]


@pytest.mark.parametrize("res", [
    HttpResult(401, {}, ""),
    HttpResult(302, {"location": "/login"}, ""),
    HttpResult(200, {"content-type": "text/html"}, "<html>login</html>"),
])
def test_session_expired_stops_without_retry(res):
    c, _ = client([res, page([])])
    with pytest.raises(SessionExpired):
        list(iter_pages(c, ENDPOINT, "123"))
    assert len(c.transport.calls) == 1


def test_rate_limited_is_an_error_without_retry():
    c, _ = client([HttpResult(429, {}, ""), page([])])
    with pytest.raises(EverliError):
        list(iter_pages(c, ENDPOINT, "123"))
    assert len(c.transport.calls) == 1


def test_endpoint_not_configured():
    ep = {**ENDPOINT, "url": "", "items_path": ""}
    with pytest.raises(EndpointNotConfigured, match="url, items_path"):
        check_endpoint(ep, "123")
    with pytest.raises(EndpointNotConfigured, match="store.id"):
        check_endpoint(ENDPOINT, "")


def test_discount_from_json_and_duplicates():
    ep = {**ENDPOINT, "fields": {**ENDPOINT["fields"], "discount_pct": "promo.label"}}
    item = product(1, "Latte", 200, 150)
    item["promo"]["label"] = "-30%"
    offers = offers_from_pages([page([item]), page([item])], ep)
    assert len(offers) == 1 and offers[0].discount_pct == 30.0


def test_local_storage_value():
    state = {"origins": [{"origin": "https://it.everli.com", "localStorage": [{"name": "tok", "value": "x"}]}]}
    assert local_storage_value(state, "tok") == "x"
    assert local_storage_value(state, "nope") is None


def test_cache_roundtrip(tmp_path):
    offers = offers_from_pages([page([product(1, "Latte", 200, 150)])], ENDPOINT)
    path = cache.save(tmp_path, offers, [{"raw": 1}], {"requests": 1})
    assert path.name.startswith("offers-") and (tmp_path / path.name.replace("offers-", "raw-").removesuffix(".json")).is_dir()
    payload, loaded = cache.load_latest(tmp_path)
    assert payload["count"] == 1 and loaded == offers


def test_relative_url_template_and_epoch_date():
    ep = {**ENDPOINT, "product_url_template": "https://spesa.everli.com{url}",
          "fields": {**ENDPOINT["fields"], "url": "link", "valid_until": "promo.ends"}}
    item = product(1, "Latte", 200, 150)
    item["link"] = "/p/1"
    item["promo"]["ends"] = 1791504000  # 2026-10-09 UTC
    [o] = offers_from_pages([page([item])], ep)
    assert o.url == "https://spesa.everli.com/p/1" and o.valid_until == "2026-10-09"


def test_root_list_items_path():
    ep = {**ENDPOINT, "items_path": "."}
    assert len(offers_from_pages([[product(1, "Latte", 200, 150)]], ep)) == 1
