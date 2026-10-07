# Prism

A news app for your phone's home screen. It collects India and world headlines from leading outlets, groups coverage of the same event together, and shows how left, centre and right outlets each reported it. Every story has a short summary and an image.

## Put it on your phone

You need a free GitHub account. Do the steps once.

1. On GitHub, create a new repository named `prism`. Make it **public** (GitHub Pages is free for public repositories).
2. Upload everything in this folder to the repository, keeping the folder structure. The branch must be called `main`. The hidden `.github` folder must be included, because it holds the update schedule.
3. In the repository, open **Settings, then Pages**, and under **Source** choose **GitHub Actions**.
4. Open the **Actions** tab, choose **Update news** on the left, and select **Run workflow**. It takes about two minutes.
5. Your app is now live at `https://YOUR-USERNAME.github.io/prism/`.

Add it to your home screen:

- **iPhone (Safari):** open the address, tap Share, then **Add to Home Screen**.
- **Android (Chrome):** open the address, tap the menu, then **Install app** or **Add to Home screen**.

From then on it opens like a normal app, and it still shows the last saved news when you are offline.

## How fresh is it

A scheduled job on GitHub fetches every outlet about every 30 minutes and publishes the result. GitHub sometimes starts scheduled jobs 5 to 20 minutes late, so think of it as near-live, not a live ticker. The app checks for a new update every 5 minutes while open, and you can pull down or tap the refresh icon to check right away.

GitHub switches off scheduled jobs in public repositories after 60 days without any activity in the repository. If the app starts saying "Updates may be paused", open the Actions tab and re-enable the workflow, or push any small change.

## Choose your outlets

Open `sources.json`. Each outlet has:

- `lean`: one of `left`, `centre-left`, `centre`, `centre-right`, `right`
- `scope`: `india` or `world`
- `feed`: the address of its RSS feed
- `enabled`: set to `false` to switch an outlet off
- `headline_only`: for feeds that carry headlines but no summary

The lean labels are approximate and based on public media-bias ratings. Judging Indian outlets in particular is contested, so change any label you disagree with. The app groups `left` and `centre-left` as Left, and `centre-right` and `right` as Right.

## What to expect

- **Summaries** are the opening lines of each outlet's own feed text, trimmed to about two sentences. They are not written by AI. A few outlets only share a teaser, so those summaries are short.
- **Agencies:** PTI, ANI, Reuters and AP do not publish open feeds, so they come in through Google News as headlines only. They count toward a story's coverage and link to the original.
- **Grouping** works by matching the words in headlines and summaries. It is good at big stories that many outlets cover and can occasionally miss a match or join two related stories. Each story page lists every outlet's own headline, so you can always see what was grouped.
- **Blindspots** are stories covered by at least three outlets where one side has none of the outlets you follow.
- **Feeds change.** Outlets move or retire their feeds from time to time. After the first run, open the run in the Actions tab and read the log: each outlet that failed is listed with a reason. Fix its `feed` address in `sources.json` or set `enabled` to `false`. The footer of the app also shows how many outlets responded.
- **Images** load through a free image service (images.weserv.nl) that resizes them and avoids sites that block direct loading. To load them straight from the news sites instead, set `USE_IMAGE_PROXY` to `false` at the top of `app.js`.
- **Personal use.** The app shows headlines, short snippets and links back to each outlet. It is built for your own reading. If you plan to share it widely, check each publisher's terms for their feeds.

## Try it on your computer

```
pip install -r requirements.txt
python tools/fetch_news.py --out news.json
python -m http.server 8000
```

Then open `http://localhost:8000`.

## Files

- `index.html`, `app.css`, `app.js`: the app
- `sw.js`, `manifest.webmanifest`, `icons/`: offline support and home screen install
- `tools/fetch_news.py`: collects, summarises and groups the news
- `sources.json`: the outlet list
- `.github/workflows/news.yml`: the schedule that keeps it fresh

If you change the app files, change `VERSION` at the top of `sw.js` (for example to `prism-v2`) so phones pick up the new version.
