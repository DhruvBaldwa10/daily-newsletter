#!/usr/bin/env python3
"""Generate a themed daily digest from raw fetched data using Claude."""

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

import anthropic
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "topics.yaml"
DATA_DIR = ROOT / "data"

SYSTEM_PROMPT = """You are a senior AI industry analyst writing a daily newsletter digest. Your audience is a technical reader who builds with AI tools daily.

Your task: given today's raw content from Hacker News, Reddit, RSS blogs, and podcasts, synthesize a compelling daily digest.

Rules:
1. Identify 4-6 major themes from the content. Each theme becomes a section.
2. Write each section as a 200-400 word narrative — not a list of links. Tell the story of what happened and why it matters.
3. Cite sources inline using HTML links. Every claim should link to its source.
4. For each section, include at least one compelling quote in a <blockquote> tag — either a direct quote from a source, a key user comment, or a striking line from an article. Always attribute the quote.
5. The FINAL section MUST be titled "What This All Means" — this is a synthesis/takeaways section. Format it as a numbered <ol> list with 3-4 bold takeaway points. Each <li> should start with <strong>Takeaway sentence.</strong> followed by 1-2 sentences of explanation. Wrap this list in a <div class="takeaways"> tag.
6. Give the digest a short, punchy title — MAX 5-6 words. Think newspaper headline, not essay title. Examples: "Agents Break Open Source", "The Memory Wall Hits", "Claude Pushes Back".
7. Write a 2-3 sentence subtitle/hook.
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

    items_text = json.dumps(raw_data["items"], indent=1, default=str)
    # Truncate if too long
    if len(items_text) > 150_000:
        items_text = items_text[:150_000] + "\n... (truncated)"

    user_prompt = f"""Today's date: {date_str}

Topics of interest:
{topics_desc}

Here are today's raw items from various sources:

{items_text}

Now synthesize this into a daily digest following the instructions. Return only valid JSON."""

    print(f"Generating digest for {date_str} via Claude...")
    client = anthropic.Anthropic()
    message = client.messages.create(
        model="claude-sonnet-4-6",
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
            model="claude-sonnet-4-6",
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

    out_path = DATA_DIR / f"{date_str}_digest.json"
    with open(out_path, "w") as f:
        json.dump(digest, f, indent=2)

    print(f"Digest saved to {out_path}")
    print(f"Title: {digest['title']}")
    print(f"Sections: {len(digest['sections'])}")


if __name__ == "__main__":
    main()
