from __future__ import annotations

import logging

from ..http import PoliteSession
from .htmlsite import HtmlSiteAdapter
from .shopify import ShopifyAdapter
from .woocommerce import WooCommerceAdapter

log = logging.getLogger(__name__)

ADAPTERS = {a.name: a for a in (ShopifyAdapter(), WooCommerceAdapter(), HtmlSiteAdapter())}
DETECT_ORDER = ["shopify", "woocommerce", "html"]


def resolve(http: PoliteSession, shop: dict):
    """Return (adapter, base_url). Honors an explicit `platform`, otherwise probes.

    `base_url` may be a list of candidates (e.g. bsw.com vs bswliquor.com); the
    first one where a platform is detected wins.
    """
    candidates = shop["base_url"] if isinstance(shop["base_url"], list) else [shop["base_url"]]
    candidates = [c.rstrip("/") for c in candidates]
    platform = shop.get("platform", "auto")
    if platform != "auto":
        return ADAPTERS[platform], candidates[0]
    for base in candidates:
        for name in DETECT_ORDER[:-1]:
            if ADAPTERS[name].detect(http, base):
                log.info("%s: detected %s at %s", shop["name"], name, base)
                return ADAPTERS[name], base
    log.info("%s: no JSON API found, falling back to HTML/structured-data scraping", shop["name"])
    return ADAPTERS["html"], candidates[0]
