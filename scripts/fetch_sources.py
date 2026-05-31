#!/usr/bin/env python3
"""Fetch content from configured sources for a given date."""

import argparse
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import feedparser
import requests
import yaml

import history

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "topics.yaml"
DATA_DIR = ROOT / "data"

# A browser-like User-Agent. Many sources (Reddit, Substack feeds, etc.) return
# 403/empty for the default python-requests/feedparser UA, so we send this everywhere.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) DailyDigestBot/1.0")


def parse_feed(url):
    """Fetch a feed with a browser-like UA, then hand the bytes to feedparser.

    feedparser's own fetch sends a UA that many hosts block; fetching via
    requests first avoids the silent empty-feed failures that result.
    """
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def fetch_hackernews(config, keywords, date_str):
    """Fetch top HN stories matching topic keywords."""
    items = []
    max_stories = config.get("max_stories", 30)
    timestamp_start = int(datetime.strptime(date_str, "%Y-%m-%d").timestamp())
    timestamp_end = timestamp_start + 86400

    for kw in keywords[:5]:
        url = "https://hn.algolia.com/api/v1/search"
        params = {
            "query": kw,
            "tags": "story",
            "numericFilters": f"created_at_i>{timestamp_start},created_at_i<{timestamp_end}",
            "hitsPerPage": max_stories,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            for hit in resp.json().get("hits", []):
                items.append({
                    "source": "hackernews",
                    "title": hit.get("title", ""),
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "hn_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                    "score": hit.get("points", 0),
                    "num_comments": hit.get("num_comments", 0),
                    "author": hit.get("author", ""),
                    "created_at": hit.get("created_at", ""),
                })
        except Exception as e:
            print(f"  HN fetch error for '{kw}': {e}")

    seen = set()
    deduped = []
    for item in sorted(items, key=lambda x: x["score"], reverse=True):
        if item["title"] not in seen:
            seen.add(item["title"])
            deduped.append(item)
    return deduped[:max_stories]


def fetch_reddit(config, keywords, date_str):
    """Fetch top Reddit posts from configured subreddits."""
    items = []
    subreddits = config.get("subreddits", [])
    max_posts = config.get("max_posts_per_sub", 15)
    headers = {"User-Agent": USER_AGENT}

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={max_posts}"
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            for post in resp.json().get("data", {}).get("children", []):
                d = post["data"]
                items.append({
                    "source": "reddit",
                    "subreddit": sub,
                    "title": d.get("title", ""),
                    "url": d.get("url", ""),
                    "reddit_url": f"https://reddit.com{d.get('permalink', '')}",
                    "score": d.get("score", 0),
                    "num_comments": d.get("num_comments", 0),
                    "selftext": (d.get("selftext", "") or "")[:500],
                    "author": d.get("author", ""),
                })
        except Exception as e:
            print(f"  Reddit fetch error for r/{sub}: {e}")

    return items


def fetch_rss(config):
    """Fetch recent entries from RSS feeds."""
    items = []
    feeds = config.get("feeds", [])

    for feed_cfg in feeds:
        try:
            feed = parse_feed(feed_cfg["url"])
            for entry in feed.entries[:10]:
                published = entry.get("published", entry.get("updated", ""))
                items.append({
                    "source": "rss",
                    "feed_name": feed_cfg["name"],
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": (entry.get("summary", "") or "")[:500],
                    "published": published,
                    "author": entry.get("author", feed_cfg["name"]),
                })
        except Exception as e:
            print(f"  RSS fetch error for {feed_cfg['name']}: {e}")

    return items


def fetch_podcasts(config):
    """Fetch recent podcast episodes from RSS feeds."""
    items = []
    feeds = config.get("feeds", [])

    for feed_cfg in feeds:
        try:
            feed = parse_feed(feed_cfg["url"])
            for entry in feed.entries[:3]:
                items.append({
                    "source": "podcast",
                    "podcast_name": feed_cfg["name"],
                    "title": entry.get("title", ""),
                    "url": entry.get("link", ""),
                    "summary": (entry.get("summary", "") or "")[:500],
                    "published": entry.get("published", ""),
                    "duration": entry.get("itunes_duration", ""),
                })
        except Exception as e:
            print(f"  Podcast fetch error for {feed_cfg['name']}: {e}")

    return items


def fetch_arxiv(config, keywords):
    """Fetch recent arXiv papers matching topic keywords.

    Primary research is the freshest, least-duplicated source tier. We query the
    arXiv API (Atom feed) sorted by submission date so each day surfaces genuinely
    new papers, then let the shortlist funnel rank them against everything else.
    """
    items = []
    # `config` is the arxiv sub-config (passed as sources["arxiv"]), matching how
    # the other fetchers receive their own block.
    max_results = config.get("max_results", 15)
    categories = config.get("categories", ["cs.CL", "cs.AI", "cs.LG"])
    queries = config.get("queries") or keywords[:6]

    cat_filter = " OR ".join(f"cat:{c}" for c in categories)
    for i, q in enumerate(queries):
        # arXiv rate-limits rapid requests (returns 200 + empty feed). Their API
        # guidance is ~3s between calls, so pace ourselves between queries.
        if i > 0:
            time.sleep(3)
        # Restrict to AI categories AND the keyword, newest first.
        search = f"({cat_filter}) AND all:\"{q}\""
        url = ("http://export.arxiv.org/api/query?"
               f"search_query={requests.utils.quote(search)}"
               f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}")
        try:
            feed = parse_feed(url)
            for entry in feed.entries:
                items.append({
                    "source": "arxiv",
                    "query": q,
                    "title": (entry.get("title", "") or "").replace("\n", " ").strip(),
                    "url": entry.get("link", ""),
                    "summary": (entry.get("summary", "") or "").replace("\n", " ").strip()[:500],
                    "published": entry.get("published", ""),
                    "author": ", ".join(a.get("name", "") for a in entry.get("authors", [])[:4]),
                })
        except Exception as e:
            print(f"  arXiv fetch error for '{q}': {e}")

    # Dedup by URL (the same paper matches multiple keyword queries).
    seen, deduped = set(), []
    for it in items:
        if it["url"] and it["url"] not in seen:
            seen.add(it["url"])
            deduped.append(it)
    return deduped[:max_results * 2]


def fetch_github_trending():
    """Fetch today's trending repos from GitHub."""
    items = []
    try:
        resp = requests.get(
            "https://api.github.com/search/repositories",
            params={"q": "created:>=" + (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
                    "sort": "stars", "order": "desc", "per_page": 15},
            headers={"Accept": "application/vnd.github.v3+json"},
            timeout=15,
        )
        resp.raise_for_status()
        for repo in resp.json().get("items", []):
            desc = repo.get("description") or ""
            topics = " ".join(repo.get("topics", []))
            ai_signal = any(kw in (desc + topics).lower() for kw in
                           ["ai", "llm", "gpt", "claude", "agent", "ml", "model", "inference",
                            "transformer", "neural", "embedding", "rag", "prompt", "mcp"])
            if ai_signal:
                items.append({
                    "name": repo["full_name"],
                    "url": repo["html_url"],
                    "description": desc[:120],
                    "language": repo.get("language", ""),
                    "stars": repo["stargazers_count"],
                    "stars_today": repo.get("watchers_count", 0),
                })
    except Exception as e:
        print(f"  GitHub trending error: {e}")
    return items[:8]


def fetch_hf_papers():
    """Fetch daily papers from Hugging Face."""
    items = []
    try:
        resp = requests.get("https://huggingface.co/api/daily_papers", timeout=15)
        resp.raise_for_status()
        for paper in resp.json()[:8]:
            items.append({
                "title": paper.get("title", ""),
                "url": f"https://huggingface.co/papers/{paper.get('paper', {}).get('id', '')}",
                "summary": paper.get("paper", {}).get("summary", "")[:200],
                "upvotes": paper.get("paper", {}).get("upvotes", 0),
            })
    except Exception as e:
        print(f"  HF papers error: {e}")
    return items


def main():
    parser = argparse.ArgumentParser(description="Fetch sources for daily digest")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                        help="Date to fetch for (YYYY-MM-DD)")
    args = parser.parse_args()
    date_str = args.date

    print(f"Fetching sources for {date_str}...")
    config = load_config()
    all_keywords = []
    for topic in config.get("topics", []):
        all_keywords.extend(topic.get("keywords", []))

    result = {"date": date_str, "fetched_at": datetime.now().isoformat(), "items": []}

    sources = config.get("sources", {})

    if sources.get("hackernews", {}).get("enabled"):
        print("  Fetching Hacker News...")
        result["items"].extend(fetch_hackernews(sources["hackernews"], all_keywords, date_str))

    if sources.get("reddit", {}).get("enabled"):
        print("  Fetching Reddit...")
        result["items"].extend(fetch_reddit(sources["reddit"], all_keywords, date_str))

    if sources.get("rss", {}).get("enabled"):
        print("  Fetching RSS feeds...")
        result["items"].extend(fetch_rss(sources["rss"]))

    if sources.get("podcasts", {}).get("enabled"):
        print("  Fetching podcasts...")
        result["items"].extend(fetch_podcasts(sources["podcasts"]))

    if sources.get("arxiv", {}).get("enabled"):
        print("  Fetching arXiv...")
        result["items"].extend(fetch_arxiv(sources["arxiv"], all_keywords))

    # Sidebar data
    result["sidebar"] = {}

    print("  Fetching GitHub trending...")
    result["sidebar"]["github_trending"] = fetch_github_trending()

    print("  Fetching HuggingFace papers...")
    result["sidebar"]["papers"] = fetch_hf_papers()

    # Drop items whose URL was already covered in a recent digest, so the model
    # never re-reports yesterday's news. (History lives in docs/, which the CI
    # commits — unlike data/, which is gitignored and lost between runs.)
    as_of = datetime.strptime(date_str, "%Y-%m-%d")
    seen = history.seen_urls(history.load_history(), as_of)
    if seen:
        before = len(result["items"])
        result["items"] = [
            it for it in result["items"]
            if history.normalize_url(it.get("url", "")) not in seen
        ]
        dropped = before - len(result["items"])
        print(f"  Dedup: dropped {dropped} item(s) already covered in the last "
              f"{history.URL_DEDUP_DAYS} days ({len(result['items'])} remain)")

    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"{date_str}_raw.json"
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print(f"Saved {len(result['items'])} items to {out_path}")


if __name__ == "__main__":
    main()
