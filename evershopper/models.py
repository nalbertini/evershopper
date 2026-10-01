from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class Offer:
    id: str
    name: str
    brand: str | None = None
    format: str | None = None
    price_full: float | None = None
    price_discounted: float | None = None
    discount_pct: float | None = None
    valid_until: str | None = None
    url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Offer":
        return cls(**{k: d.get(k) for k in cls.__dataclass_fields__})
