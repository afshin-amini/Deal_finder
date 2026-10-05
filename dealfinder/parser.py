"""Pull structured facts out of free-text whisky listings.

Shops only reliably give us a title (and sometimes a description), so this is
regex + lookup tables. Everything is best-effort: a field is None when unknown.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field

# --- distillery knowledge ------------------------------------------------------
# peat: "heavy" | "medium" | "light" | "none" — the distillery's *house* style.
# Specific bottlings override this via text (e.g. "unpeated", "Port Charlotte").
# profile tags feed the scorer: "fruit" (bourbon-cask fruit), "earthy", "waxy", "sherry_house".
DISTILLERIES: dict[str, dict] = {
    # Islay
    "ardbeg": {"peat": "heavy", "region": "islay", "profile": ["earthy"]},
    "laphroaig": {"peat": "heavy", "region": "islay", "profile": ["earthy"]},
    "lagavulin": {"peat": "heavy", "region": "islay", "profile": ["earthy"]},
    "caol ila": {"peat": "heavy", "region": "islay", "profile": ["fruit"]},
    "kilchoman": {"peat": "heavy", "region": "islay", "profile": []},
    "bowmore": {"peat": "medium", "region": "islay", "profile": ["fruit"]},
    "port charlotte": {"peat": "heavy", "region": "islay", "profile": ["earthy"]},
    "octomore": {"peat": "heavy", "region": "islay", "profile": []},
    "bruichladdich": {"peat": "none", "region": "islay", "profile": ["fruit"]},
    "bunnahabhain": {"peat": "none", "region": "islay", "profile": ["earthy", "sherry_house"]},
    "ardnahoe": {"peat": "heavy", "region": "islay", "profile": []},
    "port ellen": {"peat": "heavy", "region": "islay", "profile": ["earthy"]},
    "finlaggan": {"peat": "heavy", "region": "islay", "profile": []},
    "williamson": {"peat": "heavy", "region": "islay", "profile": []},  # teaspooned Laphroaig
    # Islands / Campbeltown
    "talisker": {"peat": "medium", "region": "islands", "profile": ["earthy"]},
    "ledaig": {"peat": "heavy", "region": "islands", "profile": ["earthy"]},
    "tobermory": {"peat": "none", "region": "islands", "profile": ["earthy"]},
    "highland park": {"peat": "light", "region": "islands", "profile": ["sherry_house"]},
    "scapa": {"peat": "none", "region": "islands", "profile": ["fruit"]},
    "arran": {"peat": "none", "region": "islands", "profile": ["fruit"]},
    "lochranza": {"peat": "none", "region": "islands", "profile": ["fruit"]},
    "lagg": {"peat": "heavy", "region": "islands", "profile": []},
    "jura": {"peat": "light", "region": "islands", "profile": []},
    "springbank": {"peat": "light", "region": "campbeltown", "profile": ["earthy", "fruit", "waxy"]},
    "longrow": {"peat": "heavy", "region": "campbeltown", "profile": ["earthy"]},
    "hazelburn": {"peat": "none", "region": "campbeltown", "profile": ["fruit"]},
    "glen scotia": {"peat": "light", "region": "campbeltown", "profile": ["earthy"]},
    "kilkerran": {"peat": "light", "region": "campbeltown", "profile": ["earthy", "waxy"]},
    "glengyle": {"peat": "light", "region": "campbeltown", "profile": ["earthy"]},
    # Highlands
    "ardmore": {"peat": "medium", "region": "highlands", "profile": ["earthy"]},
    "ardlair": {"peat": "none", "region": "highlands", "profile": ["fruit"]},  # unpeated Ardmore
    "ben nevis": {"peat": "light", "region": "highlands", "profile": ["earthy", "fruit"]},
    "clynelish": {"peat": "none", "region": "highlands", "profile": ["fruit", "waxy"]},
    "brora": {"peat": "medium", "region": "highlands", "profile": ["waxy", "earthy"]},
    "balblair": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "glen garioch": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "glendronach": {"peat": "none", "region": "highlands", "profile": ["sherry_house"]},
    "glenmorangie": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "dalmore": {"peat": "none", "region": "highlands", "profile": ["sherry_house"]},
    "teaninich": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "tomatin": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "royal brackla": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "aberfeldy": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "edradour": {"peat": "none", "region": "highlands", "profile": ["sherry_house"]},
    "ballechin": {"peat": "heavy", "region": "highlands", "profile": ["earthy"]},
    "deanston": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "glengoyne": {"peat": "none", "region": "highlands", "profile": ["sherry_house"]},
    "loch lomond": {"peat": "light", "region": "highlands", "profile": ["fruit"]},
    "croftengea": {"peat": "heavy", "region": "highlands", "profile": ["earthy"]},
    "inchmurrin": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "old pulteney": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "wolfburn": {"peat": "light", "region": "highlands", "profile": []},
    "fettercairn": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "glencadam": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    "blair athol": {"peat": "none", "region": "highlands", "profile": ["earthy"]},
    "glenturret": {"peat": "none", "region": "highlands", "profile": []},
    "tullibardine": {"peat": "none", "region": "highlands", "profile": []},
    "glen ord": {"peat": "none", "region": "highlands", "profile": ["fruit"]},
    # Speyside
    "mortlach": {"peat": "none", "region": "speyside", "profile": ["earthy", "sherry_house"]},
    "benrinnes": {"peat": "none", "region": "speyside", "profile": ["earthy"]},
    "dailuaine": {"peat": "none", "region": "speyside", "profile": ["earthy"]},
    "craigellachie": {"peat": "none", "region": "speyside", "profile": ["earthy", "fruit"]},
    "linkwood": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glen elgin": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "longmorn": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glenlossie": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "mannochmore": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "aultmore": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glen moray": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glenrothes": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glen grant": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glenlivet": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glenfiddich": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "balvenie": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "macallan": {"peat": "none", "region": "speyside", "profile": ["sherry_house"]},
    "glenfarclas": {"peat": "none", "region": "speyside", "profile": ["sherry_house"]},
    "aberlour": {"peat": "none", "region": "speyside", "profile": ["sherry_house"]},
    "glenallachie": {"peat": "none", "region": "speyside", "profile": ["sherry_house"]},
    "benromach": {"peat": "light", "region": "speyside", "profile": ["earthy", "fruit"]},
    "benriach": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glenglassaugh": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "strathisla": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "miltonduff": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glentauchers": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "tamdhu": {"peat": "none", "region": "speyside", "profile": ["sherry_house"]},
    "knockdhu": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "ancnoc": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "cragganmore": {"peat": "none", "region": "speyside", "profile": ["earthy"]},
    "inchgower": {"peat": "none", "region": "speyside", "profile": ["earthy"]},
    "glen keith": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "speyburn": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "tormore": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "allt-a-bhainne": {"peat": "light", "region": "speyside", "profile": []},
    "tomintoul": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "cardhu": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "dufftown": {"peat": "none", "region": "speyside", "profile": ["earthy"]},
    "auchroisk": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "caperdonich": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "imperial": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "glen spey": {"peat": "none", "region": "speyside", "profile": ["fruit"]},
    "royal lochnagar": {"peat": "none", "region": "highlands", "profile": ["earthy"]},
    # Lowlands
    "auchentoshan": {"peat": "none", "region": "lowlands", "profile": ["fruit"]},
    "glenkinchie": {"peat": "none", "region": "lowlands", "profile": ["fruit"]},
    "bladnoch": {"peat": "none", "region": "lowlands", "profile": ["fruit"]},
    "ailsa bay": {"peat": "heavy", "region": "lowlands", "profile": []},
    "rosebank": {"peat": "none", "region": "lowlands", "profile": ["fruit"]},
    "littlemill": {"peat": "none", "region": "lowlands", "profile": ["fruit"]},
}

# Peated sub-brands / expressions of normally unpeated distilleries.
PEATED_EXPRESSIONS = [
    "mòine", "moine", "staoisha", "toiteach", "smokehead", "peat monster", "big peat",
    "benriach smoke", "smoke season", "curiositas", "peated", "heavily peated",
    "lightly peated", "port charlotte", "octomore", "longrow", "ledaig", "croftengea",
    "ballechin", "torfa", "the peat", "sheep dip", "peat reek", "smoky",
]
UNPEATED_MARKERS = ["unpeated", "un-peated", "non-peated", "non peated", "not peated", "no peat"]

# Independent bottlers and brands. Keys are canonical names; values are aliases (lowercase).
BOTTLERS: dict[str, list[str]] = {
    "Gordon & MacPhail": ["gordon & macphail", "gordon and macphail", "gordon & mcphail", "g&m", "g & m",
                          "connoisseurs choice", "connoisseur's choice", "discovery range"],
    "Decadent Drinks": ["decadent drinks", "decadent dreams", "decadent drams", "decadent"],
    "Whisky Sponge": ["whisky sponge", "whiskysponge", "sponge"],
    "Single Malts of Scotland": ["single malts of scotland", "smos"],
    "Thompson Bros": ["thompson bros", "thompson brothers", "dornoch distillery"],
    "Berry Bros & Rudd": ["berry bros", "berry brothers", "bbr", "berrys'", "berry's", "berrys own"],
    "Signatory": ["signatory", "un-chillfiltered collection", "cask strength collection"],
    "Cadenhead": ["cadenhead", "cadenhead's", "wm cadenhead"],
    "Hunter Laing": ["hunter laing", "old malt cask", "old & rare", "first editions"],
    "Douglas Laing": ["douglas laing", "xop", "old particular", "provenance"],
    "SMWS": ["smws", "scotch malt whisky society"],
    "Adelphi": ["adelphi"],
    "That Boutique-y Whisky Company": ["boutique-y", "boutiquey"],
    "Watt Whisky": ["watt whisky"],
    "Murray McDavid": ["murray mcdavid"],
    "A.D. Rattray": ["ad rattray", "a.d. rattray", "a. d. rattray"],
    "Elixir Distillers": ["elixir distillers", "elements of islay", "black tot"],
    "Cask Noir": ["cask noir"],
    "The Whisky Cellar": ["the whisky cellar", "whisky cellar"],
}

# Short/ambiguous aliases need word boundaries and specific context to avoid false hits.
_SHORT_ALIASES = {"g&m", "g & m", "bbr", "smos", "sponge", "decadent", "xop", "provenance"}

CASK_PATTERNS: list[tuple[str, str]] = [
    ("first_fill_bourbon", r"(first|1st)[\s-]*fill\s+(ex[\s-]*)?(bourbon|american oak|barrel)"),
    ("refill_bourbon", r"refill\s+(ex[\s-]*)?(bourbon|american oak|barrel|hogshead|hhd)"),
    ("bourbon", r"\b(ex[\s-]*)?bourbon\s*(cask|barrel|hogshead|hhd|wood|matured)?|\bamerican oak\b|\bbarrel\b"),
    ("hogshead", r"\bhogshead|\bhhd\b|\bhogs?\b"),
    ("refill", r"\brefill\b"),
    ("first_fill_sherry", r"(first|1st)[\s-]*fill\s+(oloroso|sherry|px|pedro)"),
    ("refill_sherry", r"refill\s+(oloroso|sherry|px|pedro|butt)|(2nd|second)[\s-]*fill\s+(oloroso|sherry)"),
    ("sherry_hogshead", r"(sherry|oloroso)\s+(hogshead|hhd)"),
    ("sherry_butt", r"(sherry|oloroso|px)\s+butt"),
    ("px", r"\bp\.?x\.?\b|pedro xim[eé]nez"),
    ("oloroso", r"\boloroso\b"),
    ("fino", r"\bfino\b|\bmanzanilla\b|\bamontillado\b|\bpalo cortado\b"),
    ("sherry", r"\bsherry\b|\bsherried\b|\bjerez\b"),
    ("port", r"\bport\s*(pipe|cask|finish|wood)|\bruby port|\btawny\b"),
    ("red_wine", r"red wine|\bbordeaux\b|\bburgundy\b|\bpinot noir\b|\bcabernet\b|\bbarolo\b|\bmadeira\b|\bmarsala\b|\brioja\b|\bsauternes\b|\bwine cask"),
    ("rum", r"\brum\s*(cask|barrel|finish)"),
    ("virgin_oak", r"virgin oak|new oak|\bstr\b"),
    ("finish", r"\bfinish(ed)?\b|\bdouble matured\b|\bacd\b|\bmatured in .* then\b"),
    ("butt", r"\bbutts?\b"),
]
_CASK_RES = [(name, re.compile(rx, re.I)) for name, rx in CASK_PATTERNS]

HEAVY_SHERRY_MARKERS = re.compile(
    r"sherry bomb|first[\s-]*fill\s+(oloroso|sherry|px)\s*(butt)?|\bpx\b.*(matured|cask)|"
    r"(fully|full|entirely|exclusively)\s+(matured\s+in\s+)?(oloroso|sherry|px)|100%\s*sherry|"
    r"pedro xim[eé]nez", re.I)

WHISKY_WORDS = re.compile(r"\b(whisky|whiskey|scotch|single malt|malt|bourbon|rye|blended malt|single cask|single grain)\b", re.I)
NOT_WHISKY = re.compile(r"\b(gin|vodka|tequila|mezcal|cognac|brandy|liqueur|wine glass|glencairn|gift box only|beer|ale|lager|cider|sake)\b", re.I)


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s.lower().replace("’", "'")).strip()


@dataclass
class Parsed:
    age: int | None = None
    vintage: int | None = None
    bottled: int | None = None
    abv: float | None = None
    volume_ml: int | None = None
    casks: list[str] = field(default_factory=list)
    sherry_level: str = "none"  # none | light | heavy
    peat: str = "unknown"  # heavy | medium | light | none | unknown
    distillery: str | None = None
    region: str | None = None
    profile: list[str] = field(default_factory=list)
    bottlers: list[str] = field(default_factory=list)
    single_cask: bool = False
    cask_strength: bool = False
    is_whisky: bool = False
    american: bool = False  # bourbon/rye *whiskey*, not a scotch matured in bourbon casks

    def to_dict(self) -> dict:
        return asdict(self)


_AGE_RE = re.compile(
    r"\b(\d{1,2})\s*(?:-|\s)?(?:years?[\s-]*old|year|yrs?|yo|y\.o\.?|y/o|ans)\b|\baged\s+(\d{1,2})\b", re.I)
_MATURED_RE = re.compile(r"\b(?:matured|aged|spent)\s+(?:for\s+)?(\d{1,2})\s+years\b()", re.I)
_YEAR_RANGE_RE = re.compile(r"\b(19[5-9]\d|20[0-3]\d)\s*[-/–]\s*(19[5-9]\d|20[0-3]\d)\b")
_VINTAGE_RE = re.compile(r"\b(?:distilled|vintage|dist\.?)\s*(?:in\s*)?(19[5-9]\d|20[0-3]\d)\b|\b(19[5-9]\d|20[0-2]\d)\s+vintage\b", re.I)
_BOTTLED_RE = re.compile(r"\b(?:bottled|btl\.?)\s*(?:in\s*)?(19[5-9]\d|20[0-3]\d)\b", re.I)
_ABV_RE = re.compile(r"(\d{2}(?:[.,]\d{1,2})?)\s*%(?:\s*(?:abv|alc|vol))?", re.I)
_PROOF_RE = re.compile(r"(\d{2,3}(?:\.\d)?)\s*proof", re.I)
_ML_RE = re.compile(r"\b(\d{2,4})\s*ml\b", re.I)
_L_RE = re.compile(r"\b(\d(?:\.\d{1,2})?)\s*(?:l|litre|liter)\b", re.I)
_CL_RE = re.compile(r"\b(\d{2,3})\s*cl\b", re.I)


def _find_bottlers(text: str) -> list[str]:
    found = []
    for canon, aliases in BOTTLERS.items():
        for a in aliases:
            if a in _SHORT_ALIASES:
                hit = re.search(rf"(?<![\w&]){re.escape(a)}(?![\w&])", text)
                if a == "sponge" and hit and "whisky" not in text:
                    hit = None
                if a == "decadent" and hit and not re.search(r"decadent\s+(drinks|dreams|drams)", text):
                    # A bare "decadent" is usually tasting-note prose.
                    hit = None
            else:
                hit = a in text
            if hit:
                found.append(canon)
                break
    return found


def _find_distillery(text: str) -> str | None:
    # Longest name first so "port charlotte" wins over a stray "port", "glen elgin" over "elgin".
    best = None
    best_pos = None
    for name in sorted(DISTILLERIES, key=len, reverse=True):
        m = re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", text)
        if m and (best_pos is None or m.start() < best_pos):
            best, best_pos = name, m.start()
    return best


def parse(title: str, extra_text: str = "") -> Parsed:
    """Parse a listing. `title` is trusted more than `extra_text` (descriptions mention other whiskies)."""
    t = normalize(title)
    full = normalize(f"{title} {extra_text}")
    p = Parsed()

    m = _AGE_RE.search(t) or _AGE_RE.search(full) or _MATURED_RE.search(full)
    if m:
        age = int(m.group(1) or m.group(2))
        if 3 <= age <= 70:
            p.age = age

    yr = _YEAR_RANGE_RE.search(t)
    if yr:
        a, b = int(yr.group(1)), int(yr.group(2))
        if b > a:
            p.vintage, p.bottled = a, b
    if p.vintage is None:
        vm = _VINTAGE_RE.search(full)
        if vm:
            p.vintage = int(vm.group(1) or vm.group(2))
    if p.bottled is None:
        bm = _BOTTLED_RE.search(full)
        if bm:
            p.bottled = int(bm.group(1))
    if p.age is None and p.vintage and p.bottled and p.bottled > p.vintage:
        p.age = p.bottled - p.vintage

    for src in (t, full):
        for am in _ABV_RE.finditer(src):
            v = float(am.group(1).replace(",", "."))
            if 37.0 <= v <= 75.0:
                p.abv = v
                break
        if p.abv:
            break
    if p.abv is None:
        pm = _PROOF_RE.search(full)
        if pm and 74 <= float(pm.group(1)) <= 150:
            p.abv = round(float(pm.group(1)) / 2, 1)

    if mm := _ML_RE.search(full):
        p.volume_ml = int(mm.group(1))
    elif cm := _CL_RE.search(full):
        p.volume_ml = int(cm.group(1)) * 10
    elif lm := _L_RE.search(t):
        v = float(lm.group(1))
        if 0.05 <= v <= 4.5:
            p.volume_ml = int(round(v * 1000))

    p.casks = [name for name, rx in _CASK_RES if rx.search(full)]
    sherry_casks = {"first_fill_sherry", "refill_sherry", "sherry_hogshead", "sherry_butt", "px", "oloroso", "fino", "sherry"}
    if sherry_casks & set(p.casks):
        if HEAVY_SHERRY_MARKERS.search(full) or {"first_fill_sherry", "px"} & set(p.casks):
            p.sherry_level = "heavy"
        elif {"refill_sherry", "sherry_hogshead", "fino"} & set(p.casks) or "finish" in p.casks:
            p.sherry_level = "light"
        elif "sherry_butt" in p.casks or "oloroso" in p.casks:
            p.sherry_level = "heavy"
        else:
            p.sherry_level = "light"

    p.distillery = _find_distillery(t) or _find_distillery(full)
    p.bottlers = _find_bottlers(full)
    if p.distillery:
        info = DISTILLERIES[p.distillery]
        p.region = info["region"]
        p.profile = list(info["profile"])
        p.peat = info["peat"]
        if p.age is None:
            # "Macallan 18", "Glendronach 21 Parliament"
            m = re.search(rf"{re.escape(p.distillery)}\s+(\d{{1,2}})\b(?!\s*(%|ml|cl|\.\d))", t)
            if m and 3 <= int(m.group(1)) <= 60:
                p.age = int(m.group(1))
        # Official bottlings follow house style; independent casks are usually refill, so don't assume.
        if "sherry_house" in p.profile and not p.bottlers:
            bourbon = {"bourbon", "first_fill_bourbon", "refill_bourbon"} & set(p.casks)
            if p.sherry_level == "none" and not bourbon:
                p.sherry_level = "heavy"
            elif p.sherry_level == "light" and set(p.casks) <= {"sherry", "oloroso", "butt"}:
                p.sherry_level = "heavy"  # e.g. "Macallan 18 Sherry Oak"

    if any(mk in full for mk in UNPEATED_MARKERS):
        p.peat = "none"
    elif re.search(r"heavily peated|\b[3-9]\d\s*ppm|\b1\d\d\s*ppm|peat monster|octomore", full):
        p.peat = "heavy"
    elif any(re.search(rf"(?<![\w-]){re.escape(x)}(?![\w])", full) for x in PEATED_EXPRESSIONS):
        if p.peat in ("unknown", "none", "light"):
            p.peat = "medium"

    p.single_cask = bool(re.search(r"single cask|cask\s*(no\.?|number|#)\s*\d|\bcask\s+\d{2,}", full))
    p.cask_strength = bool(re.search(r"cask strength|barrel proof|natural strength|full proof", full)) or (p.abv is not None and p.abv >= 52)

    p.american = bool(
        re.search(r"kentucky|tennessee|straight (bourbon|rye)|bourbon whiske?y|\brye whiske?y", full)
        and not p.region
    )
    p.is_whisky = bool(
        (WHISKY_WORDS.search(full) or p.distillery or p.bottlers) and not NOT_WHISKY.search(t)
    )
    return p
