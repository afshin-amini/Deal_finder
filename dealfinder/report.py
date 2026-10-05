"""On-demand report: every bottle matching a search, with full details and tasting notes, by email.

  python -m dealfinder report "Ardmore" --shop kwm

Uses the shop's own search when it has one (KWM), otherwise scans the shop's
whisky collections and filters by name. Doesn't touch the price database.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date
from pathlib import Path

from . import adapters
from .emailer import Emailer
from .http import PoliteSession
from .models import Listing
from .parser import parse
from .scoring import palate_score

log = logging.getLogger(__name__)


def _matches(listing: Listing, query: str) -> bool:
    return query.lower() in f"{listing.title} {listing.vendor} {listing.description}".lower()


def collect(cfg: dict, query: str, shops: list[str] | None, with_notes: bool = True,
            include_out_of_stock: bool = False) -> list[dict]:
    h = cfg["http"]
    http = PoliteSession(user_agent=h.get("user_agent") or PoliteSession().user_agent,
                         min_delay=float(h.get("min_delay_seconds", 4.0)),
                         timeout=float(h.get("timeout_seconds", 90)))
    rows = []
    for shop in cfg["shops"]:
        if shops and shop["name"] not in shops:
            continue
        if not shops and not shop.get("enabled", True):
            continue
        adapter, base = adapters.resolve(http, shop)
        if shop.get("min_delay_seconds"):
            http.set_host_delay(base, float(shop["min_delay_seconds"]))
        shop_cfg = {**shop, "base_url": base}
        search = getattr(adapter, "search", None)
        found = list(search(http, shop_cfg, query)) if search else \
            [l for l in adapter.fetch(http, shop_cfg) if _matches(l, query)]
        if not include_out_of_stock:
            skipped = sum(1 for l in found if l.in_stock is False)
            found = [l for l in found if l.in_stock is not False]
            log.info("%s: skipping %d out-of-stock bottles", shop["name"], skipped)
        log.info("%s: %d bottles match %r", shop["name"], len(found), query)
        fetch_notes = getattr(adapter, "fetch_notes", None)
        for l in found:
            notes = ""
            if with_notes and fetch_notes:
                try:
                    notes = fetch_notes(http, l.url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("notes failed for %s: %s", l.url, exc)
            p = parse(l.title, f"{l.product_type} {' '.join(l.tags)} {l.description}")
            s = palate_score(p, f"{l.text_blob()} {notes}".lower(), cfg["watchlist"], has_notes=bool(notes))
            rows.append({"listing": l, "parsed": p, "score": s, "notes": notes})
    rows.sort(key=lambda r: (-r["score"].palate, r["listing"].price or 0))
    return rows


def _e(s) -> str:
    return html.escape(str(s or ""))


def _notes_html(notes: str) -> str:
    out = []
    for block in notes.split(" | "):
        m = re.match(r"(.*?Tasting Note)\s+", block)
        who = m.group(1) if m else "Tasting note"
        body = block[m.end():] if m else block
        parts = re.split(r"\s*(?=\b(?:Nose|Palate|Finish|Comment)\s*:)", body)
        lines = []
        for part in filter(None, parts):
            mm = re.match(r"(Nose|Palate|Finish|Comment)\s*:\s*", part)
            lines.append(f"<b>{mm.group(1)}:</b> {_e(part[mm.end():])}" if mm else _e(part))
        out.append(f'<div style="margin-top:6px"><div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#666">'
                   f'{_e(who)}</div>' + "<br>".join(lines) + "</div>")
    return "".join(out)


def render(query: str, rows: list[dict]) -> tuple[str, str, str]:
    today = date.today().strftime("%-d %b %Y")
    shops = sorted({r["listing"].shop.upper() for r in rows})
    subject = f"{query} at {', '.join(shops) or 'your shops'}: {len(rows)} bottles in stock"
    h = ['<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:680px;color:#222">',
         f'<h1 style="font-size:20px;margin:0 0 4px">{_e(query)}: {len(rows)} bottles</h1>',
         f'<div style="color:#666;font-size:13px;margin-bottom:12px">{_e(", ".join(shops))} &middot; {today} &middot; best palate match first</div>']
    t = [f"{query}: {len(rows)} bottles ({', '.join(shops)}, {today})", ""]
    for r in rows:
        l, p, s = r["listing"], r["parsed"], r["score"]
        facts = " · ".join(x for x in [
            f"{p.age} yr" if p.age else "", f"{p.abv}% abv" if p.abv else "",
            f"{p.volume_ml} ml" if p.volume_ml else "", l.product_type.replace("Scotland > ", ""),
            ", ".join(t.replace("vintage ", "vintage ") for t in l.tags if t.startswith("vintage")),
            "single cask" if p.single_cask else "", "cask strength" if p.cask_strength else "",
            ", ".join(c.replace("_", " ") for c in p.casks[:3]), f"peat: {p.peat}" if p.peat not in ("none", "unknown") else "",
        ] if x)
        price = f"${l.price:,.2f}" if l.price is not None else "?"
        stock = "" if l.in_stock is not False else ' &middot; <span style="color:#a33">not orderable online</span>'
        h.append('<div style="padding:12px 0;border-top:1px solid #ddd">')
        h.append(f'<div style="font-size:16px;font-weight:600"><a href="{_e(l.url)}" style="color:#2c5a4c">{_e(l.title)}</a></div>')
        h.append(f'<div style="font-size:15px"><b>{price}</b> at {_e(l.shop.upper())}{stock}</div>')
        h.append(f'<div style="font-size:13px;color:#555">{_e(facts)}</div>')
        h.append(f'<div style="font-size:12px;color:#777">Peaty/earthy {s.peat_earth} &middot; Fruit/red fruit {s.fruit_earth}</div>')
        if l.description:
            h.append(f'<div style="font-size:13px;margin-top:6px">{_e(l.description[:700])}</div>')
        if r["notes"]:
            h.append(f'<div style="font-size:13px;margin-top:6px;padding:6px 10px;background:#f7f4ee;border-left:3px solid #8a5a12">'
                     f'{_notes_html(r["notes"])}</div>')
        h.append("</div>")
        t += [f"{l.title} — {price} at {l.shop}", f"  {facts}", f"  peaty/earthy {s.peat_earth}, fruit/red fruit {s.fruit_earth}",
              f"  {l.url}"] + ([f"  {r['notes'][:500]}"] if r["notes"] else []) + [""]
    if not rows:
        h.append("<p>No bottles matched.</p>")
    h.append("</div>")
    return subject, "".join(h), "\n".join(t)


def run_report(cfg: dict, query: str, shops: list[str] | None, out_path: str, dry_run: bool = False,
               include_out_of_stock: bool = False) -> int:
    rows = collect(cfg, query, shops, include_out_of_stock=include_out_of_stock)
    subject, html_body, text_body = render(query, rows)
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(f"<!doctype html><meta charset='utf-8'><title>{_e(subject)}</title>{html_body}")
    print(text_body)
    emailer = Emailer(dry_run=dry_run)
    if emailer.configured and not dry_run:
        ok = emailer.send(subject, html_body, text_body)
        # Actions logs are public on a public repo: never print the address.
        print("Email sent." if ok else "EMAIL FAILED - check the SMTP_USER / SMTP_PASSWORD secrets")
        return 0 if ok else 1
    print("Email not configured (EMAIL_TO / SMTP_USER / SMTP_PASSWORD); report saved to " + out_path)
    return 0
