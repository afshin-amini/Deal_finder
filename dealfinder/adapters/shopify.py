"""Shopify storefronts expose every published product at /products.json."""

from __future__ import annotations

import logging
from typing import Iterator

from ..http import PoliteSession
from ..models import Listing
from .base import html_to_text, to_price

log = logging.getLogger(__name__)

PAGE_SIZE = 250  # Shopify's max
MAX_PAGES = 200


class ShopifyAdapter:
    name = "shopify"

    def detect(self, http: PoliteSession, base_url: str) -> bool:
        try:
            resp = http.get(f"{base_url}/products.json", params={"limit": 1}, accept="application/json")
        except Exception as exc:  # noqa: BLE001 - detection must not crash
            log.debug("shopify detect failed for %s: %s", base_url, exc)
            return False
        if resp.status_code != 200 or "json" not in resp.headers.get("Content-Type", ""):
            return False
        try:
            return isinstance(resp.json().get("products"), list)
        except ValueError:
            return False

    def fetch(self, http: PoliteSession, shop: dict) -> Iterator[Listing]:
        base = shop["base_url"]
        # Scoping to whisky collections (if configured) is much cheaper than the whole catalog.
        paths = [f"/collections/{c}/products.json" for c in shop.get("collections", [])] or ["/products.json"]
        seen: set[str] = set()
        for path in paths:
            for page in range(1, MAX_PAGES + 1):
                resp = http.get(base + path, params={"limit": PAGE_SIZE, "page": page}, accept="application/json")
                if resp.status_code != 200:
                    log.warning("%s%s page %s -> HTTP %s", base, path, page, resp.status_code)
                    break
                products = resp.json().get("products", [])
                if not products:
                    break
                for p in products:
                    for listing in parse_product(shop["name"], base, p):
                        if listing.key not in seen:
                            seen.add(listing.key)
                            yield listing
                if len(products) < PAGE_SIZE:
                    break


def parse_product(shop_name: str, base: str, p: dict) -> Iterator[Listing]:
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    description = html_to_text(p.get("body_html"))
    variants = p.get("variants") or [{}]
    for v in variants:
        title = p.get("title", "")
        vt = v.get("title") or ""
        if vt and vt.lower() not in ("default title", "default"):
            title = f"{title} - {vt}"
        url = f"{base}/products/{p.get('handle', '')}"
        if len(variants) > 1 and v.get("id"):
            url += f"?variant={v['id']}"
        yield Listing(
            shop=shop_name,
            product_id=str(v.get("id") or p.get("id")),
            title=title,
            url=url,
            price=to_price(v.get("price")),
            in_stock=v.get("available"),
            vendor=p.get("vendor") or "",
            product_type=p.get("product_type") or "",
            tags=list(tags),
            description=description,
            compare_at_price=to_price(v.get("compare_at_price")),
        )
