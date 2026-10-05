from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Listing

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    key            TEXT PRIMARY KEY,
    shop           TEXT NOT NULL,
    product_id     TEXT NOT NULL,
    title          TEXT NOT NULL,
    url            TEXT,
    vendor         TEXT,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    last_price     REAL,
    last_in_stock  INTEGER,
    parsed_json    TEXT,
    score_json     TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    key          TEXT NOT NULL REFERENCES listings(key),
    observed_at  TEXT NOT NULL,
    price        REAL,
    in_stock     INTEGER,
    compare_at   REAL
);
CREATE INDEX IF NOT EXISTS prices_key ON prices(key, observed_at);
CREATE TABLE IF NOT EXISTS alerts (
    key      TEXT NOT NULL,
    kind     TEXT NOT NULL,
    price    REAL,
    sent_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_key ON alerts(key, kind, sent_at);
CREATE TABLE IF NOT EXISTS notes (
    key         TEXT PRIMARY KEY,
    notes       TEXT NOT NULL,
    fetched_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    shop         TEXT NOT NULL,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    platform     TEXT,
    base_url     TEXT,
    n_listings   INTEGER,
    error        TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- runs -------------------------------------------------------------

    def start_run(self, shop: str) -> int:
        cur = self.conn.execute("INSERT INTO runs(shop, started_at) VALUES (?, ?)", (shop, now()))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, platform: str | None, base_url: str | None, n: int, error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE runs SET finished_at=?, platform=?, base_url=?, n_listings=?, error=? WHERE rowid=?",
            (now(), platform, base_url, n, error, run_id),
        )
        self.conn.commit()

    def shop_has_history(self, shop: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM runs WHERE shop=? AND finished_at IS NOT NULL AND error IS NULL AND n_listings > 0 LIMIT 1",
            (shop,),
        ).fetchone() is not None

    # ---- listings / prices -----------------------------------------------

    def get(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM listings WHERE key=?", (key,)).fetchone()

    def price_history(self, key: str, days: int = 120) -> list[float]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT price FROM prices WHERE key=? AND observed_at>=? AND price IS NOT NULL", (key, since)
        ).fetchall()
        return [r["price"] for r in rows]

    def upsert(self, l: Listing, parsed: dict, score: dict) -> None:
        ts = now()
        self.conn.execute(
            """INSERT INTO listings(key, shop, product_id, title, url, vendor, first_seen, last_seen,
                                    last_price, last_in_stock, parsed_json, score_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET
                 title=excluded.title, url=excluded.url, vendor=excluded.vendor,
                 last_seen=excluded.last_seen, last_price=excluded.last_price,
                 last_in_stock=excluded.last_in_stock, parsed_json=excluded.parsed_json,
                 score_json=excluded.score_json""",
            (l.key, l.shop, l.product_id, l.title, l.url, l.vendor, ts, ts, l.price,
             None if l.in_stock is None else int(l.in_stock), json.dumps(parsed), json.dumps(score)),
        )
        self.conn.execute(
            "INSERT INTO prices(key, observed_at, price, in_stock, compare_at) VALUES (?,?,?,?,?)",
            (l.key, ts, l.price, None if l.in_stock is None else int(l.in_stock), l.compare_at_price),
        )

    def commit(self) -> None:
        self.conn.commit()

    def mark_missing_out_of_stock(self, shop: str, run_started: str) -> int:
        """Listings not seen in a complete run of this shop are gone from its catalog."""
        cur = self.conn.execute(
            "UPDATE listings SET last_in_stock=0 WHERE shop=? AND last_seen<? AND last_in_stock IS NOT 0",
            (shop, run_started),
        )
        self.conn.commit()
        return cur.rowcount

    # ---- tasting notes ----------------------------------------------------

    def get_notes(self, key: str) -> str | None:
        row = self.conn.execute("SELECT notes FROM notes WHERE key=?", (key,)).fetchone()
        return row["notes"] if row else None

    def set_notes(self, key: str, notes: str) -> None:
        # Store "" too, so pages without notes aren't fetched again.
        self.conn.execute("INSERT OR REPLACE INTO notes(key, notes, fetched_at) VALUES (?,?,?)", (key, notes, now()))
        self.conn.commit()

    def missing_notes(self, shop: str, limit: int) -> list[sqlite3.Row]:
        """Best-scoring in-stock bottles first, so the backfill reaches the interesting ones early."""
        return self.conn.execute(
            """SELECT l.key, l.url FROM listings l LEFT JOIN notes n ON n.key=l.key
               WHERE l.shop=? AND n.key IS NULL AND l.last_in_stock IS NOT 0
               ORDER BY json_extract(l.score_json, '$.palate') DESC LIMIT ?""",
            (shop, limit),
        ).fetchall()

    # ---- alerts -----------------------------------------------------------

    def recently_alerted(self, key: str, kind: str, price: float | None, days: int) -> bool:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        row = self.conn.execute(
            "SELECT price FROM alerts WHERE key=? AND kind=? AND sent_at>=? ORDER BY sent_at DESC LIMIT 1",
            (key, kind, since),
        ).fetchone()
        if row is None:
            return False
        # Re-alert inside the window only if the price fell further.
        return not (price is not None and row["price"] is not None and price < row["price"] - 0.01)

    def record_alert(self, key: str, kind: str, price: float | None) -> None:
        self.conn.execute("INSERT INTO alerts(key, kind, price, sent_at) VALUES (?,?,?,?)", (key, kind, price, now()))
        self.conn.commit()

    # ---- reporting --------------------------------------------------------

    def top(self, limit: int = 20, track: str | None = None, in_stock_only: bool = True) -> list[sqlite3.Row]:
        order = {"peat_earth": "$.peat_earth", "fruit_earth": "$.fruit_earth"}.get(track or "", "$.palate")
        where = "WHERE last_in_stock IS NOT 0" if in_stock_only else ""
        return self.conn.execute(
            f"""SELECT * FROM listings {where}
                ORDER BY json_extract(score_json, '{order}') DESC, last_price ASC LIMIT ?""",
            (limit,),
        ).fetchall()
