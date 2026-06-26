# 🐉 Jellycat Watch

Get a push notification on your iPhone the moment a tracked Jellycat dragon comes back in stock — including any brand-new dragon Jellycat releases.

## How it works

Three pieces work together:

1. **A checker** — a small Python script (`check_stock.py`) that visits the Jellycat product pages you care about, plus the Dragons & Dinosaurs category, and works out what's in stock. It runs in the cloud on **GitHub Actions**, so it keeps going even when your phone and computer are off.
2. **A scheduler** — a free service (**cron-job.org**) that reliably triggers the checker every few minutes. (GitHub's own built-in timer is unreliable for frequent runs, so we drive it externally instead.)
3. **The notifications** — when something restocks, the checker sends a high-priority alert to **ntfy**, a free app on your iPhone that buzzes you.

No coding needed — it's all copying and pasting.

---

## Part 1 — The notification app (iPhone)

1. Install **ntfy** from the App Store — the official one is by **Philipp Heckel** (green bell icon).
2. **When it first asks to send you notifications, tap "Allow."** This step matters: if you skip the prompt and switch notifications on later in Settings, push delivery often won't register properly (see Troubleshooting).
3. Tap **+** to subscribe to a **topic**. A topic is a secret word — anyone who knows it can send you alerts, so keep it long and private. Yours is:
   ```
   jellycat-dragon-alerts-7Qx2m9
   ```
4. Optional: to add it as a home-screen icon, open `https://ntfy.sh/your-topic` in Safari → **Share → Add to Home Screen**.

Keep the topic name handy — it must match the GitHub secret in Part 3 exactly.

---

## Part 2 — Put the checker on GitHub

1. Create a free account at [github.com](https://github.com).
2. Create a **new repository** named `jellycat-watch`. **Make it Public** — public repos get unlimited free Actions minutes, and there are no secrets in the code (your topic lives in a separate GitHub secret, not the files).
3. Add these files to the repo:
   ```
   check_stock.py
   config.json
   .github/workflows/check.yml
   docs/            (optional dashboard — see Part 6)
   ```

   **Tip on uploading:** GitHub's web drag-and-drop can't create folders and Windows sometimes mangles filenames during a drag (e.g. `CHECK_~1.PY`). The reliable way is **Add file → Create new file**: type the full path in the name box — typing `.github/workflows/check.yml` auto-creates the folders — then paste the contents. Do the same for `check_stock.py` and `config.json`. If a filename ever comes out wrong, open it → pencil ✏️ → fix the name → commit.

---

## Part 3 — Tell it your ntfy topic (kept secret)

1. In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `NTFY_TOPIC`
3. Value: your exact topic from Part 1 — `jellycat-dragon-alerts-7Qx2m9`
4. **Add secret**.

The app subscription and this secret must be **character-for-character identical**, or no alerts arrive. (GitHub hides secret values once saved — you can overwrite but not read them back, so if in doubt, set both sides fresh to the same value.)

---

## Part 4 — Choose which items to watch

`config.json` comes set up with eight dragons **and** automatic tracking of any future dragon. You only need to edit it to add or remove items.

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

1. **`products`** — the explicit list. Each is always checked, even if delisted or sold out for ages. `name` is just the label shown in the notification; `url` is the real product page address.
2. **`auto_discover`** — reads Jellycat's *Dragons & Dinosaurs* category every run and automatically adds any product whose address looks like `something-dragon` (e.g. a new `ember-dragon`). New dragons get picked up on the next check with no edits from you. The `slug_pattern` keeps it sensible: it matches a single name + `-dragon`, so it **includes** real dragon plushes (`sage-dragon`, `onyx-dragon`) and **excludes** bag charms, soothers, books and "Personalised … Huge" versions.

Leave `ntfy_topic` as-is in this file; the GitHub secret from Part 3 overrides it. Commit any changes.

> **Note on the URL convention:** auto-discovery assumes a single-word name like `name-dragon`. A two-word name (e.g. `blue-moon-dragon`) wouldn't be caught automatically — just add it to `products` by hand.

---

## Part 5 — Make it run automatically (the scheduler)

GitHub's built-in schedule is unreliable for frequent runs (it often skips for long stretches), so we trigger the checker from **cron-job.org** instead — a free, dependable scheduler.

### 5a. Create a GitHub access token

1. GitHub **profile picture → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Settings:
   - **Name:** `jellycat cron trigger`
   - **Expiration:** as long as allowed (~1 year — note the date; you'll regenerate it then).
   - **Resource owner:** your account.
   - **Repository access:** **Only select repositories → `jellycat-watch`**.
   - **Permissions → Repository permissions → Actions → Read and write** (leave the rest default; "Metadata: read" turns on automatically).
3. **Generate**, then **copy the token immediately** (`github_pat_…`, shown once).

### 5b. Set up the cron-job.org job

1. Create a free account at **cron-job.org → Create cronjob**.
2. **Title:** `Jellycat checker`
3. **URL:**
   ```
   https://api.github.com/repos/Kxrxn-95/jellycat-watch/actions/workflows/check.yml/dispatches
   ```
   (Replace `Kxrxn-95` with your GitHub username if different.)
4. **Schedule:** every **3 minutes**. (Don't go below ~2 minutes — each run makes several requests to Jellycat, and hitting them too hard risks being blocked.)
5. Enable **Expert mode / Advanced** and set:
   - **Method:** `POST`
   - **Body:** `{"ref":"main"}`
   - **Headers:**
     ```
     Authorization: Bearer github_pat_YOUR_TOKEN_HERE
     Accept: application/vnd.github+json
     Content-Type: application/json
     User-Agent: jellycat-cron
     ```
     (The `User-Agent` line is required — GitHub's API rejects requests without one.)
6. **Save**, then use **Run now / Test run**. A successful trigger returns **HTTP 204 No Content**.

### 5c. Confirm it's running

Open the GitHub **Actions** tab. A new run appears within seconds of each trigger, and a fresh one should show up roughly every 3 minutes from now on — no manual input. A new run appears on **every** check (not only when something's in stock); a green tick just means "checked OK." The phone notification is the thing that only happens on an actual restock.

---

## Part 6 — (Optional) Home-screen status dashboard

The `docs/` folder is a small web page showing the current status of everything you track.

1. **Settings → Pages →** Source: **Deploy from a branch**, branch **main**, folder **/docs**, save. You'll get a URL like `https://yourname.github.io/jellycat-watch/`.
2. Edit `docs/index.html` and set `STATE_URL` to your raw state file:
   ```
   https://raw.githubusercontent.com/Kxrxn-95/jellycat-watch/main/state.json
   ```
3. Open the Pages URL in Safari → **Share → Add to Home Screen**.

This only *shows* status — the buzzing still comes from the ntfy app.

---

## Testing the whole chain

To prove GitHub → script → ntfy → your phone all work:

1. Add a known **in-stock** item to `config.json`, e.g.
   `{ "name": "TEST", "url": "https://jellycat.com/bashful-beige-bunny/" }`, and commit.
2. With the ntfy app **fully closed** (a banner won't show if the app is open in the foreground), trigger a run (cron-job.org will within 3 min, or hit **Run workflow**).
3. You should get a lock-screen notification, because the item is in stock and newly tracked.
4. Remove the TEST line afterwards and commit.

---

## Troubleshooting

- **Alerts show inside the ntfy app but no iOS banner.** iOS didn't register the app for push (usually because the first-launch "Allow" prompt was missed). **Delete and reinstall the ntfy app, tap "Allow" on the prompt, then re-subscribe to your topic.** This is the reliable fix. Also check **Settings → Notifications → ntfy** has Allow Notifications + Banners + Sounds on, and that the topic isn't muted in the app.
- **No alerts at all (not even in-app).** The app's topic and the `NTFY_TOPIC` secret don't match. Set both to the same exact value.
- **Notifications respect silent/Do-Not-Disturb.** Max priority gives a prominent banner + sound, but iOS won't override silent mode or a Focus unless you allow ntfy under **Settings → Focus**. (True override needs a "Critical Alerts" entitlement ntfy doesn't have.)
- **Runs stopped appearing.** Check cron-job.org's execution history — anything other than HTTP 204 usually means the **GitHub token has expired** (regenerate it and paste the new one into the cron-job.org header). Also note GitHub auto-disables a repo's *built-in* schedule after 60 days of no commits — but since cron-job.org drives this, that doesn't affect you.
- **An item shows `unknown` in the log.** Jellycat may have changed its page wording for that product — the detection rule needs a tweak.

---

## Good to know

- **How often:** every ~3 minutes via cron-job.org. Plenty fast for restocks without hammering Jellycat. Don't push below ~2 minutes.
- **Notifications fire only on the transition** out-of-stock → in-stock, so you won't get repeat spam. This is remembered in `state.json`, which the checker commits after each run (those auto-commits are normal).
- **Cost:** free. Public-repo GitHub Actions, cron-job.org, and ntfy.sh are all free.
- **Token upkeep:** the GitHub token expires (~1 year) — regenerate and update it in cron-job.org when it does.
- **Privacy:** anyone who guesses your ntfy topic could send you notifications, so keep it private. The repo being public only exposes harmless code and product URLs — never your topic (it's a secret) or token.
