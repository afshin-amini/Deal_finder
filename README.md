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

## Dram Ledger (the app)

A phone-friendly web app in `docs/`, hosted free on GitHub Pages:

- **Deals:** the latest alerts, new arrivals this week, and bottles that are good value today.
- **For you:** every in-stock bottle ranked by how well it fits your palate.
- **Search:** all in-stock whiskies across the shops.
- **Journal:** your tastings with scores out of 100. Add or edit them from your phone.

Tapping a bottle shows its price history, KWM tasting notes, why it was suggested, and an "I've tasted this" button.

Once the journal has **8 or more tastings**, each daily run learns your taste from it (`dealfinder/taste.py`):
- **Feature weights:** how far above or below your average you score each distillery, bottler, peat level, sherry level, cask, region, and strength and age band.
- **Flavour weights:** the same for the flavour words in your notes.

Every bottle then gets a "you'd likely score it ~88" prediction, and is matched to your closest favourite ("like your Tri Carragh Ardmore 2009 (91)"). These feed into the rankings, the alerts and the email. The more tastings you add, the more weight the profile gets compared with the general rules.

**Setup (once):**
1. Go to repo **Settings → Pages → Build and deployment → Source: GitHub Actions**.
2. Go to **Actions → publish app → Run workflow**. The app's address is then shown on the run page and under Settings → Pages: `https://<you>.github.io/Deal_finder/`.
3. On your phone, open that address, then use Share → **Add to Home Screen**.
4. To add tastings and update prices from the app, create a **fine-grained personal access token** for this repository only. Give it **Contents: Read and write** (to save tastings) and **Actions: Read and write** (for the update button). Paste it into the app's Journal → Settings. The token stays on that device only.

**Updating prices from the app:** tap ↻ in the top bar and choose Quick (BSW + Craft Cellars, ~5 min), KWM only (~15 min) or Everything (~25 min). This starts the same `daily whisky deals` workflow with `shops` and `quiet` inputs. Quiet means it only emails if something new turned up. The app shows that the update is running and reloads the data when it's published.

**Cellar tab:**
- **Watching:** price targets. Tap **Watch price** on any bottle, or accept the suggestions made from your journal's "would buy" notes. You choose the matching shop listings and the target. When a watched bottle is in stock at or under your target, the daily run sends a **Watchlist** alert (email and app), and the Deals tab lists it at the top. A watch without a target alerts on any price drop or restock.
- **My bottles:** what you own, with price paid, where, bought and opened dates, and how full it is. Owned bottles no longer show up in For you, new arrivals or deal alerts.
- **Linking tastings to shop bottles:** use **Link my tasting** on a bottle, or tick listings in the journal form. Linked bottles show "You scored 88" in every list. Matching suggests candidates and you confirm them:
  - an age or vintage must not conflict ("Kilkerran 16" never matches "Kilkerran 12")
  - an independent bottler in the listing must also be in your name
  - the distillery and expression words must match

All of this is stored in `docs/data/journal.json` next to your tastings (`watch`, `shelf`, and `links` on entries).

Data flow: the daily run commits `docs/data/*.json` and republishes the app. The journal is saved to `docs/data/journal.json`, which is a public file in a public repo.

## Daily email (GitHub Actions)

The workflow in `.github/workflows/daily.yml` sends one email per day. It covers price drops, new arrivals, bottles back in stock and deals, with KWM tasting notes included. If a shop has scrape problems, the email says so.

1. **Create a Gmail App Password.** Go to Google Account → Security and turn on 2-Step Verification if it's off. Then go to **App passwords**, create one called "whisky", and copy the 16-character code.
2. **Add repository secrets.** In GitHub, open the repo → Settings → Secrets and variables → Actions → **New repository secret**, and add:
   - `EMAIL_TO`: the address to send to
   - `SMTP_USER`: the Gmail address that sends the email (it can be the same one)
   - `SMTP_PASSWORD`: the App Password from step 1
   - `NTFY_TOPIC` (optional): also sends phone push alerts via the ntfy app
3. **Test it.** Go to the Actions tab → "daily whisky deals" → **Run workflow**. The first run records a price baseline and emails you a summary. After that, emails only contain changes.
4. **Turn on the daily schedule.** Uncomment the two `schedule:` lines at the top of `daily.yml`. The schedule only runs on the repo's default branch.

For a mail provider other than Gmail, also set `SMTP_HOST` and `SMTP_PORT`. Port 465 uses SSL; port 587 uses STARTTLS.

Price history is kept between runs in the Actions cache. If GitHub ever evicts it, the next run records a fresh baseline and you miss one day of alerts.

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
