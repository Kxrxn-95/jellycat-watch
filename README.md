# 🐰 Jellycat Watch

Get a push notification on your iPhone the moment a specific Jellycat item comes back in stock.

## How it works (the short version)

There are two pieces:

1. **A checker** — a tiny script that visits the Jellycat product pages you care about every ~15 minutes and works out whether they're in stock. It runs for free in the cloud on **GitHub Actions**, so it keeps checking even when your phone and computer are off.
2. **The notifications** — when something restocks, the checker sends an alert to **ntfy**, a free app you install on your iPhone and add to your home screen. That app is what buzzes you.

You don't need to know any code. Setup is about 15 minutes, mostly copying and pasting.

---

## Part 1 — Get the notification app on your iPhone

1. On your iPhone, install **ntfy** from the App Store (it's free, the icon is a green bell).
2. Open it, tap **+** to subscribe to a topic, and enter a **topic name**. A topic is just a secret word — anyone who knows it can send you alerts, so make it long and random. Use this one (or invent your own):

   ```
   jellycat-dragon-alerts-7Qx2m9
   ```

3. To make it feel like an app: in Safari you can also open `https://ntfy.sh/jellycat-dragon-alerts-7Qx2m9`, tap the **Share** button, then **Add to Home Screen**. (The app itself already lives on your home screen, so this step is optional.)

Keep that topic name handy — you'll paste it into GitHub in Part 3.

---

## Part 2 — Put the checker on GitHub

1. Create a free account at [github.com](https://github.com) if you don't have one.
2. Create a **new repository** — name it anything (e.g. `jellycat-watch`). It can be private.
3. Upload the files from this folder into the repo. The easiest way: on the new repo page click **uploading an existing file**, then drag in everything from the `jellycat-watch` folder. Make sure the folder structure is kept, especially `.github/workflows/check.yml`.

   Your repo should contain:
   ```
   check_stock.py
   config.json
   .github/workflows/check.yml
   docs/            (optional dashboard — see Part 5)
   ```

---

## Part 3 — Tell it your ntfy topic (kept secret)

1. In your repo go to **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `NTFY_TOPIC`
3. Value: your topic from Part 1 (e.g. `jellycat-dragon-alerts-7Qx2m9`)
4. Click **Add secret**.

Using a secret keeps your topic out of the public code.

---

## Part 4 — Choose which items to watch

`config.json` already comes set up with your eight dragons **and** automatic tracking of any future dragon. You only need to edit it if you want to add or remove items.

```json
{
  "ntfy_topic": "CHANGE_ME-jellycat-a8f3kd92",

  "auto_discover": {
    "enabled": true,
    "category_url": "https://jellycat.com/animals/dragons-dinosaurs",
    "slug_pattern": "^[a-z0-9]+-dragon$",
    "max_pages": 8
  },

  "products": [
    { "name": "Persimmon Dragon", "url": "https://jellycat.com/persimmon-dragon/" },
    { "name": "Heart Dragon",     "url": "https://jellycat.com/heart-dragon/" }
  ]
}
```

**Two ways items get watched:**

1. **`products`** — the explicit list. Each item is always checked, even if it's been delisted or sold out for ages. `name` is just the label shown in the notification; `url` is the real product page address.
2. **`auto_discover`** — this reads Jellycat's *Dragons & Dinosaurs* category every run and automatically adds any product whose web address looks like `something-dragon` (e.g. a brand-new `ember-dragon`). So when Jellycat releases a new dragon, it's picked up on the next check without you touching anything.

   The `slug_pattern` is what makes it dragon-only and sensible: it matches a single name followed by `-dragon`, which **includes** real dragon plushes (e.g. `sage-dragon`, `onyx-dragon`) and **excludes** bag charms, soothers, books and "Personalised … Huge" versions. To watch a *different* animal too, you could change the category URL and pattern — ask me and I'll set it up.

You can leave `ntfy_topic` as-is; the secret from Part 3 overrides it. Commit any changes you make.

### Turn it on

Go to the **Actions** tab, enable workflows if prompted, pick **Jellycat stock check**, and click **Run workflow** to test it now. Open the run and read the log — you'll see a line per item like:

```
[check] Bashful Beige Bunny (Medium): IN STOCK (was: None)
```

You'll also see a `[discover] found N matching products` line showing the dragons it auto-picked up from the category page. After this first run it checks itself automatically every ~15 minutes. You'll only get a notification when an item flips from out-of-stock to in-stock — not on every check.

> **Tip — test your phone alert:** pick an item that's currently *in stock*, run it once (it records the state), then delete `state.json` from the repo and run again. The "was" will reset and it'll fire a test notification.

---

## Part 5 — (Optional) Your own home-screen dashboard

The `docs/` folder is a little web page showing the current status of everything you track, which you can add to your home screen as its own app.

1. In the repo, go to **Settings → Pages**, set **Source: Deploy from a branch**, branch **main**, folder **/docs**, save. After a minute GitHub gives you a URL like `https://yourname.github.io/jellycat-watch/`.
2. Edit `docs/index.html` and set `STATE_URL` near the bottom to your repo's raw state file:
   ```
   https://raw.githubusercontent.com/YOURNAME/jellycat-watch/main/state.json
   ```
3. Open the Pages URL in Safari → **Share → Add to Home Screen**.

This page only *shows* status; the actual buzzing still comes from the ntfy app in Part 1.

---

## Good to know

- **How often:** every ~15 minutes. GitHub sometimes delays scheduled runs when busy, so treat it as "about every 15–20 min." You can make it more frequent by editing the `cron` line in `.github/workflows/check.yml`, but don't hammer the site.
- **Accuracy:** the checker reads the product page's stock status. It's reliable for normal in/out-of-stock, but if Jellycat changes their page wording the log will show `unknown` for that item — tell me and I'll adjust the script.
- **Cost:** free. GitHub Actions includes plenty of free minutes for a job this small, and ntfy.sh is free.
- **Privacy:** anyone who guesses your ntfy topic could send you notifications, so keep it random and private.
