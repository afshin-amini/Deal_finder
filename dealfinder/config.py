from __future__ import annotations

import os
import tomllib
from pathlib import Path


def load(path: str | Path = "config.toml") -> dict:
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    ntfy = cfg.setdefault("ntfy", {})
    ntfy["topic"] = os.environ.get("NTFY_TOPIC") or ntfy.get("topic") or None
    ntfy["token"] = os.environ.get("NTFY_TOKEN") or ntfy.get("token") or None
    if os.environ.get("NTFY_SERVER"):
        ntfy["server"] = os.environ["NTFY_SERVER"]
    if os.environ.get("DEALFINDER_DB"):
        cfg["db_path"] = os.environ["DEALFINDER_DB"]
    cfg.setdefault("http", {})
    cfg.setdefault("alerts", {})
    cfg.setdefault("value", {})
    cfg.setdefault("watchlist", {})
    cfg.setdefault("shops", [])
    return cfg
