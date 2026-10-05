from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Listing:
    """One purchasable bottle as seen on a shop at scrape time."""

    shop: str
    product_id: str  # stable id within the shop (Shopify variant id, SKU, or URL)
    title: str
    url: str
    price: float | None
    currency: str = "CAD"
    in_stock: bool | None = None
    vendor: str = ""
    product_type: str = ""
    tags: list[str] = field(default_factory=list)
    description: str = ""  # plain text; tasting notes when the shop has them
    compare_at_price: float | None = None  # shop's own "was" price, if shown

    @property
    def key(self) -> str:
        return f"{self.shop}:{self.product_id}"

    def text_blob(self) -> str:
        """Everything the parser and scorer are allowed to look at."""
        return " ".join(
            [self.title, self.vendor, self.product_type, " ".join(self.tags), self.description]
        )
