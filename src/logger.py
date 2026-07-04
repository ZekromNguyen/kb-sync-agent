"""Structured console logging + the `logs/last_run.json` artefact writer."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

_LOGGER_NAME = "kb_mini_agent"


def setup_logging(level: str = "INFO", log_dir: str = "logs") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(message)s")
        )
        logger.addHandler(handler)
    return logger


def write_last_run(log_dir: str, run: dict) -> str:
    """Persist the structured per-run summary. Overwrites each run."""
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, "last_run.json")
    payload = {**run, "finished_at": datetime.now(timezone.utc).isoformat()}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    return path
