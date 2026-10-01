from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


LAUNCHD_LOG = "launchd.log"
LAUNCHD_MAX_BYTES = 1_000_000


def rotate_launchd_log(log_dir: Path) -> None:
    """launchd scrive stdout/stderr in un file che non ruota da solo: lo si ruota qui, a ogni avvio."""
    path = log_dir / LAUNCHD_LOG
    if path.exists() and path.stat().st_size > LAUNCHD_MAX_BYTES:
        path.replace(log_dir / f"{LAUNCHD_LOG}.1")


def setup(log_dir: Path, verbose: bool = False) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    rotate_launchd_log(log_dir)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(log_dir / "evershopper.log", maxBytes=500_000, backupCount=3)
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [file_handler, console]
