"""Caricamento di config.yaml con i default di config.example.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from . import ROOT

EXAMPLE = ROOT / "config.example.yaml"
DEFAULT = ROOT / "config.yaml"


class ConfigError(Exception):
    pass


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


def load(path: Path | None = None) -> dict[str, Any]:
    cfg = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    path = path or DEFAULT
    if path.exists():
        cfg = _merge(cfg, yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    elif path != DEFAULT:
        raise ConfigError(f"File di configurazione non trovato: {path}")
    return cfg


def resolve(path: str) -> Path:
    """Percorsi relativi della config interpretati dalla root del repo."""
    p = Path(path).expanduser()
    return p if p.is_absolute() else ROOT / p
