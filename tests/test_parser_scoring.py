from dealfinder.parser import parse
from dealfinder.scoring import deal_score, palate_score

WATCH = {
    "Ardmore": [],
    "Gordon & MacPhail": ["G&M"],
    "Decadent Drinks": ["Decadent Dreams"],
    "Whisky Sponge": [],
    "Single Malts of Scotland": ["SMOS"],
    "Thompson Bros": [],
    "Berry Bros & Rudd": ["Berry Bros", "BBR"],
}


def score(title, notes=""):
    p = parse(title, notes)
    return p, palate_score(p, f"{title} {notes}", WATCH, has_notes=bool(notes))


def test_age_abv_volume():
    p = parse("Ardmore 12 Year Old Port Wood Finish 46% 700ml")
    assert (p.age, p.abv, p.volume_ml) == (12, 46.0, 700)
    assert "port" in p.casks and "finish" in p.casks


def test_age_from_vintage_range_and_bare_number():
    assert parse("Benrinnes 1995-2022 Whisky Sponge").age == 27
    assert parse("Macallan 18 Sherry Oak").age == 18
    assert parse("Glen Moray 2010 Cask 123 750ml").age is None  # no bottling year


def test_proof_and_cl():
    p = parse("Something Single Malt 114 proof 70cl")
    assert p.abv == 57.0 and p.volume_ml == 700


def test_sherry_levels():
    assert parse("Ben Nevis 2013 Refill Sherry Hogshead").sherry_level == "light"
    assert parse("Glendronach 15 PX and Oloroso").sherry_level == "heavy"
    assert parse("Macallan 18 Sherry Oak").sherry_level == "heavy"
    # IB of a sherry-house distillery, no cask stated: don't assume sherry
    assert parse("Decadent Drinks Mortlach 1998").sherry_level == "none"


def test_peat_overrides():
    assert parse("Bunnahabhain Moine 10").peat in ("medium", "heavy")
    assert parse("Caol Ila 2010 Unpeated").peat == "none"
    assert parse("Ardlair 6 Year Old").peat == "none"


def test_bottler_aliases():
    assert "Gordon & MacPhail" in parse("G&M Connoisseurs Choice Ardmore").bottlers
    assert "Single Malts of Scotland" in parse("SMOS Clynelish 12").bottlers
    assert "Decadent Drinks" not in parse("Glenfarclas 15", "a decadent, rich dram").bottlers
    assert "Decadent Drinks" in parse("Decadent Dreams Linkwood").bottlers


def test_not_whisky():
    assert not parse("Hendrick's Gin 750ml").is_whisky
    p, s = score("Buffalo Trace Kentucky Straight Bourbon Whiskey")
    assert p.american and s.palate == 0


def test_peat_track():
    _, s = score("Gordon & MacPhail Ardmore 2009 Refill Sherry Hogshead 57.3%")
    assert s.peat_earth >= 75 and s.fruit_earth == 0
    assert set(s.watchlist) == {"Ardmore", "Gordon & MacPhail"}


def test_fruit_track_bourbon():
    _, s = score("SMOS Clynelish 12 Year Old 1st Fill Bourbon Barrel 56.2%")
    assert s.fruit_earth >= 70 and s.peat_earth == 0


def test_fruit_track_prefers_light_over_heavy_sherry():
    _, light = score("Craigellachie 2012 Refill Sherry Hogshead")
    _, heavy = score("Glendronach 15 PX and Oloroso")
    assert light.fruit_earth > heavy.fruit_earth + 20


def test_tasting_notes_count_more_with_notes():
    _, without = score("Craigellachie 13 Year Old")
    _, with_notes = score("Craigellachie 13 Year Old", "Nose: apple, peach, honey. Palate: earthy, waxy.")
    assert with_notes.fruit_earth > without.fruit_earth


def test_deal_score_drop_and_median():
    p = parse("Ardmore 12 Year Old 46%")
    d = deal_score(80.0, [100, 100, 100, 95], 100.0, None, p, {})
    assert d.drop_pct == 20.0 and d.vs_median_pct == 20.0 and d.score > 40


def test_deal_score_no_history():
    d = deal_score(100.0, [], None, None, parse("NAS whisky"), {})
    assert d.score == 0 and d.reasons == []


def test_matured_years_age():
    assert parse("Benromach 2005 KWM Cask 335", "matured 18 years in a First Fill Sherry Hogshead").age == 18
