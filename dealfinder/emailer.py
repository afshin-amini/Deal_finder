"""Daily digest email over SMTP (e.g. Gmail with an app password).

Configured entirely through environment variables so nothing personal lands in the repo:
  EMAIL_TO        recipient (comma-separate for several)
  SMTP_USER       login, usually the sending address
  SMTP_PASSWORD   for Gmail: an App Password (Google account > Security > App passwords)
  SMTP_HOST       default smtp.gmail.com
  SMTP_PORT       default 465 (SSL); 587 uses STARTTLS
  EMAIL_FROM      default SMTP_USER
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger(__name__)

SECTION_TITLES = {
    "drop": "Price drops",
    "new": "New arrivals",
    "restock": "Back in stock",
    "deal": "Deals",
}


class Emailer:
    def __init__(self, dry_run: bool = False):
        self.to = os.environ.get("EMAIL_TO", "").strip()
        self.user = os.environ.get("SMTP_USER", "").strip()
        self.password = os.environ.get("SMTP_PASSWORD", "")
        self.host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
        self.port = int(os.environ.get("SMTP_PORT", "465"))
        self.sender = os.environ.get("EMAIL_FROM", self.user)
        self.dry_run = dry_run

    @property
    def configured(self) -> bool:
        return bool(self.to and self.user and self.password)

    def send(self, subject: str, html_body: str, text_body: str) -> bool:
        if self.dry_run or not self.configured:
            if self.dry_run:
                print(f"\n[email dry-run] {subject}\n{text_body}\n")
            return self.dry_run
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self.sender
        msg["To"] = self.to
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype="html")
        try:
            ctx = ssl.create_default_context()
            if self.port == 465:
                with smtplib.SMTP_SSL(self.host, self.port, context=ctx, timeout=30) as s:
                    s.login(self.user, self.password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=30) as s:
                    s.starttls(context=ctx)
                    s.login(self.user, self.password)
                    s.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as exc:
            log.error("email send failed: %s", exc)
            return False


def _e(s) -> str:
    return html.escape(str(s or ""))


def render_digest(alerts: list, notes_snippet, seed_notes: list[str], failures: list[str],
                  top_rows: list | None = None) -> tuple[str, str, str]:
    """Return (subject, html, text). `alerts` are runner.Alert objects, best first."""
    by_kind: dict[str, list] = {}
    for a in alerts:
        by_kind.setdefault(a.kind, []).append(a)

    counts = ", ".join(f"{len(v)} {SECTION_TITLES[k].lower()}" for k, v in by_kind.items())
    subject = f"Whisky deals: {counts}" if alerts else "Whisky deals: nothing new today"
    if failures:
        subject += " (scrape problems)"

    h = ['<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:680px;color:#222">']
    t = []
    for kind in ("drop", "new", "restock", "deal"):
        items = by_kind.get(kind)
        if not items:
            continue
        h.append(f'<h2 style="font-size:18px;border-bottom:2px solid #b5651d;padding-bottom:4px">{SECTION_TITLES[kind]}</h2>')
        t.append(f"\n== {SECTION_TITLES[kind]} ==")
        for a in items:
            l = a.listing
            price = f"${l.price:.2f}" if l.price is not None else "?"
            track = "peaty/earthy" if a.palate.get("track") == "peat_earth" else "fruit/red-fruit"
            watch = ", ".join(a.palate.get("watchlist") or [])
            deal = "; ".join(a.deal.get("reasons") or [])
            why = "; ".join((a.palate.get("reasons") or {}).get(a.palate.get("track"), [])[:4])
            notes = notes_snippet(a.notes, 600) if a.notes else ""
            h.append('<div style="margin:12px 0 18px">')
            h.append(f'<div style="font-size:16px;font-weight:600"><a href="{_e(l.url)}" style="color:#7a3b0c">{_e(l.title)}</a></div>')
            h.append(f'<div style="font-size:15px"><b>{price}</b> at {_e(l.shop.upper())}'
                     + (f' &middot; <span style="color:#2e7d32">{_e(deal)}</span>' if deal else "") + "</div>")
            h.append(f'<div style="font-size:13px;color:#555">Palate {a.palate.get("palate")} ({track})'
                     + (f" &middot; watchlist: {_e(watch)}" if watch else "") + "</div>")
            if why:
                h.append(f'<div style="font-size:12px;color:#777">{_e(why)}</div>')
            if l.description and not notes:
                h.append(f'<div style="font-size:13px;margin-top:4px">{_e(l.description[:400])}</div>')
            if notes:
                h.append(f'<div style="font-size:13px;margin-top:4px;padding:6px 8px;background:#faf6f0;border-left:3px solid #b5651d">{_e(notes)}</div>')
            h.append("</div>")
            t.append(f"- {l.title} | {price} at {l.shop} | palate {a.palate.get('palate')} ({track})"
                     + (f" | {deal}" if deal else "") + f"\n  {l.url}" + (f"\n  {notes}" if notes else ""))

    if not alerts:
        h.append("<p>No new bottles, price drops or restocks matched your palate today.</p>")
        t.append("No new bottles, price drops or restocks matched your palate today.")
    if seed_notes:
        h.append('<h2 style="font-size:16px">Baseline recorded</h2><ul>' + "".join(f"<li>{_e(n)}</li>" for n in seed_notes) + "</ul>")
        t.append("\nBaseline recorded:\n" + "\n".join(seed_notes))
    if top_rows:
        h.append('<h3 style="font-size:15px">Best palate matches in stock</h3><ul>')
        for r in top_rows:
            h.append(f'<li><a href="{_e(r["url"])}">{_e(r["title"])}</a> &mdash; ${r["last_price"] or 0:.2f} @ {_e(r["shop"])}</li>')
            t.append(f"- {r['title']} ${r['last_price'] or 0:.2f} @ {r['shop']}")
        h.append("</ul>")
    if failures:
        h.append('<h3 style="font-size:15px;color:#b00020">Scrape problems</h3><ul>' + "".join(f"<li>{_e(f)}</li>" for f in failures) + "</ul>")
        t.append("\nScrape problems:\n" + "\n".join(failures))
    h.append("</div>")
    return subject, "".join(h), "\n".join(t)
