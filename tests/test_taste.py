import json

from dealfinder.parser import parse
from dealfinder.taste import MIN_ENTRIES, blend, flavour_words, load_profile


def _journal(tmp_path, entries):
    p = tmp_path / "journal.json"
    p.write_text(json.dumps({"version": 1, "entries": entries}))
    return load_profile(p)


ENTRIES = [
    {"name": "Tri Carragh Ardmore 2009 12 Year 55%", "score": 92, "nose": "green smoke, leather, pear"},
    {"name": "SMOS Ardmore 13 Year 48%", "score": 90, "palate": "earthy peat, toffee, apple"},
    {"name": "G&M Ledaig 2007 Refill Sherry 55.1%", "score": 89, "palate": "smoke, leather, red fruit"},
    {"name": "Kilkerran 12", "score": 88, "nose": "waxy, earthy, smoke"},
    {"name": "Clynelish 14", "score": 84, "nose": "wax, honey, citrus"},
    {"name": "Glendronach 15 PX and Oloroso", "score": 74, "palate": "raisin, prune, dark chocolate"},
    {"name": "Macallan 12 Sherry Oak", "score": 72, "palate": "raisin, dried fruit, sherry"},
    {"name": "Aberlour A'bunadh oloroso 60%", "score": 76, "palate": "sherry, raisin, cinnamon"},
    {"name": "Glenlivet 12 40%", "score": 75, "palate": "apple, vanilla"},
]


def test_flavour_words():
    assert flavour_words("Smoky bacon, leathery and EARTHY with cherries") == {"smoke", "bacon", "leather", "earth", "cherry"}


def test_profile_learns_likes_and_dislikes(tmp_path):
    prof = _journal(tmp_path, ENTRIES)
    assert prof.active and len(prof.entries) == len(ENTRIES) >= MIN_ENTRIES
    ardmore = prof.predict(parse("Whisky Agency Ardmore 2005 51.3%"), "smoke, leather, earthy")
    px = prof.predict(parse("Glendronach 18 Allardice oloroso"), "raisin, prune, sherry")
    assert ardmore.predicted > prof.mean > px.predicted
    assert any("Ardmore" in r for r in ardmore.reasons)
    assert ardmore.similar and "Ardmore" in ardmore.similar["name"]
    s = prof.summary()
    assert any(x["f"] == "Ardmore" for x in s["likes"]) and any("heavy" in x["f"] for x in s["dislikes"])


def test_profile_inactive_below_minimum(tmp_path):
    assert not _journal(tmp_path, ENTRIES[:3]).active


def test_blend_trusts_profile_more_as_it_grows(tmp_path):
    prof = _journal(tmp_path, ENTRIES)
    pred = prof.predict(parse("Ardmore 2009"), "smoke")
    assert min(40, pred.percentile) <= blend(40, pred, prof) <= max(40, pred.percentile)


def test_missing_or_bad_journal(tmp_path):
    assert load_profile(tmp_path / "nope.json") is None
    assert _journal(tmp_path, [{"name": "", "score": 90}, {"name": "x", "score": "bad"}]).entries == []


def test_abv_field_used_when_name_has_none(tmp_path):
    prof = _journal(tmp_path, ENTRIES + [{"name": "Mystery Speyside", "abv": 58.2, "score": 88}])
    assert any(f == "abv:55+" for f in prof.entries[-1].feats)
