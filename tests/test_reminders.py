import json
import subprocess

import pytest

from evershopper import reminders
from evershopper.reminders import ListNotFound, RemindersAccessDenied, RemindersError, read_list

HELPER_OUTPUT = json.dumps({
    "list": "Spesa",
    "items": [
        {"id": "A1", "title": " Latte ", "notes": "intero", "priority": 0},
        {"id": "A2", "title": "Pasta", "notes": "  ", "priority": 0},
        {"id": "A3", "title": "", "notes": None, "priority": 0},
    ],
})


class FakeRun:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.result = subprocess.CompletedProcess([], returncode, stdout, stderr)
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        return self.result


@pytest.fixture
def helper(tmp_path):
    h = tmp_path / "reminders-helper"
    h.write_text("")
    return h


def test_eventkit_parses_and_cleans_items(helper):
    run = FakeRun(stdout=HELPER_OUTPUT)
    items = read_list("Spesa", helper=helper, run=run, system="Darwin")
    assert run.calls == [[str(helper), "--list", "Spesa"]]
    assert [(i.id, i.title, i.notes) for i in items] == [("A1", "Latte", "intero"), ("A2", "Pasta", None)]


@pytest.mark.parametrize("code,exc", [(3, RemindersAccessDenied), (4, ListNotFound), (5, RemindersError)])
def test_eventkit_exit_codes(helper, code, exc):
    with pytest.raises(exc):
        read_list("Spesa", helper=helper, run=FakeRun(returncode=code, stderr="msg"), system="Darwin")


def test_auto_falls_back_to_jxa_without_helper(tmp_path):
    run = FakeRun(stdout=HELPER_OUTPUT)
    items = read_list('Spesa "casa"', helper=tmp_path / "manca", run=run, system="Darwin")
    cmd = run.calls[0]
    assert cmd[:3] == ["osascript", "-l", "JavaScript"]
    assert 'const name = "Spesa \\"casa\\"", listId = "";' in cmd[4]  # escape corretto
    assert len(items) == 2


def test_eventkit_backend_requires_helper(tmp_path):
    with pytest.raises(RemindersError, match="build.sh"):
        read_list("Spesa", backend="eventkit", helper=tmp_path / "manca", run=FakeRun(), system="Darwin")


def test_jxa_list_not_found_lists_available():
    err = "execution error: Error: LIST_NOT_FOUND Lavoro, Casa (-2700)"
    with pytest.raises(ListNotFound, match=r"Liste disponibili: Lavoro, Casa$"):
        read_list("Spesa", backend="jxa", run=FakeRun(returncode=1, stderr=err), system="Darwin")


def test_jxa_access_denied():
    err = "execution error: Not authorized to send Apple events to Reminders. (-1743)"
    with pytest.raises(RemindersAccessDenied):
        read_list("Spesa", backend="jxa", run=FakeRun(returncode=1, stderr=err), system="Darwin")


def test_not_macos():
    with pytest.raises(RemindersError, match="macOS"):
        read_list("Spesa", system="Linux")


def test_read_json(tmp_path):
    f = tmp_path / "lista.json"
    f.write_text(HELPER_OUTPUT)
    assert [i.title for i in reminders.read_json(f)] == ["Latte", "Pasta"]


class FakeOpen:
    """Simula `open -W … --args … --out FILE`: scrive nel file come farebbe l'app."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if self.payload is not None:
            out = cmd[cmd.index("--out") + 1]
            with open(out, "w") as f:
                f.write(json.dumps(self.payload))
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def app(tmp_path):
    a = tmp_path / "EvershopperReminders.app"
    a.mkdir()
    return a


def test_auto_prefers_app(app, helper):
    run = FakeOpen(json.loads(HELPER_OUTPUT))
    items = read_list("Spesa", app=app, helper=helper, run=run, system="Darwin")
    cmd = run.calls[0]
    assert cmd[:5] == ["open", "-W", "-n", "-g", str(app)]
    assert cmd[5:8] == ["--args", "--list", "Spesa"]
    assert [i.title for i in items] == ["Latte", "Pasta"]


@pytest.mark.parametrize("code,exc", [(3, RemindersAccessDenied), (4, ListNotFound), (5, RemindersError)])
def test_app_errors_from_file(app, code, exc):
    run = FakeOpen({"error": "msg", "code": code})
    with pytest.raises(exc):
        read_list("Spesa", app=app, run=run, system="Darwin")


def test_app_without_result(app):
    with pytest.raises(RemindersError, match="non ha prodotto risultati"):
        read_list("Spesa", app=app, run=FakeOpen(None), system="Darwin")


def test_access_denied_message_names_the_app(app):
    with pytest.raises(RemindersAccessDenied, match="Evershopper Promemoria"):
        read_list("Spesa", app=app, run=FakeOpen({"error": "negato", "code": 3}), system="Darwin")


LISTS = [{"id": "X1", "title": "Spesa", "source": "iCloud"}, {"id": "X2", "title": "Spesa", "source": "Gmail"}]


def test_list_lists(app, helper):
    assert reminders.list_lists(app=app, run=FakeOpen({"lists": LISTS}), system="Darwin") == LISTS
    run = FakeRun(stdout=json.dumps({"lists": LISTS}))
    assert reminders.list_lists(backend="eventkit", helper=helper, run=run, system="Darwin") == LISTS
    assert run.calls == [[str(helper), "--lists"]]


def test_list_id_is_passed(app, helper):
    run = FakeOpen(json.loads(HELPER_OUTPUT))
    read_list("Spesa", list_id="X2", app=app, run=run, system="Darwin")
    assert run.calls[0][5:10] == ["--args", "--list", "Spesa", "--list-id", "X2"]
    run = FakeRun(stdout=HELPER_OUTPUT)
    read_list("Spesa", list_id="X2", backend="jxa", run=run, system="Darwin")
    assert 'listId = "X2"' in run.calls[0][4]


def test_duplicate_lists_warn(helper, caplog):
    out = json.loads(HELPER_OUTPUT) | {"matched": LISTS}
    items = read_list("Spesa", helper=helper, run=FakeRun(stdout=json.dumps(out)), system="Darwin")
    assert len(items) == 2
    assert "2 liste «Spesa»" in caplog.text and "iCloud, Gmail" in caplog.text
