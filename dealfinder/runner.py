"""The daily run: scrape every shop, record prices, score, decide and send alerts."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from . import adapters
from .db import DB, now
from .http import BotChallenge, PoliteSession
from .models import Listing
from .emailer import Emailer, render_digest
from .notify import Ntfy
from .parser import parse
from .scoring import deal_score, palate_score
from .collection import Collection, load_collection
from .taste import TasteProfile, blend, load_profile

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
             notes: str = "", profile: TasteProfile | None = None,
             coll: Collection | None = None) -> tuple[dict, dict, dict, list[Alert]]:
    if not shop_cfg.get("trust_compare_at", True):
        listing.compare_at_price = None  # shop shows a permanent "was" price; history is the real signal
    # Facts (cask, age, ABV) come from the listing; tasting notes only feed the flavour words below,
    # since notes prose ("Finish: ...", "like a PX bomb") would fake cask detections.
    parsed = parse(listing.title, f"{listing.vendor} {listing.product_type} {' '.join(listing.tags)} {listing.description}")
    if not parsed.is_whisky:
        return parsed.to_dict(), {}, {}, []
    text = f"{listing.text_blob()} {notes}"
    score = palate_score(parsed, text, cfg["watchlist"], has_notes=bool(notes) or shop_cfg.get("has_tasting_notes", False))
    prev = db.get(listing.key)
    history = db.price_history(listing.key)
    deal = deal_score(listing.price, history, prev["last_price"] if prev else None,
                      listing.compare_at_price, parsed, cfg["value"])

    sd = score.to_dict()
    if profile is not None and profile.active:
        pred = profile.predict(parsed, text)
        sd["rule_palate"] = score.palate
        sd["personal"] = pred.to_dict()
        sd["palate"] = blend(score.palate, pred, profile)

    a = cfg["alerts"]
    alerts: list[Alert] = []
    palate = sd["palate"]
    watched = bool(score.watchlist)
    in_stock = listing.in_stock is not False
    is_mini = (parsed.volume_ml is not None and parsed.volume_ml < 300) or bool(
        re.search(r"\b(mini|miniature|sample|gift set|tasting set)\b", listing.title, re.I))

    def add(kind: str, extra: float = 0) -> None:
        pr = palate + deal.score + (10 if watched else 0) + extra
        alerts.append(Alert(kind, listing, sd, deal.to_dict(), pr, notes=notes))

    # Your watchlist: alert at or under your target (or, with no target, on any drop or restock).
    w = coll.watching(listing.key, listing.title) if coll else None
    if w and in_stock and listing.price:
        hit_target = w.target is not None and listing.price <= w.target
        hit_any = w.target is None and prev is not None and (deal.drop_pct or prev["last_in_stock"] == 0)
        if hit_target or hit_any:
            why = (f"at or under your target ${w.target:.0f}" if hit_target else "price drop or back in stock")
            d = deal.to_dict()
            d["reasons"] = [f"Watchlist: {why}"] + d["reasons"]
            alerts.append(Alert("watch", listing, sd, d, 200 + palate, notes=notes))
            sd["watch"] = {"id": w.id, "target": w.target}
    owned = bool(coll and coll.owns(listing.key, listing.title))
    if owned:
        sd["owned"] = True

    if seeded and in_stock and listing.price and not is_mini and not owned and not alerts:
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
    return parsed.to_dict(), sd, deal.to_dict(), alerts


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
    head = {"new": "New", "drop": "Price drop", "restock": "Back in stock", "deal": "Deal", "watch": "Watchlist"}[al.kind]
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
    profile = load_profile(cfg.get("journal_path", "docs/data/journal.json"))
    coll = load_collection(cfg.get("journal_path", "docs/data/journal.json"))
    log.info("watchlist: %d items; bottles owned: %d", len(coll.watch), len(coll.owned_keys) + len(coll.owned_names))
    if profile is not None:
        log.info("taste profile: %d journal entries (%s)", len(profile.entries),
                 "active" if profile.active else f"needs {8 - len(profile.entries)} more to switch on")
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
                parsed, score, deal, alerts = evaluate(listing, shop_cfg, cfg, db, seeded, notes, profile, coll)
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

    sent: set[int] = set()  # indexes into `fresh` delivered by at least one channel

    # --- email: one daily digest with everything, tasting notes included ---
    emailer = Emailer(dry_run=dry_run)
    if emailer.configured or dry_run:
        top = db.top(10) if seed_notes else None
        subject, html_body, text_body = render_digest(fresh, notes_snippet, seed_notes, failures, top)
        if (fresh or seed_notes or failures or cfg.get("email", {}).get("send_when_empty", True)) and \
                emailer.send(subject, html_body, text_body):
            sent.update(range(len(fresh)))

    # --- ntfy: a push per top alert, the rest in one digest ---
    if notifier.topic or not emailer.configured:
        for i, al in enumerate(fresh[:max_individual]):
            title, body = format_alert(al)
            prio = 4 if al.palate.get("palate", 0) >= 75 or al.kind == "drop" else 3
            tags = ["tumbler_glass"] + (["fire"] if al.palate.get("track") == "peat_earth" else ["cherries"])
            if notifier.send(title, body, click=al.listing.url, priority=prio, tags=tags):
                sent.add(i)
        rest = fresh[max_individual:]
        if rest:
            body = "\n".join(
                f"• {a.listing.title} — ${a.listing.price:.2f} @ {a.listing.shop} (palate {a.palate.get('palate')}, {a.kind})"
                for a in rest[:25]
            )
            if notifier.send(f"{len(rest)} more whisky alerts", body, priority=2, tags=["tumbler_glass"]):
                sent.update(range(max_individual, max_individual + min(25, len(rest))))
        if seed_notes:
            top5 = db.top(5)
            body = "\n".join(seed_notes) + "\n\nTop matches so far:\n" + "\n".join(
                f"• {r['title']} — ${r['last_price'] or 0:.2f} @ {r['shop']}" for r in top5
            )
            notifier.send("Whisky deal finder: baseline recorded", body, priority=2, tags=["white_check_mark"])
        if failures:
            notifier.send("Whisky deal finder: scrape problems", "\n".join(failures), priority=3, tags=["warning"])

    if not dry_run:
        for i in sorted(sent):
            db.record_alert(fresh[i].listing.key, fresh[i].kind, fresh[i].listing.price)

    if cfg.get("export_dir"):
        from .export import export_app_data
        export_app_data(db, cfg["export_dir"], profile)

    db.close()
    log.info("done: %d alerts sent/queued, %d failures", len(fresh), len(failures))
    return 1 if failures and len(failures) == len([s for s in cfg["shops"] if not only or s["name"] in only]) else 0
