#!/usr/bin/env python3
"""
Jellycat stock checker.

Reads products from config.json, fetches each product page, decides whether it
is in stock, and sends a push notification (via ntfy) the moment an item goes
from out-of-stock to in-stock.

State is kept in state.json so you only get notified once per restock, not on
every run.
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"

# A normal browser User-Agent so the site serves us the real page.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

# Stock is read from the product's structured-data availability tag (see
# detect_in_stock), not from scanning page text — words like "Add to Bag",
# "Out of Stock" and "Retired" also appear in the site navigation and the
# "you might also like" carousels, which would otherwise give false readings.


def fetch(url: str) -> str:
    """Download the raw HTML of a page."""
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def detect_in_stock(html: str):
    """
    Decide whether a product is buyable, using the page's structured-data
    availability tag (schema.org JSON-LD, or the Open Graph fallback). This
    is the product's *own* status, so it isn't fooled by nav links or the
    cross-sell carousels elsewhere on the page.

      availability "InStock"      -> in stock
      availability "OutOfStock"   -> out of stock
      missing (e.g. retired item) -> out of stock

    Returns (in_stock: bool, info: str). `info` is logged for transparency.
    """
    lower = html.lower()

    schema = None
    m = re.search(
        r'availability"\s*:\s*"[^"]*?(instock|outofstock|soldout|preorder)', lower
    )
    if m:
        schema = m.group(1)
    else:
        m = re.search(r'product:availability["\'][^>]*content=["\']([^"\']+)', lower)
        if m:
            val = m.group(1).strip()
            if "outofstock" in val or "out of stock" in val:
                schema = "outofstock"
            elif "instock" in val or val in ("in stock", "available"):
                schema = "instock"

    in_stock = (schema == "instock")
    info = f"schema={schema}"
    return in_stock, info


def discover_products(disc: dict) -> list:
    """
    Auto-find product URLs from a Jellycat category listing page.

    Walks the paginated category page (e.g. Dragons & Dinosaurs), pulls out
    every product link, and keeps only those whose slug matches slug_pattern
    (default: a single word followed by "-dragon", e.g. "persimmon-dragon").
    This deliberately skips bag charms, soothers, books and "Personalised …
    Huge" variants, while picking up any brand-new dragon automatically.
    """
    base = disc.get("category_url")
    if not base:
        return []
    pattern = re.compile(disc.get("slug_pattern", r"^[a-z0-9]+-dragon$"))
    max_pages = int(disc.get("max_pages", 8))
    link_re = re.compile(
        r'href=["\'](?:https?://jellycat\.com)?/([a-z0-9-]+)/?["\']', re.I
    )

    found = {}  # url -> name
    for page in range(1, max_pages + 1):
        page_url = f"{base}?page={page}"
        try:
            html = fetch(page_url)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"[discover] page {page} fetch failed ({exc})")
            break

        for slug in link_re.findall(html):
            slug = slug.lower()
            if pattern.match(slug):
                url = f"https://jellycat.com/{slug}/"
                name = slug.replace("-", " ").title()
                found.setdefault(url, name)

        # Stop once there's no link to the next page.
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
    for p in explicit:               # explicit overrides discovered name
        if p.get("url"):
            by_url[norm(p["url"])] = p
    return list(by_url.values())


def notify(topic: str, title: str, message: str, url: str) -> None:
    """Send a push notification through ntfy.sh."""
    data = message.encode("utf-8")
    req = Request(
        f"https://ntfy.sh/{topic}",
        data=data,
        headers={
            "Title": title,
            "Tags": "tada,shopping_bags",
            "Priority": "max",
            "Click": url,            # tapping the notification opens the product
            "Actions": f"view, Open product, {url}",
        },
    )
    with urlopen(req, timeout=30) as resp:
        resp.read()


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def main() -> int:
    config = load_json(CONFIG_PATH, None)
    if not config:
        print("ERROR: config.json missing or invalid.", file=sys.stderr)
        return 1

    # Topic can come from an environment variable (recommended for GitHub) or
    # from config.json.
    topic = os.environ.get("NTFY_TOPIC") or config.get("ntfy_topic", "")
    if not topic or topic.startswith("CHANGE_ME"):
        print("ERROR: no ntfy topic set (env NTFY_TOPIC or config.json).", file=sys.stderr)
        return 1

    explicit = config.get("products", [])
    discovered = []
    disc = config.get("auto_discover", {})
    if disc.get("enabled"):
        try:
            discovered = discover_products(disc)
        except Exception as exc:  # never let discovery break the core checks
            print(f"[discover] skipped due to error: {exc}")

    products = merge_products(explicit, discovered)
    if not products:
        print("ERROR: no products to check (config.json empty and nothing discovered).", file=sys.stderr)
        return 1
    print(f"Checking {len(products)} item(s) total.\n")

    state = load_json(STATE_PATH, {})
    changed = False

    for product in products:
        name = product.get("name", product.get("url", "Unknown item"))
        url = product.get("url")
        if not url:
            continue

        try:
            html = fetch(url)
        except (HTTPError, URLError, TimeoutError) as exc:
            print(f"[skip] {name}: fetch failed ({exc})")
            continue

        in_stock, info = detect_in_stock(html)
        prev = state.get(url, {}).get("in_stock")

        status = "IN STOCK" if in_stock else "out of stock"
        print(f"[check] {name}: {status} (was: {prev})  [{info}]")

        # Notify only on the transition out-of-stock -> in-stock.
        if in_stock is True and prev is not True:
            print(f"  -> RESTOCK! Sending notification for {name}")
            try:
                notify(
                    topic,
                    title=f"{name} is back in stock!",
                    message=f"{name} is available now on Jellycat. Tap to buy.",
                    url=url,
                )
            except (HTTPError, URLError, TimeoutError) as exc:
                print(f"  !! notification failed: {exc}")

        state[url] = {"in_stock": in_stock, "name": name, "checked": int(time.time())}
        changed = True

        time.sleep(2)  # be polite to the site

    if changed:
        STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
