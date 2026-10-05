"""Fallback for custom-built shops: find product pages, read schema.org data from each.

Product URLs come from category/listing pages (`listing_urls`, with an optional
`{page}` placeholder) and/or from the sitemap. Each product page is parsed for
JSON-LD `Product` data, falling back to OpenGraph/product meta tags. Most shop
platforms emit one or the other for Google Shopping.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from typing import Iterator
from urllib.parse import urljoin, urlsplit

from ..http import PoliteSession, RobotsDisallowed
from ..models import Listing
from .base import html_to_text, to_price

log = logging.getLogger(__name__)

DEFAULT_PRODUCT_PATTERN = r"/(products?|item|p|shop)/[^/?#]+"
MAX_LISTING_PAGES = 50
MAX_SITEMAPS = 30


def auto_keywords() -> list[str]:
    """URL-slug words that suggest a whisky page: generic terms + every distillery and bottler we know."""
    from ..parser import BOTTLERS, DISTILLERIES

    words = {"whisky", "whiskey", "scotch", "single-malt", "malt", "cask", "year-old", "yo"}
    words |= {d.replace(" ", "-") for d in DISTILLERIES}
    words |= {a.replace(" & ", "-").replace(" ", "-") for aliases in BOTTLERS.values() for a in aliases if len(a) > 4}
    return sorted(words)


class _PageScan(HTMLParser):
    """Collect JSON-LD blocks, <meta> tags, <link rel=canonical>, and <a href>s."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.jsonld: list[str] = []
        self.meta: dict[str, str] = {}
        self.links: list[str] = []
        self.canonical: str | None = None
        self.title = ""
        self._in_jsonld = False
        self._in_title = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_jsonld, self._buf = True, []
        elif tag == "meta":
            k = a.get("property") or a.get("name") or a.get("itemprop")
            if k and a.get("content") is not None:
                self.meta.setdefault(k.lower(), a["content"])
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag == "link" and (a.get("rel") or "").lower() == "canonical":
            self.canonical = a.get("href")
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._buf))
            self._in_jsonld = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_jsonld:
            self._buf.append(data)
        elif self._in_title:
            self.title += data


def scan(html_text: str) -> _PageScan:
    s = _PageScan()
    s.feed(html_text)
    return s


def _walk(node) -> Iterator[dict]:
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for k in ("@graph", "mainEntity", "itemListElement", "item"):
            if k in node:
                yield from _walk(node[k])


def _is_type(node: dict, name: str) -> bool:
    t = node.get("@type")
    return name in t if isinstance(t, list) else t == name


def find_products(page: _PageScan) -> list[dict]:
    out = []
    for raw in page.jsonld:
        try:
            data = json.loads(raw.strip())
        except ValueError:
            # Some sites emit trailing commas or raw newlines inside strings.
            try:
                data = json.loads(re.sub(r",\s*([}\]])", r"\1", raw.strip()).replace("\n", " "))
            except ValueError:
                continue
        out.extend(n for n in _walk(data) if _is_type(n, "Product"))
    return out


def _first_offer(offers) -> dict:
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    if not isinstance(offers, dict):
        return {}
    if _is_type(offers, "AggregateOffer") and "offers" in offers:
        return _first_offer(offers["offers"]) or offers
    return offers


_NOTES_START = re.compile(r"(tasting notes?|nose\s*[:\-]|palate\s*[:\-]|notes from)", re.I)


def extract_notes(html_text: str, limit: int = 1500) -> str:
    """Grab the tasting-notes section from a page body (KWM-style write-ups)."""
    body = re.sub(r"(?is)<(script|style|nav|header|footer)[^>]*>.*?</\1>", " ", html_text)
    text = html_to_text(body)
    m = _NOTES_START.search(text)
    return text[m.start(): m.start() + limit] if m else ""


def parse_product_page(shop_name: str, url: str, html_text: str, want_notes: bool = False) -> Listing | None:
    listing = _parse_product_page(shop_name, url, html_text)
    if listing and want_notes:
        notes = extract_notes(html_text)
        if notes and notes not in listing.description:
            listing.description = f"{listing.description} {notes}".strip()
    return listing


def _parse_product_page(shop_name: str, url: str, html_text: str) -> Listing | None:
    page = scan(html_text)
    canonical = urljoin(url, page.canonical) if page.canonical else url
    products = find_products(page)
    if products:
        p = products[0]
        offer = _first_offer(p.get("offers"))
        price = to_price(offer.get("price") or offer.get("lowPrice"))
        availability = str(offer.get("availability", ""))
        brand = p.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name", "")
        return Listing(
            shop=shop_name,
            product_id=str(p.get("sku") or p.get("productID") or canonical),
            title=html_to_text(p.get("name") or page.title),
            url=canonical,
            price=price,
            currency=offer.get("priceCurrency") or "CAD",
            in_stock=("InStock" in availability or "LimitedAvailability" in availability) if availability else None,
            vendor=str(brand or ""),
            product_type=str(p.get("category") or ""),
            description=html_to_text(p.get("description")),
        )
    m = page.meta
    price = to_price(m.get("product:price:amount") or m.get("og:price:amount") or m.get("price"))
    if price is None:
        return None
    avail = (m.get("product:availability") or m.get("og:availability") or "").lower()
    return Listing(
        shop=shop_name,
        product_id=canonical,
        title=html_to_text(m.get("og:title") or page.title),
        url=canonical,
        price=price,
        currency=m.get("product:price:currency") or m.get("og:price:currency") or "CAD",
        in_stock=("in" in avail and "out" not in avail) if avail else None,
        description=html_to_text(m.get("og:description") or m.get("description")),
    )


class HtmlSiteAdapter:
    name = "html"

    def detect(self, http: PoliteSession, base_url: str) -> bool:
        return True  # last resort; always "works" if pages carry structured data

    # ---- URL discovery --------------------------------------------------

    def _listing_page_urls(self, http: PoliteSession, shop: dict) -> Iterator[str]:
        pattern = re.compile(shop.get("product_url_pattern", DEFAULT_PRODUCT_PATTERN))
        host = urlsplit(shop["base_url"]).netloc
        for template in shop.get("listing_urls", []):
            pages = range(1, MAX_LISTING_PAGES + 1) if "{page}" in template else [None]
            prev: set[str] = set()
            for n in pages:
                url = urljoin(shop["base_url"], template.format(page=n) if n else template)
                try:
                    resp = http.get(url)
                except RobotsDisallowed:
                    log.warning("robots.txt disallows %s", url)
                    break
                if resp.status_code != 200:
                    break
                found = {
                    urljoin(url, h).split("#")[0]
                    for h in scan(resp.text).links
                    if pattern.search(urljoin(url, h)) and urlsplit(urljoin(url, h)).netloc == host
                }
                if not found or found <= prev:
                    break  # ran off the end of pagination
                yield from sorted(found - prev)
                prev |= found

    def _sitemap_urls(self, http: PoliteSession, shop: dict) -> Iterator[str]:
        pattern = re.compile(shop.get("product_url_pattern", DEFAULT_PRODUCT_PATTERN))
        keywords = shop.get("sitemap_keywords", "auto")
        keywords = auto_keywords() if keywords == "auto" else [k.lower() for k in keywords]
        queue = [urljoin(shop["base_url"], shop.get("sitemap", "/sitemap.xml"))]
        visited = 0
        while queue and visited < MAX_SITEMAPS:
            sm = queue.pop(0)
            visited += 1
            try:
                resp = http.get(sm, accept="application/xml")
            except RobotsDisallowed:
                continue
            if resp.status_code != 200:
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                continue
            ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for loc in root.findall("s:sitemap/s:loc", ns):
                queue.append(loc.text.strip())
            for loc in root.findall("s:url/s:loc", ns):
                u = loc.text.strip()
                if pattern.search(u) and (not keywords or any(k in u.lower() for k in keywords)):
                    yield u

    def fetch(self, http: PoliteSession, shop: dict) -> Iterator[Listing]:
        limit = shop.get("max_products", 400)
        sources = []
        if shop.get("listing_urls"):
            sources.append(self._listing_page_urls(http, shop))
        if shop.get("use_sitemap", not shop.get("listing_urls")):
            sources.append(self._sitemap_urls(http, shop))
        seen: set[str] = set()
        for src in sources:
            for url in src:
                if url in seen:
                    continue
                if len(seen) >= limit:
                    log.info("%s: hit max_products=%s", shop["name"], limit)
                    return
                seen.add(url)
                try:
                    resp = http.get(url)
                except RobotsDisallowed:
                    continue
                except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
                    log.warning("fetch failed %s: %s", url, exc)
                    continue
                if resp.status_code != 200:
                    continue
                listing = parse_product_page(shop["name"], url, resp.text, shop.get("has_tasting_notes", False))
                if listing:
                    yield listing
                else:
                    log.debug("no product data on %s", url)
