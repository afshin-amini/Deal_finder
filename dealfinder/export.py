"""Publish the deal finder's data as static JSON for the app (served by GitHub Pages).

  catalog.json  every whisky currently in stock, with scores (compact keys)
  deals.json    alerts from the last 30 days
  history.json  price change points, for bottles whose price has moved
  notes.json    KWM tasting notes by bottle
  meta.json     run time, shop counts, taste-profile summary

Your journal (journal.json) lives in the same folder but is written by the app,
never by this module.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .db import DB

log = logging.getLogger(__name__)

KEEP_ALERT_DAYS = 30
FRESH_DAYS = 2  # a listing not seen within this many days of its shop's last run is stale


def _write(path: Path, data) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _cut(iso: str, days: int) -> str:
    return (datetime.fromisoformat(iso) - timedelta(days=days)).isoformat()


def catalog_rows(db: DB) -> list[dict]:
    latest = {r["shop"]: r["m"] for r in db.conn.execute("SELECT shop, MAX(last_seen) m FROM listings GROUP BY shop")}
    out = []
    for r in db.conn.execute("SELECT * FROM listings WHERE last_in_stock IS NOT 0 AND last_price IS NOT NULL"):
        if r["last_seen"] < _cut(latest[r["shop"]], FRESH_DAYS):
            continue
        p = json.loads(r["parsed_json"] or "{}")
        s = json.loads(r["score_json"] or "{}")
        if not s:
            continue
        deal = s.get("deal") or {}
        personal = s.get("personal") or {}
        row = {
            "k": r["key"], "t": r["title"], "s": r["shop"], "p": r["last_price"], "u": r["url"],
            "pal": s.get("palate", 0), "pe": s.get("peat_earth", 0), "fe": s.get("fruit_earth", 0),
            "tr": s.get("track"), "w": s.get("watchlist") or [],
            "a": p.get("age"), "v": p.get("abv"), "ml": p.get("volume_ml"), "d": p.get("distillery"),
            "b": p.get("bottlers") or [], "c": p.get("casks") or [], "pk": p.get("peat"), "sh": p.get("sherry_level"),
            "sc": p.get("single_cask") or None, "cs": p.get("cask_strength") or None,
            "fs": r["first_seen"][:10],
        }
        if deal.get("score"):
            row["ds"] = deal["score"]
            row["dr"] = deal.get("reasons") or []
        if personal:
            row["me"] = personal.get("predicted")
            row["mr"] = personal.get("reasons") or []
            if personal.get("similar"):
                row["sim"] = personal["similar"]
        why = (s.get("reasons") or {}).get(s.get("track") or "", [])
        if why:
            row["why"] = why[:4]
        out.append({k: v for k, v in row.items() if v not in (None, [], "")})
    return out


def deals_rows(db: DB, now_iso: str) -> list[dict]:
    rows = db.conn.execute(
        """SELECT a.kind, a.sent_at, a.price, l.key, l.title, l.shop, l.url
           FROM alerts a JOIN listings l ON l.key = a.key
           WHERE a.sent_at >= ? ORDER BY a.sent_at DESC""",
        (_cut(now_iso, KEEP_ALERT_DAYS),),
    ).fetchall()
    return [{"kind": r["kind"], "at": r["sent_at"][:10], "p": r["price"], "k": r["key"], "t": r["title"],
             "s": r["shop"], "u": r["url"]} for r in rows]


def history_rows(db: DB, keys: set[str]) -> dict[str, list]:
    hist: dict[str, list] = {}
    for r in db.conn.execute("SELECT key, substr(observed_at,1,10) d, price FROM prices WHERE price IS NOT NULL ORDER BY key, observed_at"):
        if r["key"] not in keys:
            continue
        pts = hist.setdefault(r["key"], [])
        if not pts or pts[-1][1] != r["price"]:
            pts.append([r["d"], r["price"]])
        else:
            pts[-1][0] = r["d"]  # extend the flat run to the latest date
    return {k: v for k, v in hist.items() if len(v) > 1}


def export_app_data(db: DB, out_dir: str, profile=None) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    catalog = catalog_rows(db)
    keys = {c["k"] for c in catalog}
    notes = {r["key"]: r["notes"] for r in db.conn.execute("SELECT key, notes FROM notes WHERE notes != ''")
             if r["key"] in keys}
    shops: dict[str, int] = {}
    for c in catalog:
        shops[c["s"]] = shops.get(c["s"], 0) + 1
    _write(out / "catalog.json", catalog)
    _write(out / "deals.json", deals_rows(db, now_iso))
    _write(out / "history.json", history_rows(db, keys))
    _write(out / "notes.json", notes)
    _write(out / "meta.json", {"generated": now_iso, "shops": shops, "notes": len(notes),
                               "profile": profile.summary() if profile is not None else None})
    journal = out / "journal.json"
    if not journal.exists():
        _write(journal, {"version": 1, "entries": []})
    log.info("exported app data: %d bottles in stock, %d with notes -> %s", len(catalog), len(notes), out)
