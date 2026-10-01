"""Client HTTP verso Everli: sequenziale, con pause casuali e tetto di richieste.

Il trasporto è separato (PlaywrightTransport in produzione, finto nei test),
così la logica di throttling e di sessione scaduta è verificabile senza rete.
"""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

log = logging.getLogger(__name__)


class EverliError(Exception):
    pass


class SessionExpired(EverliError):
    """Sessione mancante o scaduta: serve un nuovo login manuale (discover.py)."""


class RequestBudgetExceeded(EverliError):
    pass


class ConnectionFailed(EverliError):
    """Everli non raggiungibile (rete assente, DNS, timeout): nessuna risposta ricevuta."""


@dataclass
class HttpResult:
    status: int
    headers: dict[str, str]
    text: str


class Transport(Protocol):
    def request(
        self, method: str, url: str, params: dict, headers: dict, body: dict | None
    ) -> HttpResult: ...


class EverliClient:
    def __init__(
        self,
        transport: Transport,
        *,
        max_requests: int = 30,
        min_delay: float = 2.0,
        max_delay: float = 6.0,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[float, float], float] = random.uniform,
    ):
        self.transport = transport
        self.max_requests = max_requests
        self.min_delay, self.max_delay = min_delay, max_delay
        self._sleep, self._jitter = sleep, jitter
        self.requests = 0

    @property
    def remaining(self) -> int:
        return self.max_requests - self.requests

    def fetch_json(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        headers: dict | None = None,
        body: dict | None = None,
    ) -> Any:
        if self.remaining <= 0:
            raise RequestBudgetExceeded(f"Raggiunto il tetto di {self.max_requests} richieste")
        if self.requests:
            self._sleep(self._jitter(self.min_delay, self.max_delay))
        self.requests += 1
        # Nel log solo metodo, URL e nomi dei parametri: niente header o cookie.
        log.info("Everli %s %s params=%s", method, url, sorted((params or {}).keys()))
        res = self.transport.request(method, url, params or {}, headers or {}, body)

        if res.status in (401, 403) or 300 <= res.status < 400:
            raise SessionExpired(f"Everli ha risposto {res.status}: sessione scaduta o non valida")
        if res.status == 429:
            raise EverliError("Everli ha risposto 429 (troppe richieste): mi fermo, riprova più tardi")
        if res.status >= 400:
            raise EverliError(f"Everli ha risposto {res.status}")
        if "html" in res.headers.get("content-type", ""):
            # Una pagina HTML al posto del JSON è quasi sempre il login.
            raise SessionExpired("Everli ha restituito una pagina HTML invece di JSON: probabile login")
        try:
            return json.loads(res.text)
        except json.JSONDecodeError as exc:
            raise EverliError(f"Risposta non JSON da {url}") from exc


def local_storage_value(state: dict, key: str) -> str | None:
    for origin in state.get("origins", []):
        for item in origin.get("localStorage", []):
            if item.get("name") == key:
                return item.get("value")
    return None


class PlaywrightTransport:
    """Riusa cookie e user agent della sessione salvata, senza aprire un browser."""

    def __init__(
        self,
        storage_state: dict,
        *,
        user_agent: str | None = None,
        extra_headers: dict | None = None,
        timeout_s: float = 20,
    ):
        self.storage_state = storage_state
        self.user_agent = user_agent
        self.extra_headers = extra_headers or {}
        self.timeout_ms = timeout_s * 1000

    def __enter__(self) -> "PlaywrightTransport":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._ctx = self._pw.request.new_context(
            storage_state=self.storage_state,
            user_agent=self.user_agent,
            extra_http_headers=self.extra_headers,
            timeout=self.timeout_ms,
        )
        return self

    def __exit__(self, *exc) -> None:
        self._ctx.dispose()
        self._pw.stop()

    def request(self, method, url, params, headers, body) -> HttpResult:
        from playwright.sync_api import Error as PlaywrightError

        try:
            res = self._ctx.fetch(
                url,
                method=method,
                params=params,
                headers=headers,
                data=json.dumps(body) if body else None,
                max_redirects=0,  # un redirect verso il login va visto, non seguito
            )
        except PlaywrightError as exc:
            # Solo il primo rigo: il messaggio di Playwright può includere i dettagli della richiesta.
            raise ConnectionFailed(f"Everli non raggiungibile: {str(exc).splitlines()[0]}") from None
        return HttpResult(res.status, res.headers, res.text())
