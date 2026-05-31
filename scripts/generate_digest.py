#!/usr/bin/env python3
"""Generate a themed daily digest from raw fetched data using Claude."""

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

import anthropic
import yaml

import history

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "topics.yaml"
DATA_DIR = ROOT / "data"

# Models. The digest is written once a day, so we use the most capable model for
# the synthesis that the reader actually sees, and cheap/fast models for the
# mechanical pre-processing (shortlisting) and side-extraction (launches).
WRITER_MODEL = "claude-sonnet-4-6"          # main digest synthesis
SHORTLIST_MODEL = "claude-haiku-4-5-20251001"  # stage-1 curation of raw items
EXTRACT_MODEL = "claude-haiku-4-5-20251001"    # sidebar launches/pricing extraction

# Two-stage funnel: we fetch wide (~300 items/day across HN, Reddit, RSS, podcasts)
# but only the most newsworthy items should reach the writer. Stage 1 (Haiku) ranks
# everything and keeps the top SHORTLIST_SIZE; stage 2 (the writer) writes from that.
SHORTLIST_SIZE = 50

SYSTEM_PROMPT = """You are a senior AI product strategist writing a daily briefing for AI product managers who are also builders. Your reader is a PM at an AI company who ships product, writes code, reviews designs, and needs to stay ahead of the curve — not just informed, but opinionated and ready to act.

Your task: given today's raw content from Hacker News, Reddit, RSS blogs, newsletters, and podcasts, synthesize a compelling daily digest.

Writing angle:
- Your reader builds AI products: they ship code, review designs, and make bets. They care what today's news changes for the things they build — new capabilities, pricing shifts, migration decisions, competitive moves, when research hits production.
- Lead with the news and the story. Let the "so what for builders" emerge from how you tell it — through specifics, framing, and what you choose to emphasize — rather than a tacked-on verdict.
- Trust the reader. They don't need every paragraph to spell out the takeaway; a sharp fact or a well-chosen quote often lands harder than an explicit "the implication is…".

Voice and craft (this is what separates a great briefing from a generic one):
- Vary your sentence rhythm and how each section opens. Do not start sections the same way or end them with the same move.
- Be concrete: numbers, names, versions, prices, dates. Specifics persuade; abstractions bore.
- BANNED phrasings — never use these or close variants, they read as filler:
  "The strategic implication for builders:", "What this means for builders:", "the implication is…",
  "the takeaway here is…", "at the end of the day", "it's worth noting that", "in a world where…",
  "the bottom line:", "make no mistake". If you catch yourself writing a sentence whose only job is to
  announce significance, cut it and show the significance instead.
- Earn your opinions with evidence. Surface contrarian takes when they hold up. Be fair to people you disagree with.

Rules:
1. Identify 4-6 major themes from the content. Each theme becomes a section.
2. Write each section as a 200-350 word narrative — not a list of links. Tell the story of what happened and why it matters.
3. Cite sources inline using HTML links. Every claim should link to its source.
4. For each section, include at least one compelling quote in a <blockquote> tag — a direct quote from a source, a key user comment, or a striking line from an article. Always attribute it. Pick quotes that carry an idea, not generic praise.
5. The FINAL section MUST be titled "What This All Means" — your PM brief. Format it as a numbered <ol> list of 3-4 takeaways, wrapped in <div class="takeaways">. Each <li> is ONE clean point: 1-2 plain sentences that state what today's news means and what to do about it. Do NOT prefix points with a bold lead-in label, a category tag, or boilerplate like "Actionable insight." — no <strong> lead-ins, just the point itself, written naturally.
6. Give the digest a short, punchy title — MAX 5-6 words. Think newspaper headline, not essay title. It MUST be clearly distinct from the recent titles listed below — no reusing the same anchor phrase ("Agents…", "Codex Goes…") that recent days already used.
7. Write a 2-3 sentence subtitle/hook that previews the actual specifics of today, not vague throat-clearing.
8. Be opinionated but fair. Surface contrarian takes when they have merit.
9. For each section, list the source URLs used (for source tags).

IMPORTANT HTML formatting rules:
- Use <blockquote><p>"Quote text here."</p><cite>— Attribution</cite></blockquote> for quotes
- Use <a href="url"> for inline citations (use double quotes for href, escape them properly in JSON with backslash)
- For the final section, wrap content in <div class="takeaways"><ol><li>...</li></ol></div>
- Make sure all HTML attribute values use escaped double quotes in the JSON string

Respond with valid JSON matching this schema:
{
  "title": "string — short punchy title, 5-6 words max",
  "subtitle": "string — 2-3 sentence hook",
  "sections": [
    {
      "heading": "string — section title",
      "body": "string — HTML content with <p>, <a>, <blockquote> tags",
      "sources": [
        {"url": "string", "label": "string — short label", "icon": "string — emoji"}
      ]
    }
  ]
}

Only return the JSON object, no markdown fences or other text."""


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def shortlist_items(client, items, date_str, recent_titles):
    """Stage 1 of the funnel: cheaply rank ~300 raw items down to the most
    newsworthy SHORTLIST_SIZE, so the writer model gets a tight, high-signal
    payload instead of a firehose. Returns the selected items (a subset of
    `items`), falling back to a deterministic heuristic if the call fails.

    The model sees a compact index (id + source + title + score) — not full
    bodies — so this stays cheap even with hundreds of items.
    """
    if len(items) <= SHORTLIST_SIZE:
        return items

    index = []
    for i, it in enumerate(items):
        index.append({
            "id": i,
            "source": it.get("source", ""),
            "origin": it.get("feed_name") or it.get("subreddit") or it.get("podcast_name") or "",
            "title": (it.get("title", "") or "")[:200],
            "score": it.get("score", 0),
            "comments": it.get("num_comments", 0),
        })

    avoid = ""
    if recent_titles:
        avoid = ("\nRecent digests already covered these — DOWN-rank anything that just rehashes them:\n"
                 + "\n".join(f"- {t}" for t in recent_titles[:10]) + "\n")

    prompt = f"""You are the editor of a daily AI briefing for a PM who builds AI products. From the {len(items)} candidate items below, select the {SHORTLIST_SIZE} MOST newsworthy and highest-signal for today ({date_str}).

Selection criteria, in priority order:
1. Genuinely new and important for someone building AI products (model/tool launches, pricing, capabilities, research that hits production, sharp practitioner analysis).
2. Substance over noise — prefer thoughtful sources and concrete developments over hot takes, memes, and low-effort posts.
3. Diversity — cover the day's distinct stories; don't pick 10 near-duplicate items about the same thing (keep the 1-2 best of each cluster).
4. Source quality matters: practitioner blogs/newsletters and company announcements usually outrank a random high-upvote Reddit post.
{avoid}
Candidate items (id | source/origin | title | score):
{json.dumps(index, indent=0)}

Return ONLY a JSON array of the selected item ids, best first, exactly like: [12, 3, 47, ...]. No prose."""

    try:
        msg = client.messages.create(
            model=SHORTLIST_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        ids = json.loads(text)
        selected = [items[i] for i in ids if isinstance(i, int) and 0 <= i < len(items)]
        if selected:
            print(f"  Shortlisted {len(selected)} of {len(items)} items via {SHORTLIST_MODEL}")
            return selected[:SHORTLIST_SIZE]
        raise ValueError("empty selection")
    except Exception as e:
        # Fallback: keep all non-Reddit items + top Reddit by score, so the
        # high-quality feeds always survive even if shortlisting fails.
        print(f"  Shortlist failed ({e}); using heuristic fallback")
        non_reddit = [it for it in items if it.get("source") != "reddit"]
        reddit = sorted((it for it in items if it.get("source") == "reddit"),
                        key=lambda x: x.get("score", 0), reverse=True)
        return (non_reddit + reddit)[:SHORTLIST_SIZE]


def main():
    parser = argparse.ArgumentParser(description="Generate digest from raw data")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    args = parser.parse_args()
    date_str = args.date

    raw_path = DATA_DIR / f"{date_str}_raw.json"
    if not raw_path.exists():
        print(f"No raw data found at {raw_path}. Run fetch_sources.py first.")
        return

    with open(raw_path) as f:
        raw_data = json.load(f)

    config = load_config()
    topics_desc = "\n".join(
        f"- {t['name']}: {', '.join(t['keywords'][:5])}"
        for t in config.get("topics", [])
    )

    # Build "already covered" context from recent digests so today reads fresh.
    entries = history.load_history()
    as_of = datetime.strptime(date_str, "%Y-%m-%d")
    recent = history.recent_coverage(entries, as_of)
    recent_titles = [e.get("title", "") for e in recent if e.get("title")]

    client = anthropic.Anthropic()

    # Stage 1 of the funnel: rank the wide fetch down to the most newsworthy items
    # so the writer gets a tight, high-signal payload (and the best sources are
    # never lost to blind truncation).
    selected = shortlist_items(client, raw_data["items"], date_str, recent_titles)
    items_text = json.dumps(selected, indent=1, default=str)
    # Safety net only — the shortlist should already be well under this.
    if len(items_text) > 150_000:
        items_text = items_text[:150_000] + "\n... (truncated)"

    if recent:
        covered_lines = "\n".join(
            f"- {e['date']} — \"{e['title']}\": " + "; ".join(e.get("headings", []))
            for e in recent
        )
        freshness_block = f"""ALREADY COVERED in the last {history.PROMPT_CONTEXT_DAYS} days — do NOT repeat these stories or angles. Find what is genuinely new today, or a distinctly fresh development on an ongoing story:
{covered_lines}

"""
    else:
        freshness_block = ""

    user_prompt = f"""Today's date: {date_str}

Topics of interest:
{topics_desc}

{freshness_block}Here are today's raw items from various sources:

{items_text}

Now synthesize this into a daily digest following the instructions. Make today distinct from the recent digests listed above. Return only valid JSON."""

    print(f"Generating digest for {date_str} via {WRITER_MODEL}...")
    message = client.messages.create(
        model=WRITER_MODEL,
        max_tokens=8192,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt}],
    )

    response_text = message.content[0].text.strip()
    if response_text.startswith("```"):
        response_text = response_text.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        digest = json.loads(response_text)
    except json.JSONDecodeError:
        # Retry with the model asked to fix its own JSON
        print("  JSON parse failed, asking model to fix...")
        fix_msg = client.messages.create(
            model=WRITER_MODEL,
            max_tokens=8192,
            messages=[
                {"role": "user", "content": f"The following JSON is malformed. Fix it and return ONLY valid JSON, nothing else:\n\n{response_text}"},
            ],
        )
        fixed = fix_msg.content[0].text.strip()
        if fixed.startswith("```"):
            fixed = fixed.split("\n", 1)[1].rsplit("```", 1)[0]
        digest = json.loads(fixed)
    digest["date"] = date_str
    digest["generated_at"] = datetime.now().isoformat()

    # Extract launches & pricing moves via Haiku (cheap, fast)
    print("  Extracting launches & pricing...")
    try:
        launches_msg = client.messages.create(
            model=EXTRACT_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": f"""From today's AI news items, extract product launches, pricing changes, and deprecations. Return a JSON array of objects:
[{{"company": "string", "detail": "string — one sentence", "type": "launch|pricing|deprecation"}}]

Only include concrete, verifiable moves (not rumors). Max 6 items. Return ONLY the JSON array.

Items:
{items_text[:50000]}"""}],
        )
        launches_text = launches_msg.content[0].text.strip()
        if launches_text.startswith("```"):
            launches_text = launches_text.split("\n", 1)[1].rsplit("```", 1)[0]
        digest["sidebar_launches"] = json.loads(launches_text)
    except Exception as e:
        print(f"  Launches extraction failed: {e}")
        digest["sidebar_launches"] = []

    # Pass through sidebar data from raw fetch
    sidebar = raw_data.get("sidebar", {})
    digest["sidebar_github"] = sidebar.get("github_trending", [])
    digest["sidebar_papers"] = sidebar.get("papers", [])
    digest["sidebar_producthunt"] = sidebar.get("producthunt", [])

    # Record today's coverage in the committed ledger so future runs can dedup.
    headings = [s.get("heading", "") for s in digest.get("sections", [])]
    covered_urls = []
    for s in digest.get("sections", []):
        for src in s.get("sources", []):
            if src.get("url"):
                covered_urls.append(src["url"])
        # also capture inline-cited hrefs so dedup catches links not in source tags
        covered_urls.extend(re.findall(r'href=\\?"(https?://[^"\\]+)', s.get("body", "")))
    history.append_entry(entries, date_str, digest.get("title", ""), headings, covered_urls)
    print(f"  Recorded {len(set(covered_urls))} URL(s) in history ledger")

    out_path = DATA_DIR / f"{date_str}_digest.json"
    with open(out_path, "w") as f:
        json.dump(digest, f, indent=2)

    print(f"Digest saved to {out_path}")
    print(f"Title: {digest['title']}")
    print(f"Sections: {len(digest['sections'])}")


if __name__ == "__main__":
    main()
