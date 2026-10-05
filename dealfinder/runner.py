"""The daily run: scrape every shop, record prices, score, decide and send alerts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import adapters
from .db import DB, now
from .http import BotChallenge, PoliteSession
from .models import Listing
from .notify import Ntfy
from .parser import parse
from .scoring import deal_score, palate_score

log = logging.getLogger(__name__)

TRACK_LABEL = {"peat_earth": "peaty/earthy", "fruit_earth": "fruit/red-fruit"}


@dataclass
class Alert:
    kind: str  # new | drop | restock | deal
    listing: Listing
    palate: dict
    deal: dict
    priority: float
    notes: str = ""


def evaluate(listing: Listing, shop_cfg: dict, cfg: dict, db: DB, seeded: bool,
             notes: str = "") -> tuple[dict, dict, dict, list[Alert]]:
    if not shop_cfg.get("trust_compare_at", True):
        listing.compare_at_price = None  # shop shows a permanent "was" price; history is the real signal
    parsed = parse(listing.title, f"{listing.vendor} {listing.product_type} {' '.join(listing.tags)} {listing.description} {notes}")
    if not parsed.is_whisky:
        return parsed.to_dict(), {}, {}, []
    text = f"{listing.text_blob()} {notes}"
    score = palate_score(parsed, text, cfg["watchlist"], has_notes=bool(notes) or shop_cfg.get("has_tasting_notes", False))
    prev = db.get(listing.key)
    history = db.price_history(listing.key)
    deal = deal_score(listing.price, history, prev["last_price"] if prev else None,
                      listing.compare_at_price, parsed, cfg["value"])

    a = cfg["alerts"]
    alerts: list[Alert] = []
    palate = score.palate
    watched = bool(score.watchlist)
    in_stock = listing.in_stock is not False
    is_mini = (parsed.volume_ml is not None and parsed.volume_ml < 300) or bool(
        re.search(r"\b(mini|miniature|sample|gift set|tasting set)\b", listing.title, re.I))

    def add(kind: str, extra: float = 0) -> None:
        pr = palate + deal.score + (10 if watched else 0) + extra
        alerts.append(Alert(kind, listing, score.to_dict(), deal.to_dict(), pr, notes=notes))

    if seeded and in_stock and listing.price and not is_mini:
        if prev is None:
            if palate >= a.get("new_min_palate", 55) or (watched and palate >= a.get("watch_min_palate", 35)):
                add("new", 5)
        else:
            if deal.drop_pct and deal.drop_pct >= a.get("drop_min_pct", 8) and palate >= a.get("drop_min_palate", 40):
                add("drop", 10)
            elif prev["last_in_stock"] == 0 and palate >= a.get("restock_min_palate", 60):
                add("restock")
        if not alerts and deal.score >= a.get("deal_min_score", 20) and palate >= a.get("deal_min_palate", 45):
            add("deal")
    return parsed.to_dict(), score.to_dict(), deal.to_dict(), alerts


def _try_notes(fetch_notes, http: PoliteSession, url: str) -> str:
    try:
        return fetch_notes(http, url)
    except BotChallenge:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("notes fetch failed for %s: %s", url, exc)
        return ""


def format_alert(al: Alert) -> tuple[str, str]:
    l = al.listing
    head = {"new": "New", "drop": "Price drop", "restock": "Back in stock", "deal": "Deal"}[al.kind]
    title = f"{head}: {l.title}"[:150]
    track = al.palate.get("track", "")
    lines = [
        f"${l.price:.2f} at {l.shop}" if l.price is not None else f"at {l.shop}",
        f"Palate {al.palate.get('palate')} ({TRACK_LABEL.get(track, track)})"
        + (f" · watchlist: {', '.join(al.palate['watchlist'])}" if al.palate.get("watchlist") else ""),
    ]
    if al.deal.get("reasons"):
        lines.append("Deal: " + "; ".join(al.deal["reasons"]))
    why = al.palate.get("reasons", {}).get(track, [])
    if why:
        lines.append("Why: " + "; ".join(why[:4]))
    if al.notes:
        lines.append("Notes: " + notes_snippet(al.notes))
    return title, "\n".join(lines)


def notes_snippet(notes: str, limit: int = 350) -> str:
    """Nose + palate is what matters on a phone screen."""
    m = re.search(r"Nose:.*?(?=Finish:|Comment:|$)", notes, re.S)
    s = (m.group(0) if m else notes).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "…"


def run(cfg: dict, only: list[str] | None = None, dry_run: bool = False) -> int:
    h = cfg["http"]
    http = PoliteSession(
        user_agent=h.get("user_agent") or PoliteSession().user_agent,
        min_delay=float(h.get("min_delay_seconds", 4.0)),
        timeout=float(h.get("timeout_seconds", 30)),
    )
    db = DB(cfg["db_path"])
    n = cfg["ntfy"]
    notifier = Ntfy(n.get("topic"), n.get("server", "https://ntfy.sh"), n.get("token"), dry_run=dry_run)

    all_alerts: list[Alert] = []
    seed_notes: list[str] = []
    failures: list[str] = []

    for shop in cfg["shops"]:
        if only and shop["name"] not in only:
            continue
        if not shop.get("enabled", True) and not only:
            continue
        run_id = db.start_run(shop["name"])
        run_started = now()
        seeded = db.shop_has_history(shop["name"])
        adapter, base = None, None
        count = whisky = 0
        try:
            adapter, base = adapters.resolve(http, shop)
            if shop.get("min_delay_seconds"):
                http.set_host_delay(base, float(shop["min_delay_seconds"]))
            shop_cfg = {**shop, "base_url": base}
            fetch_notes = getattr(adapter, "fetch_notes", None)
            new_notes_budget = int(shop.get("notes_new_per_run", 25))
            for listing in adapter.fetch(http, shop_cfg):
                count += 1
                notes = db.get_notes(listing.key) or ""
                if fetch_notes and seeded and new_notes_budget > 0 and db.get(listing.key) is None:
                    # New bottle: grab its tasting notes now so the alert can include them.
                    new_notes_budget -= 1
                    notes = _try_notes(fetch_notes, http, listing.url)
                    db.set_notes(listing.key, notes)
                parsed, score, deal, alerts = evaluate(listing, shop_cfg, cfg, db, seeded, notes)
                if not score:
                    continue  # not whisky; don't store
                whisky += 1
                db.upsert(listing, parsed, {**score, "deal": deal})
                all_alerts.extend(alerts)
                if whisky % 50 == 0:
                    db.commit()
            db.commit()
            if whisky:
                gone = db.mark_missing_out_of_stock(shop["name"], run_started)
                if gone:
                    log.info("%s: %d listings no longer listed -> out of stock", shop["name"], gone)
            if fetch_notes and whisky:
                backfill = db.missing_notes(shop["name"], int(shop.get("notes_backfill_per_run", 30)))
                for row in backfill:
                    db.set_notes(row["key"], _try_notes(fetch_notes, http, row["url"]))
                if backfill:
                    log.info("%s: fetched tasting notes for %d bottles", shop["name"], len(backfill))
            db.finish_run(run_id, adapter.name, base, whisky)
            log.info("%s: %d listings, %d whisky via %s", shop["name"], count, whisky, adapter.name)
            if whisky == 0:
                failures.append(f"{shop['name']}: 0 whiskies found via {adapter.name} at {base}")
            elif not seeded:
                seed_notes.append(f"{shop['name']}: first run, recorded {whisky} whiskies via {adapter.name}")
        except BotChallenge as exc:
            db.commit()
            log.warning("%s: %s", shop["name"], exc)
            db.finish_run(run_id, adapter.name if adapter else None, base, whisky, f"bot check: {exc}")
            failures.append(f"{shop['name']}: site showed a bot check; skipped ({whisky} recorded before it)")
        except Exception as exc:  # noqa: BLE001 - one shop failing shouldn't stop the others
            db.commit()
            log.exception("%s failed", shop["name"])
            db.finish_run(run_id, adapter.name if adapter else None, base, whisky, repr(exc))
            failures.append(f"{shop['name']}: {exc!r}")

    # Best first; a bottle that qualifies at multiple shops only alerts once per shop.
    all_alerts.sort(key=lambda a: a.priority, reverse=True)
    repeat_days = int(cfg["alerts"].get("repeat_after_days", 14))
    fresh = [a for a in all_alerts if not db.recently_alerted(a.listing.key, a.kind, a.listing.price, repeat_days)]
    max_individual = int(n.get("max_individual_alerts", 8))

    for al in fresh[:max_individual]:
        title, body = format_alert(al)
        prio = 4 if al.palate.get("palate", 0) >= 75 or al.kind == "drop" else 3
        tags = ["tumbler_glass"] + (["fire"] if al.palate.get("track") == "peat_earth" else ["cherries"])
        if notifier.send(title, body, click=al.listing.url, priority=prio, tags=tags) and not dry_run:
            db.record_alert(al.listing.key, al.kind, al.listing.price)

    rest = fresh[max_individual:]
    if rest:
        body = "\n".join(
            f"• {a.listing.title} — ${a.listing.price:.2f} @ {a.listing.shop} (palate {a.palate.get('palate')}, {a.kind})"
            for a in rest[:25]
        )
        if notifier.send(f"{len(rest)} more whisky alerts", body, priority=2, tags=["tumbler_glass"]) and not dry_run:
            for a in rest[:25]:
                db.record_alert(a.listing.key, a.kind, a.listing.price)

    if seed_notes:
        top = db.top(5)
        body = "\n".join(seed_notes) + "\n\nTop matches so far:\n" + "\n".join(
            f"• {r['title']} — ${r['last_price'] or 0:.2f} @ {r['shop']}" for r in top
        )
        notifier.send("Whisky deal finder: baseline recorded", body, priority=2, tags=["white_check_mark"])

    if failures:
        notifier.send("Whisky deal finder: scrape problems", "\n".join(failures), priority=3, tags=["warning"])

    db.close()
    log.info("done: %d alerts sent/queued, %d failures", len(fresh), len(failures))
    return 1 if failures and len(failures) == len([s for s in cfg["shops"] if not only or s["name"] in only]) else 0
