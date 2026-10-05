"""Learn your palate from your own tasting journal.

Every journal entry is parsed into features (distillery, bottler, peat level,
sherry level, cask types, region, strength and age bands) plus the flavour
words in your notes. A feature's weight is how far above or below your
average you score bottles that have it, shrunk toward zero when there are only
a few examples. A shop bottle's predicted score is your average plus the
weights of the features it shares.

"Similar to" compares a bottle's flavour words (KWM notes, descriptions) and
features with the bottles you scored highest.
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .parser import Parsed, parse

MIN_ENTRIES = 8          # below this the profile stays off and the rule-based palate is used
SHRINK = 3.0             # pseudo-count pulling small-sample weights toward 0
FEATURE_CAP = 15.0       # max total points features can move a prediction
FLAVOUR_CAP = 8.0

FLAVOURS = [
    # smoke / peat / maritime
    "peat", "smoke", "smoky", "ash", "bonfire", "campfire", "embers", "tar", "iodine", "medicinal", "brine",
    "salt", "sea", "seaweed", "bacon", "rubber", "diesel", "engine oil", "char",
    # earthy / savoury / old-school
    "leather", "tobacco", "earth", "earthy", "mushroom", "forest floor", "dunnage", "farmyard", "musty",
    "wax", "waxy", "rancio", "meaty", "sulphur", "coffee", "cocoa", "chocolate", "dark chocolate",
    # orchard & tropical fruit
    "apple", "pear", "peach", "apricot", "nectarine", "pineapple", "mango", "papaya", "passion fruit",
    "banana", "melon", "tropical", "citrus", "lemon", "lime", "orange", "grapefruit", "kiwi",
    # red & dark fruit
    "cherry", "raspberry", "strawberry", "redcurrant", "blackcurrant", "cranberry", "plum", "berry",
    "red fruit", "dark fruit", "raisin", "sultana", "fig", "date", "prune", "jam",
    # sweet / bakery
    "vanilla", "honey", "toffee", "caramel", "butterscotch", "fudge", "custard", "cream", "butter",
    "biscuit", "shortbread", "pastry", "marzipan", "coconut", "sugar", "syrup", "maple",
    # spice / wood / malt / floral
    "spice", "cinnamon", "clove", "nutmeg", "ginger", "pepper", "anise", "licorice", "oak", "cedar",
    "sandalwood", "malt", "cereal", "barley", "bread", "nutty", "almond", "walnut", "hazelnut",
    "floral", "heather", "grass", "herbal", "mint", "eucalyptus", "pine",
    # wine / sherry influence
    "sherry", "wine", "port", "grape",
]
def _flavour_re(f: str) -> re.Pattern:
    if f.endswith("y") and not f.endswith("ey"):
        return re.compile(rf"\b{re.escape(f[:-1])}(?:y|ies)\b", re.I)  # cherry / cherries
    return re.compile(rf"\b{re.escape(f)}(?:s|es|y)?\b", re.I)          # smoke / smokes / smokey


_FLAVOUR_RES = [(f, _flavour_re(f)) for f in FLAVOURS]
# Adjective and noun forms of one idea count once.
_CANON = {"smoky": "smoke", "earthy": "earth", "waxy": "wax"}


def flavour_words(text: str) -> set[str]:
    if not text:
        return set()
    return {_CANON.get(f, f) for f, rx in _FLAVOUR_RES if rx.search(text)}


def _band_abv(abv: float | None) -> str | None:
    if abv is None:
        return None
    return "abv:<46" if abv < 46 else "abv:46-50" if abv < 50 else "abv:50-55" if abv < 55 else "abv:55+"


def _band_age(age: int | None) -> str | None:
    if age is None:
        return None
    return ("age:<10" if age < 10 else "age:10-14" if age < 15 else "age:15-20" if age <= 20
            else "age:21-29" if age < 30 else "age:30+")


def features(p: Parsed) -> set[str]:
    f = set()
    if p.distillery:
        f.add(f"distillery:{p.distillery}")
    for b in p.bottlers:
        f.add(f"bottler:{b}")
    if p.peat != "unknown":
        f.add(f"peat:{p.peat}")
    f.add(f"sherry:{p.sherry_level}")
    for c in p.casks:
        if c not in ("butt", "refill", "hogshead", "finish"):
            f.add(f"cask:{c}")
    if p.region:
        f.add(f"region:{p.region}")
    for band in (_band_abv(p.abv), _band_age(p.age)):
        if band:
            f.add(band)
    if p.single_cask:
        f.add("single_cask")
    if p.cask_strength:
        f.add("cask_strength")
    return f


FEATURE_LABEL = {
    "distillery": "{}", "bottler": "{}", "peat": "peat: {}", "sherry": "sherry: {}", "cask": "{} cask",
    "region": "{}", "abv": "{} abv", "age": "{} yrs",
}


def label(feature: str) -> str:
    if ":" not in feature:
        return feature.replace("_", " ")
    kind, val = feature.split(":", 1)
    val = val.replace("_", " ")
    if kind in ("distillery", "region"):
        val = val.title()
    return FEATURE_LABEL.get(kind, "{}").format(val)


@dataclass
class Entry:
    id: str
    name: str
    score: float
    text: str
    parsed: Parsed
    feats: set[str]
    words: set[str]


@dataclass
class Prediction:
    predicted: float
    percentile: int              # where the prediction sits among your own scores, 0-100
    reasons: list[str] = field(default_factory=list)
    matched: int = 0
    similar: dict | None = None  # {"name", "score", "shared": [...]}

    def to_dict(self) -> dict:
        return {"predicted": round(self.predicted, 1), "pct": self.percentile, "reasons": self.reasons,
                "matched": self.matched, "similar": self.similar}


class TasteProfile:
    def __init__(self, entries: list[Entry]):
        self.entries = entries
        scores = [e.score for e in entries]
        self.mean = statistics.mean(scores) if scores else 0.0
        self.sorted_scores = sorted(scores)
        self.fw = self._weights(lambda e: e.feats)
        self.ww = self._weights(lambda e: e.words)
        cut = self.sorted_scores[int(len(scores) * 0.7)] if scores else 0
        self.favourites = [e for e in entries if e.score >= cut]

    @property
    def active(self) -> bool:
        return len(self.entries) >= MIN_ENTRIES

    def _weights(self, get) -> dict[str, tuple[float, int]]:
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for e in self.entries:
            for f in get(e):
                sums[f] = sums.get(f, 0.0) + (e.score - self.mean)
                counts[f] = counts.get(f, 0) + 1
        return {f: (sums[f] / (counts[f] + SHRINK), counts[f]) for f in sums}

    def percentile(self, score: float) -> int:
        if not self.sorted_scores:
            return 50
        below = sum(1 for s in self.sorted_scores if s < score)
        return round(100 * below / len(self.sorted_scores))

    def predict(self, p: Parsed, text: str) -> Prediction:
        feats = features(p)
        contrib = [(f, self.fw[f][0]) for f in feats if f in self.fw]
        feat_total = max(-FEATURE_CAP, min(FEATURE_CAP, sum(w for _, w in contrib)))
        words = flavour_words(text)
        wcontrib = [(w, self.ww[w][0]) for w in words if w in self.ww]
        flav_total = 0.0
        if wcontrib:
            avg = sum(w for _, w in wcontrib) / len(wcontrib)
            flav_total = max(-FLAVOUR_CAP, min(FLAVOUR_CAP, avg * min(1.0, len(wcontrib) / 4) * 2))
        pred = self.mean + feat_total + flav_total
        lo, hi = self.sorted_scores[0], self.sorted_scores[-1]
        pred = max(lo, min(hi, pred))
        reasons = []
        for f, w in sorted(contrib, key=lambda x: -abs(x[1]))[:3]:
            if abs(w) >= 0.5:
                reasons.append(f"{'+' if w > 0 else '−'} {label(f)} ({self.fw[f][1]} rated)")
        if abs(flav_total) >= 1:
            top = [w for w, v in sorted(wcontrib, key=lambda x: -x[1]) if v > 0][:3]
            if flav_total > 0 and top:
                reasons.append("+ notes you like: " + ", ".join(top))
        return Prediction(pred, self.percentile(pred), reasons, len(contrib), self.similar(p, feats, words))

    def similar(self, p: Parsed, feats: set[str], words: set[str]) -> dict | None:
        best, best_sim, best_shared = None, 0.0, []
        for e in self.favourites:
            shared_words = words & e.words
            union = words | e.words
            sim = (len(shared_words) / len(union)) if union else 0.0
            if p.distillery and f"distillery:{p.distillery}" in e.feats:
                sim += 0.25
            if any(f.startswith("bottler:") for f in feats & e.feats):
                sim += 0.05
            for k in ("peat:", "sherry:"):
                if any(f.startswith(k) for f in feats & e.feats):
                    sim += 0.05
            if sim > best_sim:
                best, best_sim, best_shared = e, sim, sorted(shared_words)
        if best is None or best_sim < 0.3:
            return None
        return {"name": best.name, "score": best.score, "shared": best_shared[:5], "sim": round(best_sim, 2)}

    def summary(self, n: int = 5) -> dict:
        """Top likes/dislikes with enough support, for the app's profile view."""
        ranked = [(f, w, c) for f, (w, c) in self.fw.items() if c >= 2]
        likes = sorted(ranked, key=lambda x: -x[1])[:n]
        dislikes = sorted(ranked, key=lambda x: x[1])[:n]
        wranked = [(f, w, c) for f, (w, c) in self.ww.items() if c >= 2]
        return {
            "entries": len(self.entries), "mean": round(self.mean, 1), "active": self.active,
            "likes": [{"f": label(f), "w": round(w, 1), "n": c} for f, w, c in likes if w > 0],
            "dislikes": [{"f": label(f), "w": round(w, 1), "n": c} for f, w, c in dislikes if w < 0],
            "flavours": [{"f": f, "w": round(w, 1), "n": c} for f, w, c in sorted(wranked, key=lambda x: -x[1])[:8] if w > 0],
        }


def entry_text(raw: dict) -> str:
    return " ".join(str(raw.get(k) or "") for k in ("nose", "palate", "finish", "notes"))


def load_profile(path: str | Path) -> TasteProfile | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entries = []
    for raw in data.get("entries", []):
        try:
            score = float(raw["score"])
        except (KeyError, TypeError, ValueError):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        text = entry_text(raw)
        p = parse(name, str(raw.get("description") or ""))
        entries.append(Entry(str(raw.get("id") or name), name, score, text, p, features(p), flavour_words(text)))
    return TasteProfile(entries)


def blend(rule_palate: int, pred: Prediction, profile: TasteProfile) -> int:
    """Mix the generic palate rules with the learned profile; trust the profile more as it grows."""
    n = len(profile.entries)
    w = 0.5 if n < 30 else 0.7 if n < 100 else 0.8
    return round((1 - w) * rule_palate + w * pred.percentile)
