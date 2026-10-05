"""Kensington Wine Market (custom PHP site).

Category pages (/products/scotch?page=N) embed every product's name, price,
region, vintage and description in a modal, so a daily price sweep only needs
the category pages. Page size and the in-stock filter are session settings set
by two AJAX posts. Andrew's/Evan's tasting notes (Nose/Palate/Finish) live only
on the product page, so those are fetched once per bottle via `fetch_notes`.

robots.txt asks for Crawl-delay: 20, which PoliteSession honours.
"""

from __future__ import annotations

import logging
import re
from typing import Iterator

from ..http import PoliteSession
from ..models import Listing
from .base import html_to_text, to_price

log = logging.getLogger(__name__)

MAX_PAGES = 60

_MODAL_SPLIT = 'class="modal fade" id="product-'
_LINK_RE = re.compile(r'href="/product/(\d+)/([^"#?]+)"')
_NAME_RE = re.compile(r"<h3>(.*?)</h3>", re.S)
_PRICE_RE = re.compile(r'data-price="([\d.,]+)"')
_H4_PRICE_RE = re.compile(r'<p class="h4">\$([\d.,]+)')
_META_RE = re.compile(r'<span class="h6 mr-2">([A-Za-z ]+):</span>([^<]*)')
_DESC_RE = re.compile(r'<p class="d-none d-lg-block">(.*?)<!-- Add to Cart -->', re.S)
_LAST_PAGE_RE = re.compile(r"\?page=(\d+)#results")


def parse_listing_page(shop_name: str, base: str, html_text: str) -> list[Listing]:
    out = []
    parts = html_text.split(_MODAL_SPLIT)
    for before, block in zip(parts, parts[1:]):
        block = block.split("<!-- Item -->")[0]
        # Each modal follows its product card; the card's link carries the SKU and slug.
        links = _LINK_RE.findall(before)
        name_m = _NAME_RE.search(block)
        if not (links and name_m):
            continue
        sku, slug = links[-1]
        price_m = _PRICE_RE.search(block) or _H4_PRICE_RE.search(block)
        meta = {k.strip().lower(): html_to_text(v) for k, v in _META_RE.findall(block)}
        desc_m = _DESC_RE.search(block)
        out.append(Listing(
            shop=shop_name,
            product_id=sku,
            title=html_to_text(name_m.group(1)),
            url=f"{base}/product/{sku}/{slug}",
            price=to_price(price_m.group(1)) if price_m else None,
            in_stock="addtocart" in block,  # samples/sold-out items have no cart button
            product_type=meta.get("region", ""),
            tags=[f"vintage {meta['vintage']}"] if meta.get("vintage") else [],
            description=html_to_text(desc_m.group(1)) if desc_m else "",
        ))
    return out


_NOTES_RE = re.compile(r"(Andrew.s Tasting Note|Evan.s Tasting Note|Tasting Notes?)\s*(.*)", re.S)
_NOTES_END = re.compile(r"\b(Adapted from the article|Related Products|You may also like|Follow Us|Newsletter)\b")


def parse_notes(html_text: str) -> str:
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html_text)
    text = html_to_text(body)
    m = _NOTES_RE.search(text)
    if not m:
        return ""
    notes = text[m.start():]
    end = _NOTES_END.search(notes, 30)
    notes = notes[: end.start()] if end else notes
    notes = re.sub(r"^(Tasting Notes\s+Distillery\s+)", "", notes)
    return notes[:4000].strip()


class KwmAdapter:
    name = "kwm"

    def detect(self, http: PoliteSession, base_url: str) -> bool:
        return "kensingtonwinemarket.com" in base_url

    def _setup_session(self, http: PoliteSession, base: str, category: str) -> None:
        http.get(f"{base}/products/{category}/")  # establishes the PHP session cookie
        http.post(f"{base}/includes/post/pagination-count.ajax.php", data={"page_count": 100})
        http.post(f"{base}/includes/post/filter-chk.ajax.php",
                  data={"chk": "chk-instock-only", "page": category, "type": "include"})

    def fetch(self, http: PoliteSession, shop: dict) -> Iterator[Listing]:
        base = shop["base_url"]
        seen: set[str] = set()
        for category in shop.get("categories", ["scotch"]):
            self._setup_session(http, base, category)
            last = None
            for page in range(1, MAX_PAGES + 1):
                resp = http.get(f"{base}/products/{category}", params={"page": page})
                if resp.status_code != 200:
                    log.warning("kwm %s page %s -> HTTP %s", category, page, resp.status_code)
                    break
                if last is None:
                    nums = [int(n) for n in _LAST_PAGE_RE.findall(resp.text)]
                    last = max(nums) if nums else 1
                    log.info("kwm %s: %s pages", category, last)
                items = parse_listing_page(shop["name"], base, resp.text)
                new = [l for l in items if l.key not in seen]
                if not new:
                    break
                for l in new:
                    seen.add(l.key)
                    yield l
                if page >= last:
                    break

    def fetch_notes(self, http: PoliteSession, url: str) -> str:
        resp = http.get(url)
        return parse_notes(resp.text) if resp.status_code == 200 else ""
