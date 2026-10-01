"""Riassume una cattura di discover.py e mette in evidenza gli endpoint
che sembrano restituire offerte (chiavi tipo price/discount/promo).

Uso:
    python discovery/summarize.py                 # ultima cattura
    python discovery/summarize.py path/file.jsonl
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

CAPTURE_DIR = Path(__file__).resolve().parent / "captures"
OFFER_HINTS = re.compile(r"(price|prezzo|discount|sconto|promo|offer|offert|saving|strike)", re.I)
# Gli ID numerici nel path diventano {id} per raggruppare le chiamate simili.
NUMERIC_SEGMENT = re.compile(r"/\d+(?=/|$)")


def key_shape(obj, depth=0, max_depth=5):
    """Struttura delle chiavi del JSON, senza valori (niente dati personali)."""
    if depth >= max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: key_shape(v, depth + 1, max_depth) for k, v in list(obj.items())[:25]}
    if isinstance(obj, list):
        return [key_shape(obj[0], depth + 1, max_depth)] if obj else []
    return type(obj).__name__


def main() -> int:
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
    else:
        files = sorted(CAPTURE_DIR.glob("capture-*.jsonl"))
        if not files:
            print("Nessuna cattura trovata: lancia prima discovery/discover.py")
            return 1
        path = files[-1]

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        groups[(rec["method"], NUMERIC_SEGMENT.sub("/{id}", rec["endpoint"]))].append(rec)

    print(f"Cattura: {path.name} – {sum(map(len, groups.values()))} chiamate, {len(groups)} endpoint\n")
    candidates = []
    for (method, endpoint), recs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        sample = next((r["body_sample"] for r in recs if r["body_sample"]), None)
        hinted = bool(sample and OFFER_HINTS.search(sample))
        mark = "★" if hinted else " "
        param_names = sorted({k for r in recs for k in r["params"]})
        print(f"{mark} {len(recs):3d}× {method:6s} {endpoint}")
        if param_names:
            print(f"         params: {', '.join(param_names)}")
        if hinted:
            candidates.append((method, endpoint, recs, sample))

    print(f"\n{len(candidates)} endpoint candidati (★ = il JSON contiene chiavi di prezzo/sconto)\n")
    for method, endpoint, recs, sample in candidates:
        print("=" * 80)
        print(f"{method} {endpoint}")
        print(f"pagina: {recs[0]['page']}")
        print(f"esempi di parametri: {[r['params'] for r in recs[:3]]}")
        files = [r["body_file"] for r in recs if r.get("body_file")]
        if files:
            print(f"risposte complete salvate: {', '.join(files[:5])}")
        try:
            shape = key_shape(json.loads(sample))
            print("struttura JSON:")
            print(json.dumps(shape, indent=2, ensure_ascii=False)[:3000])
        except json.JSONDecodeError:
            print("(body troncato, vedi body_sample nel file di cattura)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
