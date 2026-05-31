#!/usr/bin/env python3
"""Rolling record of stories already covered in past digests.

Persisted at docs/history.json — NOT data/ — because data/ is gitignored and the
daily GitHub Action checks out a fresh repo each run (so it has no memory of
yesterday). docs/ is committed (`git add docs/`), so the ledger survives across
runs and lets us guarantee each digest is fresh.
"""

import json
import re
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = ROOT / "docs" / "history.json"

URL_DEDUP_DAYS = 10      # drop raw items whose URL was covered within this window
PROMPT_CONTEXT_DAYS = 7  # how much recent coverage to show the model as "already covered"
MAX_ENTRIES = 30         # cap ledger size so it doesn't grow unbounded


def normalize_url(url):
    """Canonicalize a URL for comparison: drop scheme, www, query, fragment, trailing slash."""
    if not url:
        return ""
    u = url.strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0]
    return u.rstrip("/")


def load_history():
    """Return the list of past coverage entries (empty list if none/unreadable)."""
    if HISTORY_PATH.exists():
        try:
            with open(HISTORY_PATH) as f:
                return json.load(f).get("entries", [])
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _recent(entries, days, as_of):
    cutoff = as_of - timedelta(days=days)
    out = []
    for e in entries:
        try:
            d = datetime.strptime(e["date"], "%Y-%m-%d")
        except (KeyError, ValueError):
            continue
        if cutoff <= d <= as_of:
            out.append(e)
    return sorted(out, key=lambda x: x["date"], reverse=True)


def seen_urls(entries, as_of, days=URL_DEDUP_DAYS):
    """Set of normalized URLs covered within the last `days` days."""
    urls = set()
    for e in _recent(entries, days, as_of):
        for u in e.get("urls", []):
            n = normalize_url(u)
            if n:
                urls.add(n)
    return urls


def recent_coverage(entries, as_of, days=PROMPT_CONTEXT_DAYS):
    """Recent entries (most-recent-first) to show the model as already-covered context."""
    return _recent(entries, days, as_of)


def append_entry(entries, date_str, title, headings, urls):
    """Add (or replace) today's coverage record and persist the ledger."""
    entries = [e for e in entries if e.get("date") != date_str]
    entries.append({
        "date": date_str,
        "title": title,
        "headings": headings,
        "urls": sorted({u for u in urls if u}),
    })
    entries = sorted(entries, key=lambda x: x["date"], reverse=True)[:MAX_ENTRIES]
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump({"entries": entries}, f, indent=2)
    return entries
