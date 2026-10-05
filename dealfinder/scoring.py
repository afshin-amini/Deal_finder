"""Rule-based palate + deal scoring.

Two palate tracks, each 0-100:
  peat_earth  - peaty, leathery, earthy (Ardmore, Ledaig, older sherried peat...)
  fruit_earth - unpeated: bourbon-cask orchard/tropical fruit, OR lightly sherried
                with earthy red fruit (refill sherry, sherry hogsheads, finishes)

Then a deal score layered on top from price history.
Every point awarded is recorded in `reasons` so alerts can explain themselves.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

from .parser import Parsed

PEAT_NOTES = ["peat", "smoke", "smoky", "smokey", "leather", "tobacco", "earthy", "earth", "soil",
              "farmyard", "bonfire", "ash", "iodine", "medicinal", "tar", "coal", "embers", "mossy",
              "campfire", "brine", "seaweed", "charred", "bacon", "dunnage", "mushroom", "forest floor"]
FRUIT_NOTES = ["orchard", "apple", "pear", "peach", "apricot", "tropical", "pineapple", "mango",
               "passion fruit", "banana", "citrus", "lemon", "melon", "honey", "vanilla", "nectarine",
               "stone fruit", "grapefruit", "guava"]
RED_EARTH_NOTES = ["red fruit", "cherry", "cherries", "raspberry", "strawberry", "redcurrant",
                   "cranberry", "plum", "red berries", "red apple", "earthy", "leather", "forest floor",
                   "mushroom", "beeswax", "waxy", "dunnage", "damp", "rancio"]


@dataclass
class Score:
    peat_earth: int = 0
    fruit_earth: int = 0
    watchlist: list[str] = field(default_factory=list)
    reasons: dict[str, list[str]] = field(default_factory=lambda: {"peat_earth": [], "fruit_earth": [], "watch": []})

    @property
    def palate(self) -> int:
        return max(self.peat_earth, self.fruit_earth)

    @property
    def track(self) -> str:
        return "peat_earth" if self.peat_earth >= self.fruit_earth else "fruit_earth"

    def to_dict(self) -> dict:
        return {"peat_earth": self.peat_earth, "fruit_earth": self.fruit_earth, "palate": self.palate,
                "track": self.track, "watchlist": self.watchlist, "reasons": self.reasons}


def _notes(text: str, words: list[str]) -> list[str]:
    return [w for w in words if re.search(rf"\b{re.escape(w)}", text)]


def watchlist_hits(text: str, parsed: Parsed, watchlist: dict[str, list[str]]) -> list[str]:
    """Match configured watchlist entries (canonical -> aliases) against title/vendor/description."""
    hits = []
    for canon, aliases in watchlist.items():
        for a in [canon.lower(), *[x.lower() for x in aliases]]:
            if len(a) <= 4 or "&" in a:
                found = re.search(rf"(?<![\w&]){re.escape(a)}(?![\w&])", text)
            else:
                found = a in text
            if found:
                hits.append(canon)
                break
    for b in parsed.bottlers:  # parser's alias table catches spellings the config misses
        if b in watchlist and b not in hits:
            hits.append(b)
    return hits


def palate_score(parsed: Parsed, text: str, watchlist: dict[str, list[str]], has_notes: bool = False) -> Score:
    s = Score()
    t = text.lower()
    rA, rB = s.reasons["peat_earth"], s.reasons["fruit_earth"]
    a = b = 0

    if not parsed.is_whisky or parsed.american:
        return s

    s.watchlist = watchlist_hits(t, parsed, watchlist)
    if s.watchlist:
        s.reasons["watch"] = list(s.watchlist)
    watch_bonus = min(15, 10 * len(s.watchlist))

    # Tasting notes only count where the shop writes them (KWM); elsewhere descriptions are
    # boilerplate, so we lean on distillery/brand/cask instead.
    note_weight = 4 if has_notes else 2

    # ---------------- Track A: peaty / leathery / earthy ----------------
    peat_pts = {"heavy": 45, "medium": 40, "light": 22, "none": 0, "unknown": 0}[parsed.peat]
    if peat_pts:
        a += peat_pts
        rA.append(f"peat:{parsed.peat} +{peat_pts}")
        if "earthy" in parsed.profile:
            a += 10; rA.append("earthy distillery +10")
        if parsed.sherry_level != "none":
            a += 10; rA.append("peat+sherry (leathery) +10")
        if parsed.age and parsed.age >= 15:
            a += 10; rA.append(f"{parsed.age}yo mature peat +10")
        elif parsed.age and parsed.age >= 10:
            a += 5; rA.append(f"{parsed.age}yo +5")
        notes = _notes(t, PEAT_NOTES)
        if notes:
            pts = min(20, note_weight * len(notes))
            a += pts; rA.append(f"notes {','.join(notes[:5])} +{pts}")
        if parsed.cask_strength:
            a += 5; rA.append("cask strength +5")
        if watch_bonus:
            a += watch_bonus; rA.append(f"watchlist +{watch_bonus}")
    elif parsed.peat == "unknown":
        # Maybe the shop describes smoke without naming a known distillery.
        notes = _notes(t, ["peat", "peated", "smoky", "smokey", "smoke", "islay", "ppm"])
        if notes:
            a += 25; rA.append(f"smoke words {','.join(notes)} +25")
            if watch_bonus:
                a += watch_bonus; rA.append(f"watchlist +{watch_bonus}")

    # ------- Track B: unpeated; bourbon-cask fruit or lightly sherried earthy red fruit -------
    if parsed.peat in ("none", "unknown"):
        b += 15; rB.append("unpeated +15")
        casks = set(parsed.casks)
        bourbon = casks & {"bourbon", "first_fill_bourbon", "refill_bourbon"}
        if parsed.sherry_level == "none" and (bourbon or casks & {"hogshead", "refill"}):
            pts = 30 if bourbon else 20
            b += pts; rB.append(f"bourbon/refill cask fruit +{pts}")
            if "fruit" in parsed.profile:
                b += 15; rB.append("fruity distillery +15")
            notes = _notes(t, FRUIT_NOTES)
        elif parsed.sherry_level == "light":
            b += 30; rB.append("lightly sherried +30")
            if "earthy" in parsed.profile or "waxy" in parsed.profile:
                b += 15; rB.append("earthy/waxy distillery +15")
            elif "fruit" in parsed.profile:
                b += 8; rB.append("fruity distillery +8")
            notes = _notes(t, RED_EARTH_NOTES)
        elif parsed.sherry_level == "heavy":
            b += 8; rB.append("heavily sherried (not the target) +8")
            if "earthy" in parsed.profile:
                b += 7; rB.append("earthy distillery +7")
            notes = _notes(t, RED_EARTH_NOTES)
        else:
            # No cask info. Independent bottlings of fruity/earthy distilleries are usually refill/bourbon.
            if "fruit" in parsed.profile or "earthy" in parsed.profile:
                pts = 20 if parsed.bottlers else 10
                b += pts; rB.append(f"{'IB of ' if parsed.bottlers else ''}fruity/earthy distillery, cask unknown +{pts}")
            notes = _notes(t, FRUIT_NOTES + RED_EARTH_NOTES)
        if "red_wine" in casks and parsed.sherry_level != "heavy":
            b += 5; rB.append("red wine cask (red fruit) +5")
        if notes:
            pts = min(20, note_weight * len(notes))
            b += pts; rB.append(f"notes {','.join(notes[:5])} +{pts}")
        if parsed.age and 12 <= parsed.age <= 30:
            b += 5; rB.append(f"{parsed.age}yo +5")
        if parsed.single_cask:
            b += 5; rB.append("single cask +5")
        if watch_bonus:
            b += watch_bonus; rB.append(f"watchlist +{watch_bonus}")
    elif parsed.peat == "light" and parsed.sherry_level != "heavy":
        # Springbank / Ben Nevis / Kilkerran: barely-there peat still fits the earthy-fruit track.
        b += 25; rB.append("lightly peated, earthy-fruit style +25")
        if "earthy" in parsed.profile or "waxy" in parsed.profile:
            b += 10; rB.append("earthy/waxy distillery +10")
        if watch_bonus:
            b += watch_bonus; rB.append(f"watchlist +{watch_bonus}")

    s.peat_earth = min(100, a)
    s.fruit_earth = min(100, b)
    return s


# ------------------------------------------------------------------ deals ----

@dataclass
class Deal:
    score: float = 0.0
    reasons: list[str] = field(default_factory=list)
    drop_pct: float | None = None
    vs_median_pct: float | None = None

    def to_dict(self) -> dict:
        return {"score": round(self.score, 1), "reasons": self.reasons, "drop_pct": self.drop_pct,
                "vs_median_pct": self.vs_median_pct}


def expected_price(parsed: Parsed, cfg: dict) -> float | None:
    """Very rough Alberta shelf-price expectation for a 700-750ml bottle, CAD."""
    if not parsed.age:
        return None
    base = cfg.get("base", 45.0) + cfg.get("per_year", 6.0) * parsed.age
    if parsed.age > 20:
        base += cfg.get("per_year_over_20", 15.0) * (parsed.age - 20)
    if parsed.cask_strength:
        base *= cfg.get("cask_strength_multiplier", 1.15)
    return base


def deal_score(price: float | None, history: list[float], prev_price: float | None,
               compare_at: float | None, parsed: Parsed, value_cfg: dict) -> Deal:
    d = Deal()
    if price is None or price <= 0:
        return d
    ml = parsed.volume_ml or 750
    norm = price * 750 / ml if 300 <= ml <= 1750 else price

    if prev_price and prev_price > price:
        d.drop_pct = round(100 * (prev_price - price) / prev_price, 1)
        d.score += min(40, d.drop_pct * 1.5)
        d.reasons.append(f"down {d.drop_pct}% from ${prev_price:.2f}")
    if len(history) >= 3:
        med = statistics.median(history)
        if med > price:
            d.vs_median_pct = round(100 * (med - price) / med, 1)
            d.score += min(25, d.vs_median_pct)
            d.reasons.append(f"{d.vs_median_pct}% under usual ${med:.2f}")
        if price <= min(history):
            d.score += 5
            d.reasons.append("lowest price seen")
    if compare_at and compare_at > price:
        pct = round(100 * (compare_at - price) / compare_at, 1)
        d.score += min(20, pct)
        d.reasons.append(f"shop sale {pct}% off ${compare_at:.2f}")
    exp = expected_price(parsed, value_cfg)
    if exp and norm < exp:
        pct = round(100 * (exp - norm) / exp, 1)
        d.score += min(15, pct / 2)
        d.reasons.append(f"good value for {parsed.age}yo (~{pct:.0f}% under typical)")
    return d
