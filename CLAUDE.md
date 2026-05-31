# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A daily AI newsletter that runs fully autonomously. A GitHub Action (`.github/workflows/daily.yml`, 00:30 UTC / 6 AM IST) fetches AI news from many sources, has Claude synthesize a digest, builds static HTML, and commits it to `docs/`. The site is served from `docs/` (GitHub Pages style — note the `/daily-newsletter/` base path fallback in `docs/app.js`). There is no test suite, build step, or linter — it's a Python pipeline plus a static site.

## Pipeline (run in this order)

```bash
python scripts/fetch_sources.py    --date YYYY-MM-DD   # -> data/<date>_raw.json
python scripts/generate_digest.py  --date YYYY-MM-DD   # -> data/<date>_digest.json  (needs ANTHROPIC_API_KEY)
python scripts/build_site.py                           # -> docs/digests/<date>.html, index.html, manifest.json
```

`--date` defaults to today. `build_site.py` takes no date — it rebuilds every page from all `data/*_digest.json` files. Dependencies: `pip install -r requirements.txt`. The API key is read from the environment by the Anthropic SDK (`anthropic.Anthropic()`); locally, export it or use a gitignored `.env`.

To preview the site locally you must use an HTTP server, not `file://` — `app.js` does `fetch()` for `manifest.json`, which browsers block over `file://`:
```bash
cd docs && python3 -m http.server 8765
```

## Critical architectural constraint: `data/` is ephemeral, `docs/` is the only memory

`data/` is **gitignored**, and the CI checks out a fresh repo every run. So `data/*_raw.json` and `data/*_digest.json` exist only transiently in CI and are **not** how state persists. Only `docs/` is committed (`git add docs/`). Any state that must survive between daily runs **must live under `docs/`**. This is the single most important thing to understand here — it's the reason the cross-day deduplication ledger (`docs/history.json`) exists rather than living in `data/`.

A consequence: `build_site.py` reads existing `docs/manifest.json` and merges, so it never loses past entries even when `data/` only contains today's digest (the normal CI case).

## How the digest is generated (`scripts/generate_digest.py`)

A **two-stage funnel**, because fetching all sources yields ~300 items/day and feeding that raw to the writer both blows the context budget and dilutes quality:

1. **Shortlist** (`shortlist_items`, Haiku) — ranks all ~300 raw items down to the 50 most newsworthy by source quality + novelty + diversity. Has a deterministic fallback (keep all non-Reddit + top Reddit by score) if the call fails. This **replaced** a blind 150K-char truncation that silently cut the highest-quality sources first (they're appended last in `fetch_sources.py`).
2. **Write** (Sonnet 4.6) — synthesizes the digest from the shortlist + a "freshness block" of recently-covered titles/headings from the history ledger.
3. **Extract** (Haiku) — pulls sidebar launches/pricing as a side call.

Models are centralized as constants at the top of the file: `WRITER_MODEL`, `SHORTLIST_MODEL`, `EXTRACT_MODEL`. **Keep these on Sonnet/Haiku — do not use Opus** (deliberate cost decision). Change models only here.

The output JSON schema (`title`, `subtitle`, `sections[].{heading,body,sources}`) is a hard contract consumed by `build_site.py` and `templates/digest.html`. Section `body` is raw HTML produced by the model. The final section must be titled "What This All Means" and is styled via `<div class="takeaways">`. The system prompt bans formulaic closers ("the strategic implication for builders:", etc.) and forbids bold lead-in labels on takeaways — preserve these rules if editing the prompt; they were added to fix real quality regressions.

## Freshness / deduplication (`scripts/history.py`)

Prevents the same news appearing across consecutive days. The ledger is `docs/history.json` (committed — see the constraint above). Two windows:
- **Hard URL filter (10 days)** in `fetch_sources.py`: drops raw items whose normalized URL was covered recently, before the model ever sees them.
- **Soft thematic context (7 days)** in `generate_digest.py`: injects recent titles/headings as "already covered" guidance and down-ranks rehashes during shortlisting.

After writing, `generate_digest.py` records today's title, headings, and all cited URLs (from `sources[].url` and inline `href`s) back into the ledger. URLs are compared via `normalize_url` (strips scheme/www/query/fragment) since the same story appears with different titles across HN/Reddit/RSS but a stable link.

## Sources (`config/topics.yaml`)

All sources, topics, and keywords live here — no source URLs are hardcoded in Python. HN (Algolia, keyword + date windowed), Reddit, RSS blogs, podcasts, plus sidebar-only GitHub trending / HF papers.

**Fetching gotcha:** the default `python-requests`/`feedparser` User-Agent gets `403`d or returns empty from Reddit, Substack, and other hosts. All fetching goes through a browser-like `USER_AGENT` — RSS/podcasts via the `parse_feed()` helper (fetch with `requests`, then hand bytes to `feedparser`). If adding a source, route it through this, and **validate the feed actually returns entries with the UA before committing** — dead feeds fail silently (Anthropic's and The Batch's RSS were removed upstream; that's why they're absent). The fictional 2026 dates mean live HN/date-windowed fetches return nothing when testing against "today"; test generation logic against the real `data/*_raw.json` fixtures instead.

## Deployment

Committing to `docs/` on `main` is itself the deploy — the daily Action pushes there. The bot commits `Daily digest for <date>` autonomously, so `main` frequently moves ahead of local; `git pull --rebase` (or rebase onto `origin/main`) before pushing. Bot commits touch only `docs/` so they won't conflict with script/config changes.
