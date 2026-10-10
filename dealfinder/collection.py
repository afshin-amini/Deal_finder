"""Your watchlist and bottles, stored next to the tasting journal (docs/data/journal.json).

  "watch": [{"id", "name", "keys": [shop listing keys], "target": 120.0, "note", "suggested": bool}]
  "shelf": [{"id", "name", "key", "price", "where", "bought", "opened", "level", "notes"}]

A watch item linked to shop listings (`keys`) matches exactly; an unlinked one
matches listings whose title contains every word of its name. Suggested items
(from your notes, not yet confirmed in the app) are ignored until accepted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

_STOP = {"the", "old", "single", "malt", "scotch", "whisky", "whiskey", "abv", "ml", "cl", "year", "years", "yo", "yr", "yrs"}


def norm_tokens(name: str) -> list[str]:
    n = str(name or "").lower().replace("&amp;", "&")
    n = re.sub(r"\d{2}(?:\.\d+)?\s*%.*$", "", n)                 # drop "46% abv / 700mL" tails
    n = re.sub(r"\b(\d+)\s*(?:years?|yrs?|y\.?o\.?)\b", r"\1", n)
    return [t for t in re.split(r"[^a-z0-9&]+", n) if t and t not in _STOP]


def name_matches(name: str, title: str) -> bool:
    want, have = norm_tokens(name), set(norm_tokens(title))
    return len(want) >= 2 and all(t in have for t in want)


@dataclass
class WatchItem:
    id: str
    name: str
    keys: list[str] = field(default_factory=list)
    target: float | None = None
    note: str = ""

    def matches(self, key: str, title: str) -> bool:
        return key in self.keys if self.keys else name_matches(self.name, title)


@dataclass
class Collection:
    watch: list[WatchItem] = field(default_factory=list)
    owned_keys: set[str] = field(default_factory=set)
    owned_names: list[str] = field(default_factory=list)

    def watching(self, key: str, title: str) -> WatchItem | None:
        hits = [w for w in self.watch if w.matches(key, title)]
        # Prefer the item with a target (most specific ask).
        return sorted(hits, key=lambda w: w.target is None)[0] if hits else None

    def owns(self, key: str, title: str) -> bool:
        return key in self.owned_keys or any(name_matches(n, title) for n in self.owned_names)


def load_collection(path: str | Path) -> Collection:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Collection()
    watch = []
    for w in doc.get("watch", []):
        if w.get("suggested") or not w.get("name"):
            continue
        try:
            target = float(w["target"]) if w.get("target") not in (None, "") else None
        except (TypeError, ValueError):
            target = None
        watch.append(WatchItem(str(w.get("id") or w["name"]), str(w["name"]), list(w.get("keys") or []), target,
                               str(w.get("note") or "")))
    shelf = [b for b in doc.get("shelf", []) if (b.get("level") or "") != "finished"]
    return Collection(watch, {b["key"] for b in shelf if b.get("key")},
                      [b["name"] for b in shelf if b.get("name") and not b.get("key")])
