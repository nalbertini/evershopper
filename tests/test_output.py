import json
import subprocess

import pytest

from evershopper import __main__ as cli
from evershopper import outputs, reminders, report
from evershopper.matching import Matcher
from evershopper.models import Offer
from evershopper.reminders import ShoppingItem


def offer(oid, name, brand, full, disc, until=None, url=None):
    return Offer(id=oid, name=name, brand=brand, price_full=full, price_discounted=disc,
                 discount_pct=round((full - disc) / full * 100, 1), valid_until=until, url=url)


OFFERS = [
    offer("1", "Latte Intero 1 L", "Granarolo", 1.80, 1.20, "2026-10-08", "https://it.everli.com/p/1"),
    offer("2", "Latte Scremato 1 L", "Parmalat", 1.60, 1.40),
    offer("3", "Caffè macinato 250 g", "Lavazza", 4.20, 2.99, "2026-10-08T23:59:00Z"),
    offer("4", "Uova fresche 6 pz", "Aia", 2.30, 2.10),
    offer("5", "Cioccolato al latte", "Milka", 2.00, 1.00),
]
ITEMS = [
    ShoppingItem("a", "Latte", list_id="L1"),
    ShoppingItem("b", "Caffè", list_id="L1"),
    ShoppingItem("c", "Uova", list_id="L1"),
    ShoppingItem("d", "Pane", list_id="L1"),
    ShoppingItem("e", "Cioccolato fondente <70%>", list_id="L1"),
]


@pytest.fixture
def rep():
    results = Matcher().match(ITEMS, OFFERS)
    return report.build(results, title="Offerte Everli", offers_date="2026-10-01T09:00:00",
                        store="Esselunga", threshold=10, max_per_item=2)


def test_build_sorts_by_discount_and_splits(rep):
    assert [line.item.title for line in rep.lines] == ["Latte", "Caffè"]  # -33.3% e -28.8%
    assert [o.id for o in rep.lines[0].offers] == ["1", "2"]
    assert [(i.title, p) for i, p in rep.below] == [("Uova", 8.7)]
    assert [i.title for i in rep.none] == ["Pane"]
    assert [i.title for i, _ in rep.doubts] == ["Cioccolato fondente <70%>"]


def test_text_is_readable(rep):
    text = report.to_text(rep)
    assert text.startswith("Offerte Everli · Esselunga · offerte del 01/10")
    assert "Granarolo Latte Intero 1 L: 1,20 € invece di 1,80 € (-33.3%), fino al 08/10" in text
    assert "Lavazza Caffè macinato 250 g: 2,99 € invece di 4,20 € (-28.8%), fino al 08/10" in text
    assert "Sconto sotto soglia: Uova (-8.7%)" in text
    assert "Non in offerta: Pane" in text


def test_html_escapes_and_links(rep):
    html = report.to_html(rep)
    assert html.startswith("<h1>Offerte Everli</h1>")  # titolo della nota
    assert '<a href="https://it.everli.com/p/1">' in html
    assert "&lt;70%&gt;" in html and "<70%>" not in html


def test_notification_and_marks(rep):
    title, msg = report.notification(rep)
    assert msg == "2 voci in offerta: Latte -33.3%, Caffè -28.8%"
    assert report.mark_texts(rep) == {
        "a": "-33.3% Granarolo Latte Intero 1 L 1,20 € fino al 08/10",
        "b": "-28.8% Lavazza Caffè macinato 250 g 2,99 € fino al 08/10",
    }
    empty = report.build([], title="T", offers_date="x")
    assert report.notification(empty)[1] == "Nessuna voce della lista in offerta questa settimana"


class FakeRun:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.result


@pytest.fixture
def macos(monkeypatch):
    monkeypatch.setattr(outputs.platform, "system", lambda: "Darwin")


def test_note_passes_data_as_json_argument(macos):
    run = FakeRun(stdout="updated\n")
    assert outputs.write_note('Offerte "Everli"', "<h1>x</h1>", "Spesa", run=run) == "updated"
    cmd = run.calls[0]
    assert cmd[:4] == ["osascript", "-l", "JavaScript", "-e"]
    assert json.loads(cmd[5]) == {"title": 'Offerte "Everli"', "html": "<h1>x</h1>", "folder": "Spesa"}
    assert "Everli" not in cmd[4]  # nessun dato interpolato nello script


def test_email_requires_address_and_reports_permission(macos):
    with pytest.raises(outputs.OutputError, match="email_to"):
        outputs.send_email("", "s", "t", run=FakeRun())
    denied = FakeRun(returncode=1, stderr="execution error: Not authorized to send Apple events to Mail. (-1743)")
    with pytest.raises(outputs.OutputError, match="Automazione"):
        outputs.send_email("a@b.it", "s", "t", run=denied)


class FakeOpen:
    def __init__(self, payload):
        self.payload, self.calls, self.requests = payload, [], []

    def __call__(self, cmd):
        self.calls.append(cmd)
        self.requests.append(json.loads(open(cmd[cmd.index("--mark") + 1]).read()))
        with open(cmd[cmd.index("--out") + 1], "w") as f:
            f.write(json.dumps(self.payload))
        return subprocess.CompletedProcess(cmd, 0, "", "")


def test_mark_sends_request_to_app(tmp_path):
    app = tmp_path / "EvershopperReminders.app"
    app.mkdir()
    run = FakeOpen({"updated": 3, "marked": 2})
    n = reminders.mark({"a": "-33% Latte"}, ["L1", "L1"], marker="🏷️ In offerta", app=app, run=run, system="Darwin")
    assert n == 3
    assert run.requests == [{"marker": "🏷️ In offerta", "list_ids": ["L1"], "marks": {"a": "-33% Latte"}}]


def test_mark_rejects_unrecognisable_marker(tmp_path):
    with pytest.raises(reminders.RemindersError, match="iniziare"):
        reminders.mark({}, ["L1"], marker="In offerta", system="Darwin")


def test_marker_lines_are_ignored_when_reading():
    out = {"items": [{"id": "a", "title": "Latte", "notes": "intero\n🏷️ In offerta: -33% Granarolo", "listId": "L1"}]}
    [item] = reminders.parse_output(out)
    assert item.notes == "intero" and item.list_id == "L1"


# --- comando run, senza rete né macOS ---

@pytest.fixture
def setup(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "offers-2026-10-01.json").write_text(json.dumps(
        {"fetched_at": "2026-10-01T09:00:00", "offers": [o.to_dict() for o in OFFERS]}))
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps({"items": [i.to_dict() | {"listId": i.list_id} for i in ITEMS]}))
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"""
paths: {{cache_dir: {cache_dir}, log_dir: {tmp_path / 'logs'}}}
everli: {{store: {{name: Esselunga}}}}
output: {{channels: [notification, note], mark_reminders: true}}
""")
    sent = {"notify": [], "note": []}
    monkeypatch.setattr(outputs, "send_notification", lambda t, m: sent["notify"].append(m))
    monkeypatch.setattr(outputs, "write_note", lambda t, h, f="": sent["note"].append(h) or "created")
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda: __import__("datetime").datetime(2026, 10, 3)),
                                                         "fromisoformat": staticmethod(__import__("datetime").datetime.fromisoformat)}))
    return cfg, lista, cache_dir, sent


def test_run_offline_delivers(setup, capsys):
    cfg, lista, cache_dir, sent = setup
    code = cli.main(["--config", str(cfg), "run", "--offline", "--reminders-json", str(lista)])
    assert code == 0
    assert sent["notify"] == ["2 voci in offerta: Latte -33.3%, Caffè -28.8%"]
    assert sent["note"][0].startswith("<h1>Offerte Everli</h1>")
    assert (cache_dir / "report-2026-10-03.txt").exists()
    out = capsys.readouterr().out
    assert "Nota «Offerte Everli» creata" in out


def test_run_dry_run_sends_nothing(setup, capsys):
    cfg, lista, _, sent = setup
    assert cli.main(["--config", str(cfg), "run", "--offline", "--dry-run", "--reminders-json", str(lista)]) == 0
    assert sent == {"notify": [], "note": []}
    assert "nessuna notifica" in capsys.readouterr().out


def test_run_warns_on_old_offers(setup, capsys, monkeypatch):
    cfg, lista, _, _ = setup
    import datetime as dt
    monkeypatch.setattr(cli, "datetime", type("D", (), {"now": staticmethod(lambda: dt.datetime(2026, 10, 20)),
                                                         "fromisoformat": staticmethod(dt.datetime.fromisoformat)}))
    cli.main(["--config", str(cfg), "run", "--offline", "--dry-run", "--reminders-json", str(lista)])
    assert "⚠️ Le offerte sono di 18 giorni fa" in capsys.readouterr().out


def test_run_failed_channel_returns_error(setup, monkeypatch):
    cfg, lista, _, _ = setup

    def broken(*a, **k):
        raise outputs.OutputError("Note non disponibile")
    monkeypatch.setattr(outputs, "write_note", broken)
    assert cli.main(["--config", str(cfg), "run", "--offline", "--reminders-json", str(lista)]) == cli.EXIT_ERROR
