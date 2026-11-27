#!/usr/bin/env python3
"""
Jellycat stock checker (UK store).

Reads products from config.json, fetches each product page, decides whether it
is in stock, and sends a push notification (via ntfy) the moment an item goes
from out-of-stock to in-stock.

Safeguards against false alarms:
  * Every reading is VERIFIED before it's trusted: the page must be the exact
    product page on the UK store (jellycat.com). Jellycat geo-routes visitors
    between its regional stores, and GitHub's servers are mostly outside the UK,
    so the checker is sometimes handed another store's page with different
    stock. Unverified readings change nothing and never alert.
  * Barrage guard: if lots of items flip to "in stock" in a single run, they're
    held for one more run and only alerted once confirmed.
  * Health alert: if no page can be verified for a long stretch, you get ONE
    low-priority "needs attention" notification instead of silence.

State is kept in state.json so you only get notified once per restock.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"

UK_HOSTS = {"jellycat.com", "www.jellycat.com"}

# Jellycat UK's BigCommerce store ID. It appears in the UK store's product image
# URLs; the US/EU stores have different IDs. Override with "store_fingerprint"
# in config.json if Jellycat ever changes it.
DEFAULT_STORE_FINGERPRINT = "s-e66ltxu1n1"

# More than this many items flipping to "in stock" in one run = hold them for
# one confirming run before alerting. Override with "barrage_threshold".
DEFAULT_BARRAGE_THRESHOLD = 3

HEALTH_ALERT_AFTER_RUNS = 10      # ~30 min of nothing verifiable at 3-min runs
STALE_STATE_DAYS = 14             # forget untracked items not seen for this long
DISCOVERY_EVERY_MINUTES = 30      # re-scan the category for new dragons this often
                                  # (Jellycat rate-limits busy visitors, so we
                                  # don't re-scan it on every 3-minute run)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-GB,en;q=0.9",
    # Ask for the UK store (best effort - the verification below is the real
    # safeguard if Jellycat sends a different store anyway).
    "Cookie": "jellycatStoreCode=uk; geotargetly_redirect_1715283202614_bypass_cookie=true",
}

IN_STOCK = {"instock", "limitedavailability", "onlineonly"}
NOT_IN_STOCK = {
    "outofstock", "soldout", "discontinued", "preorder", "presale",
    "backorder", "instoreonly",
}

LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)
CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}


# --------------------------------------------------------------------------- #
# Fetching and reading pages
# --------------------------------------------------------------------------- #

def fetch(url: str):
    """Download a page. Returns (final_url_after_redirects, html)."""
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.geturl(), resp.read().decode(charset, errors="replace")


def split_url(url):
    """('jellycat.com', '/heart-dragon') - lower-case host and path, no trailing slash."""
    parts = urlparse(url or "")
    return (parts.hostname or "").lower(), parts.path.rstrip("/").lower()


def iter_ld_objects(html: str):
    """Yield every JSON object found in the page's JSON-LD structured data."""
    for block in LD_JSON_RE.findall(html):
        try:
            data = json.loads(block.strip(), strict=False)
        except ValueError:
            continue
        stack = [data]
        while stack:
            obj = stack.pop()
            if isinstance(obj, list):
                stack.extend(obj)
            elif isinstance(obj, dict):
                yield obj
                if "@graph" in obj:
                    stack.append(obj["@graph"])


def is_product(obj: dict) -> bool:
    kind = obj.get("@type")
    return "Product" in (kind if isinstance(kind, list) else [kind])


def format_price(offer: dict):
    price = offer.get("price")
    currency = (offer.get("priceCurrency") or "").upper()
    if not price:
        return None
    symbol = CURRENCY_SYMBOLS.get(currency)
    return f"{symbol}{price}" if symbol else f"{price} {currency}".strip()


def read_stock(requested_url: str, final_url: str, html: str, fingerprint: str):
    """
    Work out the stock status of one product page.

    Returns (verdict, detail, price) where verdict is:
      "in"         - verified UK product page, marked in stock
      "out"        - verified UK product page, not buyable (incl. discontinued)
      "unverified" - not provably the right UK product page; ignore this reading
    """
    req_host, req_path = split_url(requested_url)
    fin_host, fin_path = split_url(final_url)

    # 1. We must have landed on the same page on the UK store.
    if fin_host not in UK_HOSTS or fin_path != req_path:
        return "unverified", f"redirected to {final_url}", None

    # 2. The page must describe THIS product in its structured data.
    products = [o for o in iter_ld_objects(html) if is_product(o)]
    if not products:
        return "unverified", "no product data on page (removed or changed?)", None
    matching = [p for p in products if split_url(p.get("url"))[1] == req_path]
    if not matching and len(products) == 1 and not products[0].get("url"):
        matching = products
    if not matching:
        return "unverified", "product data is for a different page", None
    product = matching[0]

    product_host = split_url(product.get("url"))[0]
    if product_host and product_host not in UK_HOSTS:
        return "unverified", f"product belongs to another store ({product_host})", None

    # 3. It must come from the UK store (its images live under the UK store ID).
    image = json.dumps(product.get("image") or "")
    evidence = image if image not in ('""', "null") else html
    if fingerprint and fingerprint not in evidence:
        return "unverified", "page is not from the Jellycat UK store", None

    # 4. Read the offer(s).
    raw = product.get("offers")
    raw = raw if isinstance(raw, list) else ([raw] if raw else [])
    offers = []
    for offer in raw:
        if isinstance(offer, dict) and isinstance(offer.get("offers"), list):
            offers.extend(o for o in offer["offers"] if isinstance(o, dict))
        elif isinstance(offer, dict):
            offers.append(offer)
    if not offers:
        return "unverified", "no availability data", None

    names = [str(o.get("availability", "")).rstrip("/").split("/")[-1] for o in offers]
    states = [n.lower() for n in names]
    price = next((format_price(o) for o in offers if format_price(o)), None)
    shown = "/".join(sorted(set(n for n in names if n))) or "none"

    if any(s in IN_STOCK for s in states):
        return "in", shown, price
    if states and all(s in NOT_IN_STOCK for s in states):
        return "out", shown, price
    return "unverified", f"unrecognised availability ({shown})", price


# --------------------------------------------------------------------------- #
# Auto-discovery of new products
# --------------------------------------------------------------------------- #

def discover_products(disc: dict) -> list:
    """
    Auto-find product URLs from a Jellycat category listing page.

    Walks the paginated category page (e.g. Dragons & Dinosaurs), pulls out
    every product link, and keeps only those whose slug matches slug_pattern
    (default: a single word followed by "-dragon", e.g. "persimmon-dragon").
    Only UK-store category pages are used.
    """
    base = disc.get("category_url")
    if not base:
        return []
    pattern = re.compile(disc.get("slug_pattern", r"^[a-z0-9]+-dragon$"))
    max_pages = int(disc.get("max_pages", 8))
    link_re = re.compile(
        r'href=["\'](?:https?://(?:www\.)?jellycat\.com)?/([a-z0-9-]+)/?["\']', re.I
    )

    found = {}  # url -> name
    for page in range(1, max_pages + 1):
        page_url = f"{base}?page={page}"
        try:
            final_url, html = fetch(page_url)
        except Exception as exc:
            print(f"[discover] page {page} fetch failed ({exc})")
            break
        if split_url(final_url)[0] not in UK_HOSTS:
            print(f"[discover] skipped: sent to {final_url} instead of the UK store")
            break

        for slug in link_re.findall(html):
            slug = slug.lower()
            if pattern.match(slug):
                url = f"https://jellycat.com/{slug}/"
                found.setdefault(url, slug.replace("-", " ").title())

        if f"page={page + 1}" not in html:
            break

    products = [{"name": name, "url": url} for url, name in sorted(found.items())]
    print(f"[discover] found {len(products)} matching products from category page")
    return products


def merge_products(explicit: list, discovered: list) -> list:
    """Combine explicit + discovered products, de-duplicated by URL.
    Explicit entries win on naming."""
    def norm(u):
        return u.rstrip("/").lower()

    by_url = {}
    for p in discovered:
        if p.get("url"):
            by_url[norm(p["url"])] = p
    for p in explicit:
        if p.get("url"):
            by_url[norm(p["url"])] = p
    return list(by_url.values())


# --------------------------------------------------------------------------- #
# Notifications and state
# --------------------------------------------------------------------------- #

def notify(topic: str, title: str, message: str, url: str, priority: str = "max",
           tags: str = "tada,shopping_bags") -> None:
    """Send a push notification through ntfy.sh."""
    req = Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Tags": tags,
            "Priority": priority,
            "Click": url,
            "Actions": f"view, Open page, {url}",
        },
    )
    with urlopen(req, timeout=30) as resp:
        resp.read()


def send(topic, title, message, url, **kwargs) -> None:
    try:
        notify(topic, title, message, url, **kwargs)
    except Exception as exc:
        print(f"  !! notification failed: {exc}")


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return default
    return default


# --------------------------------------------------------------------------- #
# Main run
# --------------------------------------------------------------------------- #

def main() -> int:
    config = load_json(CONFIG_PATH, None)
    if not config:
        print("ERROR: config.json missing or invalid.", file=sys.stderr)
        return 1

    topic = os.environ.get("NTFY_TOPIC") or config.get("ntfy_topic", "")
    if not topic or topic.startswith("CHANGE_ME"):
        print("ERROR: no ntfy topic set (env NTFY_TOPIC or config.json).", file=sys.stderr)
        return 1

    fingerprint = config.get("store_fingerprint", DEFAULT_STORE_FINGERPRINT)
    threshold = int(config.get("barrage_threshold", DEFAULT_BARRAGE_THRESHOLD))

    state = load_json(STATE_PATH, {})
    meta = state.pop("_meta", {}) if isinstance(state.get("_meta"), dict) else {}
    now = int(time.time())

    explicit = config.get("products", [])
    discovered = []
    disc = config.get("auto_discover", {})
    if disc.get("enabled"):
        cached = meta.get("discovered") or []
        age = now - int(meta.get("discovered_at", 0))
        if cached and age < DISCOVERY_EVERY_MINUTES * 60:
            discovered = cached
            print(f"[discover] using saved list of {len(cached)} products "
                  f"(re-scan in ~{max(1, (DISCOVERY_EVERY_MINUTES * 60 - age) // 60)} min)")
        else:
            try:
                fresh = discover_products(disc)
            except Exception as exc:  # never let discovery break the core checks
                print(f"[discover] skipped due to error: {exc}")
                fresh = []
            if fresh:
                meta["discovered"], meta["discovered_at"] = fresh, now
                discovered = fresh
            else:
                discovered = cached
                print(f"[discover] nothing usable this time - keeping the saved list "
                      f"of {len(cached)} products")

    products = merge_products(explicit, discovered)
    if not products:
        print("ERROR: no products to check.", file=sys.stderr)
        return 1
    print(f"Checking {len(products)} item(s) on the UK store.\n")

    # ---- 1. Read every page --------------------------------------------- #
    results = []
    for product in products:
        url = product.get("url")
        if not url:
            continue
        name = product.get("name", url)
        try:
            final_url, html = fetch(url)
            verdict, detail, price = read_stock(url, final_url, html, fingerprint)
        except HTTPError as exc:
            verdict, detail, price = "unverified", f"HTTP {exc.code} (page missing or blocked)", None
        except Exception as exc:
            verdict, detail, price = "unverified", f"fetch failed ({type(exc).__name__}: {exc})", None

        entry = state.get(url, {})
        prev = entry.get("in_stock")
        results.append({
            "url": url, "name": name, "verdict": verdict, "detail": detail,
            "price": price, "prev": prev, "pending": bool(entry.get("pending")),
        })

        if verdict == "unverified":
            print(f"[skip]  {name}: UNVERIFIED - {detail} (no change, no alert)")
        else:
            status = "IN STOCK" if verdict == "in" else "out of stock"
            extra = f" · {price}" if price else ""
            print(f"[check] {name}: {status} (was: {prev})  [UK store · {detail}{extra}]")

        time.sleep(2)  # be polite to the site

    # ---- 2. Decide who to alert (barrage guard) ------------------------- #
    flips = [r for r in results if r["verdict"] == "in" and r["prev"] is not True]
    confirmed = [r for r in flips if r["pending"]]
    fresh = [r for r in flips if not r["pending"]]
    held = fresh if len(fresh) > threshold else []
    to_alert = confirmed + ([] if held else fresh)

    if held:
        print(f"\n!! {len(held)} items flipped to in stock in one run - unusual, so holding "
              f"them for one more check before alerting: {', '.join(r['name'] for r in held)}")

    # ---- 3. Update state -------------------------------------------------- #
    held_urls = {r["url"] for r in held}
    for r in results:
        entry = dict(state.get(r["url"], {}))
        entry["name"] = r["name"]
        if r["verdict"] == "unverified":
            entry["last_unverified"] = now
            entry["note"] = r["detail"]
        elif r["url"] in held_urls:
            entry["pending"] = True
            entry["checked"] = now
        else:
            entry["in_stock"] = r["verdict"] == "in"
            entry["pending"] = False
            entry["checked"] = now
            entry.pop("note", None)
        state[r["url"]] = entry

    # ---- 4. Send restock alerts ------------------------------------------ #
    for r in to_alert:
        print(f"  -> RESTOCK! Sending notification for {r['name']}")
        price = f" ({r['price']})" if r["price"] else ""
        send(topic, f"{r['name']} is back in stock!",
             f"{r['name']} is available now on Jellycat UK{price}. Tap to buy.", r["url"])

    # ---- 5. Health check: one alert if nothing can be verified for a while #
    verified = sum(1 for r in results if r["verdict"] != "unverified")
    if results and verified == 0:
        meta["unverified_runs"] = int(meta.get("unverified_runs", 0)) + 1
    else:
        meta["unverified_runs"] = 0
        meta["health_alert_sent"] = False
    if meta["unverified_runs"] >= HEALTH_ALERT_AFTER_RUNS and not meta.get("health_alert_sent"):
        print("!! No product page could be verified for a while - sending a health alert")
        send(topic, "Jellycat checker needs attention",
             "The checker hasn't been able to read any Jellycat UK product pages for a "
             "while, so restocks may be missed. Check the latest run log on GitHub.",
             "https://jellycat.com/", priority="default", tags="warning")
        meta["health_alert_sent"] = True

    # ---- 6. Tidy up and save --------------------------------------------- #
    tracked = {r["url"] for r in results}
    for url in list(state):
        last = state[url].get("checked", 0) if isinstance(state[url], dict) else 0
        if url not in tracked and now - last > STALE_STATE_DAYS * 86400:
            del state[url]

    print(f"\nSummary: {len(results)} checked, {verified} verified, "
          f"{len(results) - verified} unverified, {len(to_alert)} alert(s) sent, "
          f"{len(held)} held for confirmation.")

    state["_meta"] = meta
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
