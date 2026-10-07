#!/usr/bin/env python3
"""Prism news fetcher.

Reads sources.json, pulls every feed in parallel, cleans each item into a short
summary, finds an image, groups coverage of the same event across outlets and
writes one news.json for the web app to read.

Usage:
    python tools/fetch_news.py --out dist/news.json
"""

import argparse
import concurrent.futures as cf
import hashlib
import html
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests

# ---------------------------------------------------------------- settings
MAX_AGE_HOURS = 36          # ignore items older than this
PER_SOURCE = 15             # newest items kept per feed
MAX_STORIES_PER_SCOPE = 120
PAGE_FETCH_LIMIT = 160      # extra page loads to find missing images or summaries
FEED_TIMEOUT = 15
PAGE_TIMEOUT = 8
PAGE_BYTES = 250_000
MIN_SOURCES_OK = 3          # below this the run fails and the old data stays live
LINK_SIM = 0.30             # minimum similarity for two items to be one story
AVG_SIM = 0.15              # minimum average similarity between two groups to merge
SUMMARY_MAX = 300
UA = ("Mozilla/5.0 (compatible; PrismNewsReader/1.0; personal news reader) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36")

SIDE = {"left": "left", "centre-left": "left", "centre": "centre",
        "centre-right": "right", "right": "right"}

STOP = set("""
a an and are as at be been but by for from had has have he her his how i if in into is it its
may more most not of on one or our out over per she so than that the their them then there
these they this to up was we were what when where which who why will with you your about after
again against all also any because before being between both can could did do does doing down
during each few further here just me my no nor now off only other own same should some such
too under until very while would says said say new live update updates latest news report
reports reported today yesterday tonight amid via vs watch video photos photo explained
month year week day days time times return record plan plans announce announced launch show set see
make take get first last next big major key top high low rise rises fall falls many much well still
back end ends way ahead early late amid across among near over under open opens release released
people million billion crore lakh percent number figure figures
""".split())

TRAILERS = [
    r"\bThe post .{0,200}? appeared first on .{0,80}$",
    r"\s*(Continue|Click here to) read(ing)?.{0,40}$",
    r"\s*Read (more|the full story).{0,40}$",
    r"\s*\[?(…|\.\.\.)\]?\s*$",
]


# ---------------------------------------------------------------- text helpers
class _Strip:
    TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.I | re.S)

    @classmethod
    def text(cls, raw):
        if not raw:
            return ""
        raw = re.sub(r"<(br|/p|/div|/li)\s*/?>", " ", raw, flags=re.I)
        raw = cls.TAG.sub(" ", raw)
        raw = html.unescape(raw)
        return re.sub(r"\s+", " ", raw).strip()


def clean_title(title, headline_only, source_name):
    t = _Strip.text(title)
    if headline_only:
        # Google News appends " - Outlet" to every headline
        m = re.match(r"^(.*\S)\s+-\s+([^-]{2,45})$", t)
        if m:
            t = m.group(1)
    else:
        parts = t.rsplit(" | ", 1)
        if len(parts) == 2 and len(parts[1]) <= 30:
            t = parts[0]
    return t.strip()


def clean_summary(raw, title):
    s = _Strip.text(raw)
    for pat in TRAILERS:
        s = re.sub(pat, "", s, flags=re.I).strip()
    if title and s.lower().startswith(title.lower()):
        s = s[len(title):].lstrip(" :-.")
    return s


def shorten(s, limit=SUMMARY_MAX):
    """Keep the first one to three sentences, within limit characters."""
    if not s:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"“‘'(])", s)
    out = ""
    for sent in sentences[:3]:
        if out and len(out) + len(sent) + 1 > limit:
            break
        out = f"{out} {sent}".strip()
        if len(out) >= 140:
            break
    if len(out) > limit:
        cut = out[:limit].rsplit(" ", 1)[0].rstrip(",;:- ")
        out = cut + "…"
    return out


def useful_summary(summary, title):
    if len(summary) < 40:
        return False
    a = re.sub(r"\W+", "", summary.lower())
    b = re.sub(r"\W+", "", (title or "").lower())
    return not (b and (a == b or a.startswith(b) and len(a) - len(b) < 20))


# ---------------------------------------------------------------- images
BAD_IMG = re.compile(r"(pixel|1x1|spacer|blank\.|/logo|logo\.|sprite|avatar|favicon|\.svg|\.gif|"
                     r"doubleclick|feedburner|/ads?/|placeholder)", re.I)


def good_image(url):
    if not url or url.startswith("data:"):
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[len("http://"):]
    if not url.startswith("https://") or BAD_IMG.search(url):
        return None
    return url


def entry_image(entry):
    best, best_w = None, -1
    for key in ("media_content", "media_thumbnail"):
        for m in entry.get(key, []) or []:
            u = good_image(m.get("url"))
            if not u:
                continue
            if m.get("medium") not in (None, "image") and "image" not in (m.get("type") or "image"):
                continue
            try:
                w = int(m.get("width") or 0)
            except ValueError:
                w = 0
            if w > best_w:
                best, best_w = u, w
    if best:
        return best
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and "image" in (link.get("type") or ""):
            u = good_image(link.get("href"))
            if u:
                return u
    blobs = [entry.get("summary", "")]
    blobs += [c.get("value", "") for c in entry.get("content", []) or []]
    for blob in blobs:
        for m in re.finditer(r"<img[^>]+src=[\"']([^\"']+)[\"']", blob or "", re.I):
            u = good_image(html.unescape(m.group(1)))
            if u:
                return u
    return None


# ---------------------------------------------------------------- fetching
def read_feed(src, verbose=False):
    """Return (items, error)."""
    url = src["feed"]
    try:
        if url.startswith("file:"):
            data = Path(url[5:]).read_bytes()
        else:
            last = None
            for attempt in range(2):
                try:
                    r = requests.get(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
                                     timeout=FEED_TIMEOUT)
                    r.raise_for_status()
                    data = r.content
                    break
                except Exception as exc:  # noqa: BLE001
                    last = exc
                    time.sleep(1.5)
            else:
                raise last
        parsed = feedparser.parse(data)
        if not parsed.entries:
            return [], "feed had no entries"
    except Exception as exc:  # noqa: BLE001
        return [], f"{type(exc).__name__}: {str(exc)[:120]}"

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=MAX_AGE_HOURS)
    items = []
    for e in parsed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        link = e.get("link")
        title = clean_title(e.get("title", ""), src.get("headline_only"), src["name"])
        if not (t and link and title):
            continue
        when = min(datetime(*t[:6], tzinfo=timezone.utc), now)
        if when < cutoff:
            continue
        raw = e.get("summary", "")
        if not raw and e.get("content"):
            raw = e["content"][0].get("value", "")
        summary = ""
        if not src.get("headline_only"):
            summary = shorten(clean_summary(raw, title))
            if not useful_summary(summary, title):
                summary = ""
        items.append({
            "source": src["id"], "name": src["name"], "scope": src["scope"],
            "lean": src["lean"], "side": SIDE[src["lean"]],
            "headline_only": bool(src.get("headline_only")),
            "title": title, "summary": summary, "url": link,
            "published": when, "image": None if src.get("headline_only") else entry_image(e),
        })
    items.sort(key=lambda i: i["published"], reverse=True)
    return items[:PER_SOURCE], None


META = re.compile(r"<meta\s[^>]*>", re.I | re.S)


def page_meta(url):
    try:
        with requests.get(url, headers={"User-Agent": UA}, timeout=PAGE_TIMEOUT, stream=True) as r:
            r.raise_for_status()
            if "html" not in r.headers.get("content-type", "html"):
                return {}
            buf = b""
            for chunk in r.iter_content(16_384):
                buf += chunk
                if len(buf) >= PAGE_BYTES or b"</head>" in buf.lower():
                    break
            text = buf.decode(r.encoding or "utf-8", errors="ignore")
            base = r.url
    except Exception:  # noqa: BLE001
        return {}
    found = {}
    for m in META.finditer(text):
        tag = m.group(0)
        k = re.search(r"(?:property|name)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        v = re.search(r"content\s*=\s*\"([^\"]*)\"|content\s*=\s*'([^']*)'", tag, re.I)
        if k and v:
            found.setdefault(k.group(1).lower(), html.unescape(v.group(1) or v.group(2) or ""))
    img = found.get("og:image") or found.get("twitter:image") or found.get("twitter:image:src")
    desc = found.get("og:description") or found.get("twitter:description") or found.get("description")
    return {"image": good_image(urljoin(base, img)) if img else None,
            "summary": desc or ""}


def fill_gaps(items, limit, verbose=False):
    todo = [i for i in items if not i["headline_only"] and (not i["image"] or not i["summary"])]
    todo.sort(key=lambda i: i["published"], reverse=True)
    todo = todo[:limit]
    if not todo:
        return 0
    filled = 0
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        for item, meta in zip(todo, pool.map(lambda i: page_meta(i["url"]), todo)):
            hit = False
            if not item["image"] and meta.get("image"):
                item["image"], hit = meta["image"], True
            if not item["summary"] and meta.get("summary"):
                s = shorten(clean_summary(meta["summary"], item["title"]))
                if useful_summary(s, item["title"]):
                    item["summary"], hit = s, True
            filled += hit
    return filled


# ---------------------------------------------------------------- clustering
def stem(w):
    """Very light stemmer so discover, discovers and discovered match."""
    if w.isdigit():
        return w
    for suffix in ("ing", "ed", "es", "ly", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4 and not w.endswith("ss"):
            return w[: -len(suffix)]
    return w


def tokens(text):
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'’\-]*", text.lower()):
        w = re.sub(r"['’]s$", "", w).strip("'’-")
        if w in STOP or (len(w) < 3 and not w.isdigit()):
            continue
        out.append(stem(w))
    return out


def pair_sims(items):
    """Similarity between items that share enough informative words."""
    n = len(items)
    titles = [set(tokens(it["title"])) for it in items]
    docs = []
    for it in items:
        tf = Counter()
        for w in tokens(it["title"]):
            tf[w] += 2
        for w in tokens(it["summary"][:220]):
            tf[w] += 1
        docs.append(tf)
    df = Counter(w for d in docs for w in d)
    cap = max(30, int(0.06 * n))
    idf = {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items() if 2 <= c <= cap}
    vec = [{w: tf * idf[w] for w, tf in d.items() if w in idf} for d in docs]
    norm = [math.sqrt(sum(x * x for x in v.values())) for v in vec]

    post = defaultdict(list)
    for i, v in enumerate(vec):
        for w, x in v.items():
            post[w].append((i, x))
    dot, shared = defaultdict(float), Counter()
    for plist in post.values():
        for a in range(len(plist)):
            for b in range(a + 1, len(plist)):
                (i, xi), (j, xj) = plist[a], plist[b]
                dot[(i, j)] += xi * xj
                shared[(i, j)] += 1
    sim = {}
    for (i, j), d in dot.items():
        both = len(titles[i] & titles[j])
        enough = shared[(i, j)] >= 3 or (shared[(i, j)] >= 2 and both >= 2)
        if norm[i] and norm[j] and enough:
            sim[(i, j)] = d / (norm[i] * norm[j])
    return sim


def cluster(items):
    n = len(items)
    sim = pair_sims(items)
    group = {i: {i} for i in range(n)}
    owner = list(range(n))
    names = {i: {items[i]["name"]} for i in range(n)}
    for (i, j), s in sorted(sim.items(), key=lambda kv: kv[1], reverse=True):
        if s < LINK_SIM:
            break
        gi, gj = owner[i], owner[j]
        if gi == gj or names[gi] & names[gj]:
            continue
        total = sum(sim.get((min(a, b), max(a, b)), 0.0) for a in group[gi] for b in group[gj])
        if total / (len(group[gi]) * len(group[gj])) < AVG_SIM:
            continue
        group[gi] |= group[gj]
        names[gi] |= names[gj]
        for k in group[gj]:
            owner[k] = gi
        del group[gj], names[gj]
    return [sorted(g) for g in group.values()]


def lead_score(it):
    s = (2 if it["image"] else 0) + (2 if it["summary"] else 0)
    s += {"centre": 1.5, "centre-left": 0.5, "centre-right": 0.5}.get(it["lean"], 0)
    return s


def build_story(members, now):
    members = sorted(members, key=lambda i: i["published"])
    lead = max(members, key=lambda i: (lead_score(i), i["published"]))
    image = lead["image"] or next((m["image"] for m in members if m["image"]), None)
    summary = lead["summary"] or next((m["summary"] for m in members if m["summary"]), "")

    seen, outlets = set(), []
    for m in sorted(members, key=lambda i: i["published"]):
        if m["name"] in seen:
            continue
        seen.add(m["name"])
        outlets.append({
            "id": m["source"], "name": m["name"], "lean": m["lean"], "side": m["side"],
            "title": m["title"], "summary": m["summary"], "url": m["url"],
            "published": m["published"].isoformat(timespec="seconds"),
        })
    sides = Counter(o["side"] for o in outlets)
    left, centre, right = sides["left"], sides["centre"], sides["right"]
    blind = None
    if len(outlets) >= 3:
        if left == 0 and right >= 2:
            blind = "left"
        elif right == 0 and left >= 2:
            blind = "right"

    scopes = Counter(m["scope"] for m in members)
    scope = "india" if scopes["india"] >= scopes["world"] else "world"
    newest = max(m["published"] for m in members)
    oldest = min(m["published"] for m in members)
    age_h = max(0.0, (now - newest).total_seconds() / 3600)
    score = len(outlets) + 3 * math.exp(-age_h / 8)
    sid = hashlib.sha1(min(members, key=lambda i: i["published"])["url"].encode()).hexdigest()[:10]
    return {
        "id": sid, "scope": scope, "title": lead["title"], "summary": summary, "image": image,
        "published": newest.isoformat(timespec="seconds"),
        "first": oldest.isoformat(timespec="seconds"),
        "score": round(score, 2),
        "counts": {"left": left, "centre": centre, "right": right, "total": len(outlets)},
        "blindspot": blind,
        "outlets": outlets,
    }


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", default=str(Path(__file__).resolve().parent.parent / "sources.json"))
    ap.add_argument("--out", default="news.json")
    ap.add_argument("--no-page-fetch", action="store_true", help="skip loading article pages for missing images")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(Path(args.sources).read_text(encoding="utf-8"))
    sources = [s for s in cfg["sources"] if s.get("enabled", True)]
    now = datetime.now(timezone.utc)

    health, all_items = [], []
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        for src, (items, err) in zip(sources, pool.map(read_feed, sources)):
            health.append({"id": src["id"], "name": src["name"], "scope": src["scope"], "lean": src["lean"],
                           "ok": err is None, "count": len(items), "error": err})
            all_items += items
            if args.verbose or err:
                print(f"{'ok  ' if not err else 'FAIL'} {src['name']:<22} {len(items):>3} items  {err or ''}")

    ok = sum(h["ok"] for h in health)
    print(f"\n{ok} of {len(sources)} sources responded, {len(all_items)} items in the last {MAX_AGE_HOURS}h")
    if ok < MIN_SOURCES_OK:
        print("Too few sources responded. Leaving the previous news.json untouched.", file=sys.stderr)
        return 2

    # drop duplicates: same link, or same outlet repeating a headline
    seen_url, seen_title, unique = set(), set(), []
    for it in sorted(all_items, key=lambda i: i["published"], reverse=True):
        key = (it["name"], re.sub(r"\W+", "", it["title"].lower()))
        if it["url"] in seen_url or key in seen_title:
            continue
        seen_url.add(it["url"])
        seen_title.add(key)
        unique.append(it)

    if not args.no_page_fetch:
        filled = fill_gaps(unique, PAGE_FETCH_LIMIT, args.verbose)
        print(f"Filled a missing image or summary for {filled} items from article pages")

    stories = [build_story([unique[i] for i in g], now) for g in cluster(unique)]
    stories.sort(key=lambda s: s["score"], reverse=True)
    kept, per = [], Counter()
    for s in stories:
        if per[s["scope"]] < MAX_STORIES_PER_SCOPE:
            per[s["scope"]] += 1
            kept.append(s)

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "stats": {"sources_total": len(sources), "sources_ok": ok, "items": len(unique), "stories": len(kept)},
        "sources": health,
        "stories": kept,
    }
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)
    multi = sum(1 for s in kept if s["counts"]["total"] > 1)
    print(f"Wrote {path} with {len(kept)} stories ({multi} covered by more than one outlet, "
          f"{sum(1 for s in kept if s['blindspot'])} blindspots), {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
