import base64
import json
import plistlib
import subprocess
from datetime import datetime

import pytest

from evershopper import __main__ as cli
from evershopper import keychain, pipeline, schedule
from evershopper.everli.client import ConnectionFailed


def cfg(weekday="sabato", hour=8, minute=47):
    return {"schedule": {"weekday": weekday, "hour": hour, "minute": minute}}


@pytest.mark.parametrize("value,expected", [
    (0, 0), (6, 6), (7, 0), ("3", 3), ("sabato", 6), ("Lunedì", 1), ("venerdi", 5), (" Domenica ", 0),
])
def test_parse_weekday(value, expected):
    assert schedule.parse_weekday(value) == expected


@pytest.mark.parametrize("value", [8, -1, "sab", None, True])
def test_parse_weekday_invalid(value):
    with pytest.raises(schedule.ScheduleError):
        schedule.parse_weekday(value)


def test_invalid_time():
    with pytest.raises(schedule.ScheduleError, match="Ora"):
        schedule.timing(cfg(hour=24))


def test_next_run():
    thursday = datetime(2026, 10, 1, 12, 0)  # giovedì
    assert schedule.next_run(6, 8, 47, thursday) == datetime(2026, 10, 3, 8, 47)   # sabato
    assert schedule.next_run(4, 13, 0, thursday) == datetime(2026, 10, 1, 13, 0)   # oggi più tardi
    assert schedule.next_run(4, 11, 0, thursday) == datetime(2026, 10, 8, 11, 0)   # oggi è passato
    assert schedule.describe(6, 8, 47) == "ogni sabato alle 08:47"
    assert schedule.describe(1, 9, 5) == "ogni lunedì alle 09:05"


def test_build_plist(tmp_path):
    data = schedule.build_plist(cfg(), "/x/.venv/bin/python", root=tmp_path)
    assert data["Label"] == "it.evershopper.weekly"
    assert data["ProgramArguments"] == ["/x/.venv/bin/python", "-m", "evershopper", "run"]
    assert data["WorkingDirectory"] == str(tmp_path)
    assert data["StartCalendarInterval"] == {"Weekday": 6, "Hour": 8, "Minute": 47}
    assert data["EnvironmentVariables"]["PATH"].startswith("/usr/bin")
    assert data["RunAtLoad"] is False
    plistlib.dumps(data)  # serializzabile


class FakeRun:
    def __init__(self, results=None):
        self.results = results or {}
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        code, out = self.results.get(cmd[1], (0, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")


def test_install_writes_plist_and_bootstraps(tmp_path):
    run = FakeRun()
    path = tmp_path / "agent.plist"
    schedule.install(cfg(), python="/x/.venv/bin/python", run=run, path=path, log_dir=tmp_path / "logs")
    assert plistlib.loads(path.read_bytes())["StartCalendarInterval"]["Weekday"] == 6
    assert [c[1] for c in run.calls] == ["bootout", "bootstrap"]
    assert run.calls[1][3] == str(path)
    assert (tmp_path / "logs").is_dir()


def test_install_refuses_system_python(tmp_path):
    with pytest.raises(schedule.ScheduleError, match="venv"):
        schedule.install(cfg(), python="/usr/bin/python3", run=FakeRun(), path=tmp_path / "a.plist")


def test_install_reports_bootstrap_error(tmp_path):
    run = FakeRun({"bootstrap": (5, "Bootstrap failed: 5: Input/output error")})
    with pytest.raises(schedule.ScheduleError, match="Input/output"):
        schedule.install(cfg(), python="/x/.venv/bin/python", run=run, path=tmp_path / "a.plist",
                         log_dir=tmp_path)


def test_status_and_uninstall(tmp_path):
    path = tmp_path / "agent.plist"
    schedule.install(cfg(), python="/x/.venv/bin/python", run=FakeRun(), path=path, log_dir=tmp_path)
    out = "it.evershopper.weekly = {\n\tstate = not running\n\truns = 3\n\tlast exit code = 2\n}"
    info = schedule.status(run=FakeRun({"print": (0, out)}), path=path)
    assert info["loaded"] and info["state"] == "not running" and info["runs"] == "3" and info["last_exit"] == "2"
    assert schedule.uninstall(run=FakeRun(), path=path) is True
    assert not path.exists()
    assert schedule.status(run=FakeRun({"print": (113, "")}), path=path) == {"installed": False, "loaded": False}


def test_run_now_requires_loaded_agent():
    with pytest.raises(schedule.ScheduleError, match="install"):
        schedule.run_now(run=FakeRun({"kickstart": (113, "")}))


# --- Portachiavi (comando security finto) ---

class FakeSecurity:
    def __init__(self):
        self.store, self.calls = {}, []

    def __call__(self, cmd):
        self.calls.append(cmd)
        key = (cmd[cmd.index("-s") + 1], cmd[cmd.index("-a") + 1])
        if cmd[1] == "add-generic-password":
            self.store[key] = cmd[cmd.index("-w") + 1]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[1] == "find-generic-password":
            if key in self.store:
                return subprocess.CompletedProcess(cmd, 0, self.store[key] + "\n", "")
            return subprocess.CompletedProcess(cmd, 44, "", "not found")
        if cmd[1] == "delete-generic-password":
            return subprocess.CompletedProcess(cmd, 0 if self.store.pop(key, None) else 44, "", "")


def test_session_roundtrip_in_keychain():
    sec = FakeSecurity()
    state = {"cookies": [{"name": "sid", "value": "àbc\"€", "domain": ".everli.com"}], "origins": []}
    keychain.save_session("evershopper", "everli-session", state, run=sec, system="Darwin")
    stored = sec.store[("evershopper", "everli-session")]
    assert json.loads(base64.b64decode(stored)) == state  # base64, nessun carattere particolare
    add = sec.calls[0]
    assert add[:3] == ["security", "add-generic-password", "-U"]
    assert keychain.load_session("evershopper", "everli-session", run=sec, system="Darwin") == state
    assert keychain.load_session("evershopper", "altro", run=sec, system="Darwin") is None
    assert keychain.delete_secret("evershopper", "everli-session", run=sec, system="Darwin") is True
    assert keychain.delete_secret("evershopper", "everli-session", run=sec, system="Darwin") is False


def test_keychain_outside_macos():
    assert keychain.get_secret("s", "a", system="Linux") is None
    with pytest.raises(keychain.KeychainError):
        keychain.set_secret("s", "a", "v", system="Linux")


def test_missing_session_is_session_expired(monkeypatch):
    from evershopper import config
    c = config.load()
    c["everli"]["endpoint"].update({"url": "https://x/{store_id}", "items_path": "data"})
    c["everli"]["store"]["id"] = "1"
    monkeypatch.setattr(keychain, "load_session", lambda *a, **k: None)
    with pytest.raises(cli.SessionExpired, match="--login"):
        pipeline.fetch_offers(c)


# --- run: un solo nuovo tentativo se la rete non c'è ---

@pytest.fixture
def run_env(tmp_path, monkeypatch):
    conf = tmp_path / "config.yaml"
    conf.write_text(f"paths: {{cache_dir: {tmp_path / 'cache'}, log_dir: {tmp_path / 'logs'}}}\n"
                    "output: {channels: []}\n")
    lista = tmp_path / "lista.json"
    lista.write_text(json.dumps({"items": [{"id": "a", "title": "Latte"}]}))
    sleeps = []
    monkeypatch.setattr(cli.time, "sleep", sleeps.append)
    return conf, lista, tmp_path / "cache", sleeps


def test_run_retries_once_on_connection_error(run_env, monkeypatch):
    conf, lista, cache_dir, sleeps = run_env
    calls = []

    def fake_fetch(cfg):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionFailed("Everli non raggiungibile")
        cache_dir.mkdir(exist_ok=True)
        path = cache_dir / "offers-x.json"
        path.write_text(json.dumps({"fetched_at": datetime.now().isoformat(), "offers": []}))
        return [], [], 1, path
    monkeypatch.setattr(pipeline, "fetch_offers", fake_fetch)
    assert cli.main(["--config", str(conf), "run", "--reminders-json", str(lista)]) == 0
    assert len(calls) == 2 and sleeps == [cli.RETRY_DELAY_S]


def test_run_gives_up_after_second_connection_error(run_env, monkeypatch):
    conf, lista, _, sleeps = run_env
    calls = []

    def always_down(cfg):
        calls.append(1)
        raise ConnectionFailed("Everli non raggiungibile")
    monkeypatch.setattr(pipeline, "fetch_offers", always_down)
    monkeypatch.setattr(cli, "notify", lambda *a: None)
    assert cli.main(["--config", str(conf), "run", "--reminders-json", str(lista)]) == cli.EXIT_ERROR
    assert len(calls) == 2 and len(sleeps) == 1


def test_launchd_log_rotation(tmp_path):
    from evershopper import logs
    big = tmp_path / "launchd.log"
    big.write_bytes(b"x" * (logs.LAUNCHD_MAX_BYTES + 1))
    logs.rotate_launchd_log(tmp_path)
    assert not big.exists() and (tmp_path / "launchd.log.1").exists()
