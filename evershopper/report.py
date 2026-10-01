"""Riepilogo leggibile delle voci della lista in offerta, ordinate per sconto."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import date, datetime

from .matching import ItemMatch
from .models import Offer
from .reminders import ShoppingItem


@dataclass
class Line:
    item: ShoppingItem
    offers: list[Offer]  # sopra soglia, sconto decrescente

    @property
    def best(self) -> float:
        return self.offers[0].discount_pct or 0


@dataclass
class Report:
    title: str
    offers_date: str
    store: str = ""
    lines: list[Line] = field(default_factory=list)
    below: list[tuple[ShoppingItem, float]] = field(default_factory=list)  # in offerta sotto soglia
    doubts: list[tuple[ShoppingItem, list[Offer]]] = field(default_factory=list)
    none: list[ShoppingItem] = field(default_factory=list)
    threshold: float = 0
    warnings: list[str] = field(default_factory=list)


def build(
    results: list[ItemMatch],
    *,
    title: str,
    offers_date: str,
    store: str = "",
    threshold: float = 0,
    max_per_item: int = 2,
) -> Report:
    rep = Report(title=title, offers_date=offers_date, store=store, threshold=threshold)
    for r in results:
        good = sorted((c.offer for c in r.matches if (c.offer.discount_pct or 0) >= threshold),
                      key=lambda o: -(o.discount_pct or 0))
        if good:
            rep.lines.append(Line(r.item, good[:max_per_item]))
        elif r.matches:
            rep.below.append((r.item, max(c.offer.discount_pct or 0 for c in r.matches)))
        elif r.doubts:
            rep.doubts.append((r.item, [c.offer for c in r.doubts[:3]]))
        else:
            rep.none.append(r.item)
    rep.lines.sort(key=lambda line: -line.best)
    return rep


def euro(x: float | None) -> str:
    return "?" if x is None else f"{x:.2f}".replace(".", ",") + " €"


def short_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        d = datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            d = date.fromisoformat(value[:10])
        except ValueError:
            return value
    return f"{d:%d/%m}"


def label(o: Offer) -> str:
    return " ".join(x for x in (o.brand, o.name, o.format) if x)


def offer_text(o: Offer) -> str:
    parts = [f"{label(o)}: {euro(o.price_discounted)}"]
    if o.price_full is not None:
        parts.append(f" invece di {euro(o.price_full)}")
    if o.discount_pct is not None:
        parts.append(f" (-{o.discount_pct:g}%)")
    until = short_date(o.valid_until)
    if until:
        parts.append(f", fino al {until}")
    return "".join(parts)


def header(rep: Report) -> str:
    where = f" · {rep.store}" if rep.store else ""
    return f"{rep.title}{where} · offerte del {short_date(rep.offers_date) or rep.offers_date}"


def to_text(rep: Report) -> str:
    out = [header(rep), ""]
    out += [f"⚠️ {w}" for w in rep.warnings] + ([""] if rep.warnings else [])
    if rep.lines:
        out.append(f"In offerta ({len(rep.lines)}):")
        for line in rep.lines:
            out.append(f"• {line.item.title}")
            out += [f"    {offer_text(o)}" for o in line.offers]
    else:
        out.append("Nessuna voce della lista in offerta" + (f" con sconto ≥ {rep.threshold:g}%" if rep.threshold else ""))
    if rep.below:
        out += ["", "Sconto sotto soglia: " + ", ".join(f"{i.title} (-{p:g}%)" for i, p in rep.below)]
    if rep.doubts:
        out += ["", "Da verificare:"]
        for item, offers in rep.doubts:
            out.append(f"• {item.title}? " + "; ".join(offer_text(o) for o in offers))
    if rep.none:
        out += ["", "Non in offerta: " + ", ".join(i.title for i in rep.none)]
    return "\n".join(out).rstrip() + "\n"


def to_html(rep: Report) -> str:
    e = html.escape

    def offer_html(o: Offer) -> str:
        text = e(offer_text(o))
        return f'<a href="{e(o.url)}">{text}</a>' if o.url else text

    # Note usa la prima riga come titolo della nota: deve essere sempre `rep.title`.
    out = [f"<h1>{e(rep.title)}</h1>", f"<p>{e(header(rep))}</p>"]
    out += [f"<p>⚠️ {e(w)}</p>" for w in rep.warnings]
    if rep.lines:
        out.append(f"<h2>In offerta ({len(rep.lines)})</h2><ul>")
        for line in rep.lines:
            subs = "".join(f"<li>{offer_html(o)}</li>" for o in line.offers)
            out.append(f"<li><b>{e(line.item.title)}</b><ul>{subs}</ul></li>")
        out.append("</ul>")
    else:
        out.append("<p>Nessuna voce della lista in offerta"
                   + (f" con sconto ≥ {rep.threshold:g}%" if rep.threshold else "") + ".</p>")
    if rep.below:
        out.append("<p><b>Sconto sotto soglia:</b> "
                   + e(", ".join(f"{i.title} (-{p:g}%)" for i, p in rep.below)) + "</p>")
    if rep.doubts:
        out.append("<h2>Da verificare</h2><ul>")
        for item, offers in rep.doubts:
            out.append(f"<li><b>{e(item.title)}?</b> " + "; ".join(offer_html(o) for o in offers) + "</li>")
        out.append("</ul>")
    if rep.none:
        out.append("<p><b>Non in offerta:</b> " + e(", ".join(i.title for i in rep.none)) + "</p>")
    return "\n".join(out)


def notification(rep: Report) -> tuple[str, str]:
    if not rep.lines:
        return rep.title, "Nessuna voce della lista in offerta questa settimana"
    top = ", ".join(f"{line.item.title} -{line.best:g}%" for line in rep.lines[:4])
    more = f" e altre {len(rep.lines) - 4}" if len(rep.lines) > 4 else ""
    return rep.title, f"{len(rep.lines)} voci in offerta: {top}{more}"


def mark_texts(rep: Report) -> dict[str, str]:
    """Testo dell'etichetta per ogni promemoria in offerta (solo l'offerta migliore)."""
    out = {}
    for line in rep.lines:
        o = line.offers[0]
        text = f"-{o.discount_pct:g}% {label(o)} {euro(o.price_discounted)}"
        until = short_date(o.valid_until)
        out[line.item.id] = text + (f" fino al {until}" if until else "")
    return out
