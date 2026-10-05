# Whisky deal finder

Checks four Calgary shops each day, records every whisky price in SQLite, scores each bottle against two palate tracks, and sends ntfy.sh push alerts for bottles worth looking at.

Shops: BSW, Kensington Wine Market (KWM), The Crown Cellars, Craft Cellars.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 1. See how each shop serves its catalog (Shopify / WooCommerce / plain HTML)
python -m dealfinder probe

# 2. Try the scorer on any bottle name
python -m dealfinder score "G&M Connoisseurs Choice Ardmore 2009 Refill Sherry Hogshead 57.3%"

# 3. First real run (records a baseline; alerts start from the second run)
python -m dealfinder run --dry-run
```

**ntfy:** install the ntfy app and subscribe to a hard-to-guess topic, such as `whisky-deals-7f3k2q`. Then either `export NTFY_TOPIC=whisky-deals-7f3k2q` or set `topic` in `config.toml`. Anyone who knows a public ntfy.sh topic name can read it, so pick a topic name nobody would guess.

## Running daily

- **GitHub Actions:** `.github/workflows/daily.yml` runs every morning. Add the repo secret `NTFY_TOPIC`, plus `NTFY_TOKEN` if you use a protected topic. Price history is kept in the Actions cache. If the cache is ever evicted, the next run records a fresh baseline and you miss one day of alerts. Some shops block cloud/datacenter IPs. If `probe` works from your laptop but the Action reports "scrape problems", run it locally instead.
- **Local cron:** `17 8 * * * cd ~/deal_finder && .venv/bin/python -m dealfinder run >> data/run.log 2>&1`

## How each shop is read

`platform = "auto"` tries these in order:

1. **Shopify** `/products.json`: the full catalog as JSON, 250 products per request. Set `collections = ["scotch"]` to scan only whisky collections.
2. **WooCommerce Store API** `/wp-json/wc/store/v1/products`.
3. **HTML fallback:** finds product URLs from `listing_urls` (category pages, with a `{page}` placeholder) or from `sitemap.xml`, filtered to whisky-looking slugs. It then reads the schema.org JSON-LD or `og:price` meta tags on each product page. `has_tasting_notes = true` (set for KWM) also pulls the "Tasting notes / Nose / Palate" text from the page body.

After `probe`, pin each shop's `platform` in `config.toml`. If a shop comes back with "no products found", open one of its whisky category pages and add it as a `listing_urls` entry. For example: `listing_urls = ["/collections/scotch?page={page}"]`.

**Being polite:** robots.txt is fetched and obeyed for every URL, including any `Crawl-delay`. There are at least 4 seconds between requests to the same host. A 429 or 5xx response backs off and slows that host for the rest of the run. The User-Agent identifies the bot and links to this repo.

## Scoring

Each bottle gets two scores from 0 to 100 (`dealfinder/scoring.py`). Every point comes with a reason, and the reasons appear in the alerts.

| Track | Rewards |
|---|---|
| **peat_earth** | Peated distilleries and expressions (Ardmore, Ledaig, Caol Ila, Longrow...), earthy house styles, peat + sherry (leathery), age 15+, cask strength, smoke/leather/earth tasting words |
| **fruit_earth** | Unpeated whisky that is either (a) bourbon/refill cask from fruity distilleries (Clynelish, Linkwood, Glen Elgin...), or (b) **lightly** sherried (refill sherry, sherry hogshead, finishes), especially from earthy/waxy distilleries (Benrinnes, Mortlach IBs, Craigellachie, Ben Nevis). Heavy sherry (PX, first-fill oloroso, Macallan/Glendronach house style) scores low on purpose. |

Only KWM writes tasting notes, so the scoring mostly relies on what can be read from a title: distillery (house peat level and style for about 110 distilleries, in `parser.py`), bottler, cask type, age and ABV. When a shop does provide notes, tasting words count double.

**Watchlist** (`[watchlist]` in config): Ardmore, Gordon & MacPhail / G&M, Decadent Drinks / Decadent Dreams, Whisky Sponge, SMOS, Thompson Bros, Berry Bros. A watchlist match adds up to 15 points and lowers the bar for "new bottle" alerts.

**Deal score:** combines the drop since the last run, the price against this bottle's 120-day median, the shop's own "compare at" sale price, and a small bonus when the price is below a rough typical price for the bottle's age.

**Alerts:** a new bottle that fits your palate, a price drop, a bottle back in stock, or a sale or below-usual price. The same alert isn't repeated for 14 days unless the price drops further. Thresholds are in `[alerts]`.

## Other commands

```bash
python -m dealfinder top --track peat_earth -n 30   # best matches currently in stock
python -m dealfinder history "ardmore"              # daily price history
python -m dealfinder run --shop kwm --dry-run       # one shop, print instead of send
python -m pytest -q
```
