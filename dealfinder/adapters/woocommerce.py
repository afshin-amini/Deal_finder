"""WooCommerce (WordPress) shops expose a public Store API at /wp-json/wc/store/v1/products."""

from __future__ import annotations

import logging
from typing import Iterator

from ..http import PoliteSession
from ..models import Listing
from .base import html_to_text

log = logging.getLogger(__name__)

API = "/wp-json/wc/store/v1/products"
PAGE_SIZE = 100
MAX_PAGES = 200


class WooCommerceAdapter:
    name = "woocommerce"

    def detect(self, http: PoliteSession, base_url: str) -> bool:
        try:
            resp = http.get(base_url + API, params={"per_page": 1}, accept="application/json")
            return resp.status_code == 200 and isinstance(resp.json(), list)
        except Exception as exc:  # noqa: BLE001
            log.debug("woocommerce detect failed for %s: %s", base_url, exc)
            return False

    def fetch(self, http: PoliteSession, shop: dict) -> Iterator[Listing]:
        base = shop["base_url"]
        categories = shop.get("categories") or [None]
        seen: set[str] = set()
        for cat in categories:
            for page in range(1, MAX_PAGES + 1):
                params = {"per_page": PAGE_SIZE, "page": page}
                if cat:
                    params["category"] = cat
                resp = http.get(base + API, params=params, accept="application/json")
                if resp.status_code != 200:
                    break
                items = resp.json()
                if not items:
                    break
                for item in items:
                    listing = parse_item(shop["name"], item)
                    if listing.key not in seen:
                        seen.add(listing.key)
                        yield listing
                if len(items) < PAGE_SIZE:
                    break


def _money(prices: dict, key: str) -> float | None:
    raw = prices.get(key)
    if raw in (None, ""):
        return None
    minor = int(prices.get("currency_minor_unit", 2))
    return round(int(raw) / (10**minor), 2)


def parse_item(shop_name: str, item: dict) -> Listing:
    prices = item.get("prices") or {}
    price = _money(prices, "price")
    regular = _money(prices, "regular_price")
    return Listing(
        shop=shop_name,
        product_id=str(item.get("id")),
        title=html_to_text(item.get("name")),
        url=item.get("permalink", ""),
        price=price,
        currency=prices.get("currency_code", "CAD"),
        in_stock=item.get("is_in_stock"),
        product_type=" ".join(c.get("name", "") for c in item.get("categories", [])),
        tags=[t.get("name", "") for t in item.get("tags", [])],
        description=html_to_text((item.get("short_description") or "") + " " + (item.get("description") or "")),
        compare_at_price=regular if regular and price and regular > price else None,
    )
