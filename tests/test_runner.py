from dealfinder import adapters, runner
from dealfinder.db import DB
from dealfinder.http import PoliteSession
from dealfinder.models import Listing


class FakeAdapter:
    name = "fake"

    def __init__(self, batches):
        self.batches = batches
        self.calls = 0

    def fetch(self, http, shop):
        batch = self.batches[min(self.calls, len(self.batches) - 1)]
        self.calls += 1
        yield from batch


def L(pid, title, price, in_stock=True):
    return Listing(shop="crown", product_id=pid, title=title, url=f"https://x/{pid}", price=price, in_stock=in_stock)


def make_cfg(tmp_path):
    return {
        "db_path": str(tmp_path / "t.db"),
        "http": {"min_delay_seconds": 0},
        "ntfy": {"topic": None, "max_individual_alerts": 8},
        "alerts": {},
        "value": {},
        "watchlist": {"Ardmore": []},
        "shops": [{"name": "crown", "base_url": "https://x"}],
    }


def test_seed_then_alerts(tmp_path, monkeypatch, capsys):
    day1 = [L("1", "Ardmore 12 Year Old Peated 46%", 90.0), L("2", "Hendrick's Gin", 40.0),
            L("3", "Lagavulin 16 Year Old", 130.0, in_stock=False)]
    day2 = [L("1", "Ardmore 12 Year Old Peated 46%", 72.0), L("3", "Lagavulin 16 Year Old", 130.0),
            L("4", "Ledaig 2008 Refill Sherry Butt 14 Year Old", 120.0)]
    fake = FakeAdapter([day1, day2])
    monkeypatch.setattr(adapters, "resolve", lambda http, shop: (fake, "https://x"))
    cfg = make_cfg(tmp_path)

    # Day 1 seeds: no deal alerts, just the baseline summary.
    runner.run(cfg, dry_run=False)
    out = capsys.readouterr().out
    assert "baseline recorded" in out and "Price drop" not in out
    db = DB(cfg["db_path"])
    assert db.get("crown:2") is None  # gin not stored
    assert db.get("crown:1")["last_price"] == 90.0

    # Day 2: price drop, restock, new peated bottle.
    runner.run(cfg, dry_run=False)
    out = capsys.readouterr().out
    assert "Price drop: Ardmore 12" in out
    assert "Back in stock: Lagavulin 16" in out
    assert "New: Ledaig 2008" in out
    assert db.price_history("crown:1") == [90.0, 72.0]

    # Day 3 with identical data: alerts are not repeated.
    runner.run(cfg, dry_run=False)
    out = capsys.readouterr().out
    assert "Price drop" not in out and "New:" not in out


def test_robots_disallow(monkeypatch):
    http = PoliteSession(min_delay=0)

    class R:
        status_code = 200
        text = "User-agent: *\nDisallow: /cart\n"

    monkeypatch.setattr(http.session, "get", lambda *a, **k: R())
    assert http.allowed("https://shop.test/products/x")
    assert not http.allowed("https://shop.test/cart")
