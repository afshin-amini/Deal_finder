"""CLI.

  python -m dealfinder run [--dry-run] [--shop kwm ...]   daily scrape + alerts
  python -m dealfinder probe [--shop bsw]                  which platform does each shop use?
  python -m dealfinder top [--track peat_earth|fruit_earth] [-n 20]
  python -m dealfinder history "ardmore"                   price history for matching bottles
  python -m dealfinder score "Ardmore 12 Year Old Port Wood Finish 46%"   test the scorer
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from . import adapters, config
from .db import DB
from .http import PoliteSession
from .parser import parse
from .scoring import palate_score


def cmd_probe(cfg: dict, only: list[str] | None) -> None:
    h = cfg["http"]
    http = PoliteSession(user_agent=h.get("user_agent") or PoliteSession().user_agent,
                         min_delay=float(h.get("min_delay_seconds", 4.0)))
    for shop in cfg["shops"]:
        if only and shop["name"] not in only:
            continue
        candidates = shop["base_url"] if isinstance(shop["base_url"], list) else [shop["base_url"]]
        print(f"\n== {shop['name']}")
        for base in candidates:
            base = base.rstrip("/")
            try:
                r = http.session.get(base + "/robots.txt", timeout=20)
                print(f"  {base}/robots.txt -> {r.status_code}")
                for line in r.text.splitlines():
                    if line.lower().startswith(("disallow", "crawl-delay", "sitemap", "user-agent")):
                        print("     ", line.strip())
            except Exception as exc:  # noqa: BLE001
                print(f"  {base}: unreachable ({exc.__class__.__name__}: {exc})")
                continue
        adapter, base = adapters.resolve(http, {**shop, "platform": "auto"})
        print(f"  -> platform: {adapter.name} at {base}")
        sample = 0
        for listing in adapter.fetch(http, {**shop, "base_url": base, "max_products": 5}):
            p = parse(listing.title, listing.description)
            print(f"     {'W' if p.is_whisky else '-'} ${listing.price} {listing.title[:70]}"
                  f"  (desc {len(listing.description)} chars)")
            sample += 1
            if sample >= 5:
                break
        if sample == 0:
            print("     no products found -- set listing_urls / product_url_pattern for this shop in config.toml")


def cmd_top(cfg: dict, track: str | None, n: int) -> None:
    db = DB(cfg["db_path"])
    for r in db.top(n, track):
        s = json.loads(r["score_json"] or "{}")
        print(f"{s.get('palate', 0):>3}  A{s.get('peat_earth', 0):>3} B{s.get('fruit_earth', 0):>3}  "
              f"${r['last_price'] or 0:>8.2f}  {r['shop']:<6} {r['title'][:80]}")


def cmd_history(cfg: dict, query: str) -> None:
    db = DB(cfg["db_path"])
    rows = db.conn.execute("SELECT key, shop, title FROM listings WHERE title LIKE ? ORDER BY title", (f"%{query}%",)).fetchall()
    for r in rows:
        print(f"\n{r['title']} [{r['shop']}]")
        for p in db.conn.execute(
            "SELECT date(observed_at) d, price, in_stock FROM prices WHERE key=? GROUP BY d ORDER BY d", (r["key"],)
        ):
            stock = {1: "in", 0: "OUT"}.get(p["in_stock"], "?")
            print(f"   {p['d']}  ${p['price'] or 0:.2f}  {stock}")


def cmd_score(cfg: dict, text: str, notes: str) -> None:
    p = parse(text, notes)
    s = palate_score(p, f"{text} {notes}", cfg["watchlist"], has_notes=bool(notes))
    print(json.dumps({"parsed": p.to_dict(), "score": s.to_dict()}, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="dealfinder")
    ap.add_argument("-c", "--config", default="config.toml")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--dry-run", action="store_true", help="print alerts instead of sending them")
    r.add_argument("--shop", action="append")
    pr = sub.add_parser("probe")
    pr.add_argument("--shop", action="append")
    t = sub.add_parser("top")
    t.add_argument("--track", choices=["peat_earth", "fruit_earth"])
    t.add_argument("-n", type=int, default=25)
    hi = sub.add_parser("history")
    hi.add_argument("query")
    sc = sub.add_parser("score")
    sc.add_argument("title")
    sc.add_argument("--notes", default="")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = config.load(args.config)

    if args.cmd == "run":
        from .runner import run
        return run(cfg, args.shop, args.dry_run)
    if args.cmd == "probe":
        cmd_probe(cfg, args.shop)
    elif args.cmd == "top":
        cmd_top(cfg, args.track, args.n)
    elif args.cmd == "history":
        cmd_history(cfg, args.query)
    elif args.cmd == "score":
        cmd_score(cfg, args.title, args.notes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
