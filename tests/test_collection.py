import json

from dealfinder import adapters, runner
from dealfinder.collection import load_collection, name_matches
from dealfinder.db import DB
from dealfinder.models import Listing


def test_name_matching():
    assert name_matches("Springbank 15", "Springbank 15 Year Old - 46% abv / 700mL")
    assert not name_matches("Springbank 15", "Springbank 10 Year Old")
    assert not name_matches("Springbank", "Springbank 15")  # one word is too loose


def _doc(tmp_path, **kw):
    p = tmp_path / "journal.json"
    p.write_text(json.dumps({"version": 1, "entries": [], **kw}))
    return p


def test_load_skips_suggested_and_finished(tmp_path):
    c = load_collection(_doc(tmp_path, watch=[{"id": "a", "name": "Springbank 15", "target": 140},
                                              {"id": "b", "name": "Kilkerran 12", "suggested": True}],
                             shelf=[{"id": "s", "name": "Ledaig 10", "key": "craft:9"},
                                    {"id": "t", "name": "Old bottle", "key": "craft:8", "level": "finished"}]))
    assert [w.id for w in c.watch] == ["a"] and c.owned_keys == {"craft:9"}


class Fake:
    name = "fake"

    def __init__(self, days):
        self.days, self.n = days, 0

    def fetch(self, http, shop):
        day = self.days[min(self.n, len(self.days) - 1)]
        self.n += 1
        yield from day


def test_watch_alert_and_owned_suppression(tmp_path, monkeypatch, capsys):
    journal = _doc(tmp_path, watch=[{"id": "w", "name": "Springbank 15", "target": 140}],
                   shelf=[{"id": "s", "name": "Ledaig 2008 Refill Sherry", "key": "crown:3"}])
    L = lambda pid, t, p: Listing(shop="crown", product_id=pid, title=t, url="u" + pid, price=p, in_stock=True)
    day1 = [L("1", "Springbank 15 Year Old 46%", 150.0), L("3", "Ledaig 2008 Refill Sherry Butt 14 Year Old", 120.0)]
    day2 = [L("1", "Springbank 15 Year Old 46%", 135.0), L("3", "Ledaig 2008 Refill Sherry Butt 14 Year Old", 100.0),
            L("4", "Ledaig 2009 Refill Sherry 13 Year Old 55%", 110.0)]
    fake = Fake([day1, day2])
    monkeypatch.setattr(adapters, "resolve", lambda http, shop: (fake, "https://x"))
    cfg = {"db_path": str(tmp_path / "t.db"), "journal_path": str(journal), "http": {"min_delay_seconds": 0},
           "ntfy": {"topic": None}, "alerts": {}, "value": {}, "watchlist": {}, "shops": [{"name": "crown", "base_url": "x"}]}
    runner.run(cfg)
    capsys.readouterr()
    runner.run(cfg)
    out = capsys.readouterr().out
    assert "Watchlist: Springbank 15" in out and "at or under your target $140" in out
    assert "Ledaig 2008" not in out          # you own it: no drop alert
    db = DB(cfg["db_path"])
    assert json.loads(db.get("crown:3")["score_json"]).get("owned") is True
