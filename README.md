# Whisky deal finder

Checks four Calgary shops each day, records every whisky price in SQLite, scores each bottle against two palate tracks, and sends ntfy.sh push alerts for bottles worth looking at.

Shops: BSW, Kensington Wine Market (KWM), The Crown Cellars, Craft Cellars.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 1. Check each shop is reachable and see sample products
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

These were checked against the live sites in October 2026.

| Shop | How it's read | Data you get | Pace |
|---|---|---|---|
| **BSW** (bswliquor.com) | Shopify `/collections/{scotch-whisky,scotch-deals,whisky-deals}/products.json` | Title, price, stock. No descriptions. | 8 s between requests; faster requests trigger a "Verifying your connection" check |
| **KWM** | Custom site. `/products/scotch?page=N` with a session set to 100 bottles per page and in-stock only, which covers everything in about 10 pages. | Price, region, vintage, description. **Tasting notes** (Andrew's and Evan's Nose/Palate/Finish) come from each product page, fetched once per bottle. | 20 s, per KWM's robots.txt `Crawl-delay` |
| **Craft Cellars** | Shopify `/collections/{scotch,daily-deal-whisky,new-arrivals-whisky}/products.json` | Title, price, stock, tags (such as "New Arrival"), description | 5 s |
| **Crown Cellars** | **Disabled.** It sits behind a Cloudflare browser challenge that blocks anything that isn't a real browser. | | |

KWM tasting notes: when a new bottle appears, its notes are fetched right away so the alert can include them. Bottles already listed are backfilled 30 per day, best palate matches first, until all of them have notes.

A bottle that drops off a shop's list is marked out of stock, so a "back in stock" alert fires when it returns. BSW shows an inflated "was" price on nearly every product, so its compare-at prices are ignored (`trust_compare_at = false`) and BSW deals come from the price history.

**Being polite:** robots.txt is fetched and obeyed for every URL, including any `Crawl-delay`. Each shop has its own minimum delay. A 429 or 5xx response backs off and slows that host for the rest of the run. If a shop answers with a bot-check page (Cloudflare "Just a moment...", Shopify "Verifying your connection"), the run stops for that shop and reports it. It does **not** try to get past the check. The User-Agent identifies the bot and links to this repo.

### Crown Cellars

Crown has chosen to put a browser challenge in front of its site, so scraping it would mean working around that choice. Options:
- Ask them. Small shops are often happy to share a product feed or to allowlist a personal price tracker.
- Check whether they're on Shopify. If you open `https://thecrowncellars.com/products.json` in your phone's browser and see JSON, the code only needs `enabled = true` once they allow it.

The generic adapters (`platform = "auto"`, `listing_urls`, sitemap + schema.org) remain available for adding other shops.

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
