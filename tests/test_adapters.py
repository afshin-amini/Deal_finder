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


KWM_CARD = """<div class="product-content"><h6>{name}</h6><div class="product-price">${price}</div></div>
<a href="/product/{sku}/{slug}" class="product-link"></a></div></div>
<!-- Item --><div class="modal fade" id="product-{pk}" tabindex="-1" role="dialog">
<h3>{name}</h3><p class="h4">${price}</p><div class="product-meta"><span class="h6 mr-2">Region:</span>Scotland &gt; Speyside<br><span class="h6 mr-2">Vintage:</span>2005</div>
<p class="d-none d-lg-block"><p>Matured 18 years in a First Fill Sherry Hogshead before bottling at 58.5%.</p></p>
<!-- Add to Cart --><form>{cart}</form></div>
"""


def test_kwm_listing_page():
    from dealfinder.adapters.kwm import parse_listing_page

    page = "<html>" + KWM_CARD.format(
        name="Benromach 2005 KWM Cask 335", price="284.99", sku="118530", slug="benromach-2005-kwm-cask-335",
        pk="1", cart='<a class="addtocart" data-id="118530" data-price="284.99">Add</a>',
    ) + KWM_CARD.format(
        name="G&amp;M Glentauchers 25ml Sample", price="10.99", sku="35750", slug="gm-sample", pk="2", cart="",
    ) + "</html>"
    a, b = parse_listing_page("kwm", "https://k.com", page)
    assert (a.product_id, a.price, a.in_stock) == ("118530", 284.99, True)
    assert a.url == "https://k.com/product/118530/benromach-2005-kwm-cask-335"
    assert a.product_type == "Scotland > Speyside" and a.tags == ["vintage 2005"]
    assert "58.5%" in a.description
    assert (b.product_id, b.price, b.in_stock, b.title) == ("35750", 10.99, False, "G&M Glentauchers 25ml Sample")


def test_kwm_notes():
    from dealfinder.adapters.kwm import parse_notes

    page = ("<html><nav>Products Wine Scotch</nav><div>Tasting Notes Distillery Andrew's Tasting Note "
            "Nose: soft new leather, tobacco. Palate: earthy peat. Finish: long.</div>"
            "<div>Adapted from the article written by Andrew</div><footer>Follow Us</footer></html>")
    n = parse_notes(page)
    assert n.startswith("Andrew's Tasting Note Nose: soft new leather") and "Adapted" not in n


def test_kwm_notes_dedup_and_ellipsis():
    from dealfinder.adapters.kwm import parse_notes

    note = ("Producer Tasting Note Nose: peat. Palate: waxy. Finish: brine. "
            "Comment: channelling Campbeltown... vibes of Longrow 18... superb stuff! ")
    page = f"<html><div>{note}</div><div>{note}</div><p>Originally written by Evan for a blog post.</p></html>"
    n = parse_notes(page)
    assert n.count("Nose:") == 1 and n.endswith("superb stuff!")


def test_kwm_comment_stops_at_next_note():
    from dealfinder.adapters.kwm import parse_notes

    page = ("<div>Andrew's Tasting Note Nose: mango. Palate: creamy. Finish: fruity. Comment: a dangerously drinkable "
            "malt. Producer Tasting Note \"Sweet lemon aromas combine with sherbet.\"</div>")
    assert "Producer" not in parse_notes(page)


def test_kwm_search_paginates():
    from dealfinder.adapters.kwm import KwmAdapter

    def card(sku):
        return KWM_CARD.format(name=f"Ardmore {sku}", price="99.99", sku=sku, slug=f"ardmore-{sku}", pk=sku,
                               cart=f'<a class="addtocart" data-id="{sku}" data-price="99.99">Add</a>')

    pages = {1: card("1") + card("2") + 'href="/products?gsearch=ardmore&page=2#results"', 2: card("3"), 3: ""}
    calls = []

    class Http:
        def get(self, url, params=None, **kw):
            calls.append(params["page"])

            class R:
                status_code = 200
                text = pages[params["page"]]
            return R()

    found = list(KwmAdapter().search(Http(), {"name": "kwm", "base_url": "https://k.com"}, "ardmore"))
    assert [l.product_id for l in found] == ["1", "2", "3"] and calls == [1, 2]
