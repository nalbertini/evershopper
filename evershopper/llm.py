"""Seconda passata opzionale: l'API Claude decide sugli abbinamenti dubbi.

Una sola richiesta per esecuzione, con tutte le voci in dubbio e i loro candidati;
la risposta è JSON vincolato da uno schema. Si inviano solo i nomi delle voci e
dei prodotti, mai credenziali. Se la chiave manca o l'API fallisce, restano i
risultati della prima passata.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .matching import SICURO, Candidate, ItemMatch

log = logging.getLogger(__name__)

SYSTEM = """Aiuti a confrontare una lista della spesa con le offerte di un supermercato italiano.
Per ogni voce della lista ricevi alcuni prodotti candidati. Indica quali candidati sono davvero
quella voce, cioè cosa comprerebbe chi l'ha scritta: "latte" è il latte da bere, non il
cioccolato al latte né il latte detergente; "caffè" è il caffè, non il gelato al caffè.
Tieni conto delle note della voce come preferenze, ma non escludere un prodotto solo perché
la marca o il formato sono diversi. Se nessun candidato va bene, restituisci una lista vuota.
Usa solo gli identificativi forniti."""

SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item_id": {"type": "string"},
                    "offer_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item_id", "offer_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["decisions"],
    "additionalProperties": False,
}


def build_payload(results: list[ItemMatch], scope: str = "ambiguous") -> list[dict]:
    payload = []
    for r in results:
        cands = list(r.doubts) + (list(r.matches) if scope == "all" else [])
        if not cands:
            continue
        payload.append({
            "item_id": r.item.id,
            "voce": r.item.title,
            "note": r.item.notes,
            "candidati": [
                {"offer_id": c.offer.id, "nome": c.offer.name, "marca": c.offer.brand, "formato": c.offer.format}
                for c in cands
            ],
        })
    return payload


def apply_decisions(results: list[ItemMatch], decisions: list[dict], scope: str = "ambiguous") -> int:
    """Sposta tra le offerte abbinate i candidati confermati; restituisce quanti."""
    by_item = {d["item_id"]: set(d["offer_ids"]) for d in decisions if isinstance(d.get("offer_ids"), list)}
    moved = 0
    for r in results:
        if r.item.id not in by_item:
            continue
        ok = by_item[r.item.id]
        confirmed = [c for c in r.doubts if c.offer.id in ok]
        if scope == "all":
            r.matches = [c for c in r.matches if c.offer.id in ok]
        for c in confirmed:
            r.matches.append(Candidate(c.offer, SICURO, c.score, "confermato da Claude", "llm"))
        moved += len(confirmed)
        r.doubts = []  # deciso: quello che non è stato confermato non è la voce
        r.matches.sort(key=lambda c: (-c.score, -(c.offer.discount_pct or 0)))
    return moved


def ask_claude(payload: list[dict], *, api_key: str, model: str, client=None) -> list[dict]:
    if client is None:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
    )
    if response.stop_reason == "refusal":
        log.warning("Claude ha rifiutato la richiesta: restano i risultati della prima passata")
        return []
    if response.stop_reason == "max_tokens":
        log.warning("Risposta di Claude troncata: restano i risultati della prima passata")
        return []
    text = next((b.text for b in response.content if b.type == "text"), "")
    return json.loads(text).get("decisions", [])


def resolve(
    results: list[ItemMatch],
    *,
    api_key: str,
    model: str,
    scope: str = "ambiguous",
    cache_dir: Path | None = None,
    client=None,
) -> int:
    """Seconda passata; le risposte sono in cache per contenuto, così rilanciare non costa."""
    payload = build_payload(results, scope)
    if not payload:
        return 0
    body = json.dumps({"model": model, "system": SYSTEM, "payload": payload}, ensure_ascii=False, sort_keys=True)
    cache_file = cache_dir / f"llm-{hashlib.sha256(body.encode()).hexdigest()[:16]}.json" if cache_dir else None
    if cache_file and cache_file.exists():
        decisions = json.loads(cache_file.read_text(encoding="utf-8"))
        log.info("Decisioni di Claude dalla cache (%s)", cache_file.name)
    else:
        log.info("Chiedo a Claude di decidere su %d voci in dubbio", len(payload))
        decisions = ask_claude(payload, api_key=api_key, model=model, client=client)
        if cache_file and decisions:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
    valid_ids = {(p["item_id"], c["offer_id"]) for p in payload for c in p["candidati"]}
    decisions = [
        {"item_id": d["item_id"], "offer_ids": [o for o in d.get("offer_ids", []) if (d["item_id"], o) in valid_ids]}
        for d in decisions if isinstance(d, dict) and "item_id" in d
    ]
    return apply_decisions(results, decisions, scope)
