"""Abbinamento tra voci generiche della lista ("latte") e offerte specifiche
("Granarolo Latte Intero 1L").

Prima passata locale: normalizzazione, radici, sinonimi ed esclusioni. Ogni
coppia voce/offerta finisce in "sicuro", "dubbio" o niente. I dubbi si possono
far decidere all'API Claude (evershopper.llm) se abilitata.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .models import Offer
from .reminders import ShoppingItem

DATA_FILE = Path(__file__).resolve().parent / "data" / "sinonimi.yaml"

STOPWORDS = {
    "di", "da", "del", "dello", "della", "dei", "degli", "delle", "dell",
    "al", "allo", "alla", "ai", "agli", "alle", "all", "a", "ad",
    "con", "per", "e", "ed", "o", "il", "lo", "la", "i", "gli", "le", "l",
    "un", "uno", "una", "in", "su", "tra", "fra",
}
# Parole che, prima della voce, la rendono un ingrediente o un gusto:
# "cioccolato AL latte", "gelato GUSTO caffè", "biscotti CON gocce di cioccolato".
MODIFIERS = {
    "di", "del", "dello", "della", "dei", "degli", "delle", "dell",
    "al", "allo", "alla", "ai", "agli", "alle", "all", "con",
    "gusto", "aroma", "sapore", "ripieno", "ripieni", "ripiena", "ripiene", "farcito", "farcita",
}
UNITS = {"g", "gr", "kg", "mg", "ml", "cl", "dl", "l", "lt", "pz", "pezzi", "conf", "x", "cad"}
QUANTITY = re.compile(r"^(\d+([.,]\d+)?)(x\d+)?(g|gr|kg|mg|ml|cl|dl|l|lt|pz)?$|^x\d+$")

SICURO = "sicuro"
DUBBIO = "dubbio"
_RANK = {DUBBIO: 0, SICURO: 1}


def normalize(text: str) -> list[str]:
    """Parole minuscole, senza accenti e punteggiatura (stopword incluse)."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text.replace("'", " "))


def stem(word: str) -> str:
    """Radice grezza per singolare/plurale: pomodoro/pomodori, mela/mele, fungo/funghi."""
    if len(word) >= 4 and word[-1] in "aeiou":
        word = word[:-1]
        if len(word) >= 4 and word[-1] == "h" and word[-2] in "cg":
            word = word[:-1]
    return word


def content_stems(text: str) -> list[str]:
    return [stem(w) for w in normalize(text) if w not in STOPWORDS and w not in UNITS and not QUANTITY.match(w)]


def _similar(a: str, b: str) -> bool:
    if a == b:
        return True
    if len(a) >= 5 and b.startswith(a) and len(b) - len(a) <= 3:  # "yogurt" → "yogurtino"
        return True
    return len(a) >= 5 and len(b) >= 5 and difflib.SequenceMatcher(None, a, b).ratio() >= 0.88


@dataclass
class Candidate:
    offer: Offer
    status: str  # SICURO | DUBBIO
    score: float
    reason: str = ""
    source: str = "fuzzy"  # fuzzy | llm


@dataclass
class ItemMatch:
    item: ShoppingItem
    matches: list[Candidate] = field(default_factory=list)  # offerte abbinate
    doubts: list[Candidate] = field(default_factory=list)   # da decidere (LLM o a mano)

    def to_dict(self) -> dict:
        def cand(c: Candidate) -> dict:
            return {"offer": c.offer.to_dict(), "status": c.status, "score": round(c.score, 2),
                    "reason": c.reason, "source": c.source}
        return {"item": self.item.to_dict(), "matches": [cand(c) for c in self.matches],
                "doubts": [cand(c) for c in self.doubts]}


class Matcher:
    def __init__(self, synonyms: dict | None = None, exclude: dict | None = None, max_doubts: int = 8):
        base = yaml.safe_load(DATA_FILE.read_text(encoding="utf-8"))
        self.synonyms = self._merge(base.get("synonyms", {}), synonyms or {})
        self.exclude = self._merge(base.get("exclude", {}), exclude or {})
        self.max_doubts = max_doubts

    @staticmethod
    def _merge(*maps: dict) -> dict[str, list[str]]:
        """Unisce le liste per voce (la config aggiunge a quelle di base, non le sostituisce)."""
        out: dict[str, list[str]] = {}
        for m in maps:
            for k, vals in m.items():
                bucket = out.setdefault(" ".join(normalize(str(k))), [])
                bucket.extend(str(v) for v in (vals or []) if str(v) not in bucket)
        return out

    def queries(self, item: ShoppingItem) -> list[list[str]]:
        """La voce e i suoi sinonimi, come liste di radici."""
        key = " ".join(normalize(item.title))
        out = []
        for text in [item.title, *self.synonyms.get(key, [])]:
            stems = content_stems(text)
            if stems and stems not in out:
                out.append(stems)
        return out

    def score(self, item: ShoppingItem, offer: Offer) -> Candidate | None:
        name_words = normalize(offer.name)
        name_stems = [stem(w) for w in name_words]
        offer_stems = set(content_stems(offer.name)) | set(content_stems(offer.brand or ""))

        key = " ".join(normalize(item.title))
        excluded = {stem(w) for t in self.exclude.get(key, []) for w in normalize(t)}
        if excluded & offer_stems:
            return None

        best: Candidate | None = None
        for q in self.queries(item):
            covered = [t for t in q if any(_similar(t, o) for o in offer_stems)]
            coverage = len(covered) / len(q)
            if coverage < 0.5:
                continue
            if coverage < 1:
                cand = Candidate(offer, DUBBIO, coverage, f"trovate {len(covered)} parole su {len(q)}")
            else:
                cand = Candidate(offer, SICURO, 1.0)
                head = q[0]
                idx = next((i for i, s in enumerate(name_stems) if _similar(head, s)), None)
                if idx is not None and idx > 0 and name_words[idx - 1] in MODIFIERS:
                    cand = Candidate(offer, DUBBIO, 0.8, f"«{item.title.strip().lower()}» sembra un ingrediente o un gusto")
            if best is None or (_RANK[cand.status], cand.score) > (_RANK[best.status], best.score):
                best = cand
            if best.status == SICURO:
                break
        if best and item.notes:
            # Le note ("intero", "senza lattosio") ordinano le offerte, non le escludono.
            note_stems = content_stems(item.notes)
            hits = sum(1 for t in note_stems if any(_similar(t, o) for o in offer_stems))
            best.score += 0.1 * hits
        return best

    def match(self, items: list[ShoppingItem], offers: list[Offer]) -> list[ItemMatch]:
        results = []
        for item in items:
            res = ItemMatch(item)
            for offer in offers:
                cand = self.score(item, offer)
                if cand is None:
                    continue
                (res.matches if cand.status == SICURO else res.doubts).append(cand)
            res.matches.sort(key=lambda c: (-c.score, -(c.offer.discount_pct or 0)))
            res.doubts.sort(key=lambda c: (-c.score, -(c.offer.discount_pct or 0)))
            res.doubts = res.doubts[: self.max_doubts]
            results.append(res)
        return results
