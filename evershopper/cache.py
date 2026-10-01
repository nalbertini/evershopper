"""Cache JSON delle offerte (normalizzate + risposte grezze), una per giorno."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Offer


def save(
    cache_dir: Path, offers: list[Offer], raw_pages: list[Any], meta: dict, now: datetime | None = None
) -> Path:
    now = now or datetime.now()
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y-%m-%d")
    raw_dir = cache_dir / f"raw-{stamp}"
    raw_dir.mkdir(exist_ok=True)
    for i, page in enumerate(raw_pages, 1):
        (raw_dir / f"page-{i:02d}.json").write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    path = cache_dir / f"offers-{stamp}.json"
    payload = {
        "fetched_at": now.isoformat(timespec="seconds"),
        **meta,
        "count": len(offers),
        "offers": [o.to_dict() for o in offers],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_latest(cache_dir: Path) -> tuple[dict, list[Offer]] | None:
    files = sorted(cache_dir.glob("offers-*.json"))
    if not files:
        return None
    payload = json.loads(files[-1].read_text(encoding="utf-8"))
    return payload, [Offer.from_dict(o) for o in payload["offers"]]
