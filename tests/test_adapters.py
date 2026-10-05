from dealfinder.adapters.htmlsite import parse_product_page
from dealfinder.adapters.shopify import parse_product
from dealfinder.adapters.woocommerce import parse_item


def test_shopify_product():
    p = {
        "id": 1, "title": "Ardmore 12 Year Old", "handle": "ardmore-12", "vendor": "Ardmore",
        "product_type": "Scotch Whisky", "tags": ["Highland", "Peated"],
        "body_html": "<p>Smoky &amp; <b>earthy</b></p>",
        "variants": [{"id": 11, "title": "Default Title", "price": "89.99", "available": True,
                      "compare_at_price": "99.99"}],
    }
    [l] = list(parse_product("crown", "https://x.com", p))
    assert l.price == 89.99 and l.compare_at_price == 99.99 and l.in_stock is True
    assert l.title == "Ardmore 12 Year Old" and l.url == "https://x.com/products/ardmore-12"
    assert l.description == "Smoky & earthy" and l.key == "crown:11"


def test_shopify_multi_variant():
    p = {"id": 1, "title": "Talisker 10", "handle": "t10", "variants": [
        {"id": 1, "title": "750ml", "price": "80"}, {"id": 2, "title": "1.75L", "price": "150"}]}
    ls = list(parse_product("s", "https://x.com", p))
    assert [l.title for l in ls] == ["Talisker 10 - 750ml", "Talisker 10 - 1.75L"]
    assert ls[1].url.endswith("?variant=2")


def test_woocommerce_item():
    item = {"id": 5, "name": "Ben Nevis 10", "permalink": "https://y.com/product/bn10",
            "prices": {"price": "8999", "regular_price": "9999", "currency_code": "CAD", "currency_minor_unit": 2},
            "is_in_stock": True, "categories": [{"name": "Scotch"}], "tags": [], "description": "<p>Waxy</p>"}
    l = parse_item("craft", item)
    assert l.price == 89.99 and l.compare_at_price == 99.99 and l.description == "Waxy"


JSONLD_PAGE = """<html><head><title>x</title>
<link rel="canonical" href="/products/ardmore-legacy">
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[{"@type":"BreadcrumbList"},
 {"@type":"Product","name":"Ardmore Legacy","sku":"AL-750","brand":{"@type":"Brand","name":"Ardmore"},
  "description":"Peat smoke, vanilla &amp; honey",
  "offers":{"@type":"Offer","price":"54.99","priceCurrency":"CAD","availability":"https://schema.org/InStock"}}]}
</script></head><body></body></html>"""

META_PAGE = """<html><head><meta property="og:title" content="Lagavulin 16">
<meta property="product:price:amount" content="139.99">
<meta property="product:availability" content="out of stock"></head></html>"""


def test_jsonld_product():
    l = parse_product_page("kwm", "https://k.com/products/ardmore-legacy?x=1", JSONLD_PAGE)
    assert l.price == 54.99 and l.in_stock is True and l.vendor == "Ardmore"
    assert l.product_id == "AL-750" and l.url == "https://k.com/products/ardmore-legacy"
    assert "vanilla & honey" in l.description


def test_meta_fallback():
    l = parse_product_page("kwm", "https://k.com/p/lag16", META_PAGE)
    assert l.title == "Lagavulin 16" and l.price == 139.99 and l.in_stock is False


def test_no_product_data():
    assert parse_product_page("kwm", "https://k.com/", "<html><body>hi</body></html>") is None


def test_tasting_notes_extracted_from_body():
    page = JSONLD_PAGE.replace("<body></body>", "<body><nav>Menu</nav><div>Tasting Notes: Nose: bonfire, leather. "
                               "Palate: earthy, cherries.</div></body>")
    l = parse_product_page("kwm", "https://k.com/x", page, want_notes=True)
    assert "leather" in l.description and "Menu" not in l.description
