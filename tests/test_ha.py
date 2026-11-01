import base64
import json

import pytest

from evershopper import __main__ as cli
from evershopper import ha, pipeline, report
from evershopper.everli.client import SessionExpired
from evershopper.matching import Matcher
from evershopper.models import Offer
from evershopper.reminders import RemindersError, ShoppingItem


def test_read_todo_keeps_items_to_buy():
    calls = []

    def post(path, body):
        calls.append((path, body))
        return {"changed_states": [], "service_response": {"todo.spesa": {"items": [
            {"uid": "u1", "summary": " Latte ", "status": "needs_action", "description": "intero"},
            {"uid": "u2", "summary": "  ", "status": "needs_action"},
        ]}}}

    items = ha.read_todo("todo.spesa", post=post)
    assert calls == [("services/todo/get_items?return_response",
                      {"entity_id": "todo.spesa", "status": "needs_action"})]
    assert items == [ShoppingItem("u1", "Latte", notes="intero", list_id="todo.spesa")]


def test_read_todo_unknown_list():
    with pytest.raises(RemindersError, match="non trovata"):
        ha.read_todo("todo.spesa", post=lambda p, b: {"service_response": {}})


def test_notify_push_only_with_service(monkeypatch):
    calls = []
    monkeypatch.delenv("EVERSHOPPER_NOTIFY", raising=False)
    ha.notify("T", "M", post=lambda p, b: calls.append(p))
    monkeypatch.setenv("EVERSHOPPER_NOTIFY", "notify.mobile_app_telefono")
    ha.notify("T", "M", post=lambda p, b: calls.append(p))
    assert calls == ["services/persistent_notification/create",
                     "services/persistent_notification/create", "services/notify/mobile_app_telefono"]


def test_save_writes_sensor_and_page(tmp_path):
    offers = [Offer("1", "Latte Intero 1 L", "Granarolo", price_full=1.80, price_discounted=1.20, discount_pct=33.3)]
    items = [ShoppingItem("a", "Latte"), ShoppingItem("b", "Pane")]
    rep = report.build(Matcher().match(items, offers), title="Offerte Everli", offers_date="2026-10-01T09:00:00",
                       store="Esselunga", threshold=10)
    posted = []
    ha.save(tmp_path, rep, "<p>ok</p>", post=lambda p, b: posted.append((p, b)))
    path, body = posted[0]
    assert path == "states/sensor.evershopper_offerte" and body["state"] == "1"
    assert body["attributes"]["voci"] == [{"voce": "Latte", "sconto": 33.3, "prezzo": 1.20,
                                           "prodotto": "Latte Intero 1 L"}]
    assert json.loads((tmp_path / ha.SENSOR_FILE).read_text()) == body
    assert (tmp_path / ha.REPORT_FILE).read_text() == "<p>ok</p>"


def test_session_from_option(monkeypatch):
    blob = base64.b64encode(json.dumps({"state": {"cookies": []}, "user_agent": "UA"}).encode()).decode()
    monkeypatch.setenv("EVERSHOPPER_SESSION", blob)
    assert pipeline.load_session({}) == ({"cookies": []}, "UA")
    monkeypatch.setenv("EVERSHOPPER_SESSION", "non-base64!")
    with pytest.raises(SessionExpired, match="export-session"):
        pipeline.load_session({})


def test_serve_refuses_outside_home_assistant(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert cli.main(["serve"]) == cli.EXIT_ERROR
