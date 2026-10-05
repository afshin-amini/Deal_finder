from __future__ import annotations

import html
import re
from typing import Iterator, Protocol

from ..http import PoliteSession
from ..models import Listing


class Adapter(Protocol):
    name: str

    def detect(self, http: PoliteSession, base_url: str) -> bool:
        """Cheap check: does this shop speak this platform's API?"""

    def fetch(self, http: PoliteSession, shop: dict) -> Iterator[Listing]:
        """Yield every listing the shop exposes (filtering happens later)."""


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def html_to_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", " \n", s)
    s = _TAG_RE.sub(" ", s)
    return _WS_RE.sub(" ", html.unescape(s)).strip()


def to_price(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        return round(float(str(v).replace(",", "").replace("$", "").strip()), 2)
    except ValueError:
        return None
