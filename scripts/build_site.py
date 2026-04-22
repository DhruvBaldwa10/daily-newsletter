#!/usr/bin/env python3
"""Build static HTML site from digest JSON files."""

import json
from datetime import datetime
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "topics.yaml"
DATA_DIR = ROOT / "data"
DOCS_DIR = ROOT / "docs"
TEMPLATES_DIR = ROOT / "templates"


def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def build_digest_page(digest_data, env, newsletter_title):
    template = env.get_template("digest.html")
    date_obj = datetime.strptime(digest_data["date"], "%Y-%m-%d")
    date_formatted = date_obj.strftime("%A, %B %d, %Y")

    html = template.render(
        newsletter_title=newsletter_title,
        title=digest_data.get("title", f"Digest for {digest_data['date']}"),
        subtitle=digest_data.get("subtitle", ""),
        date_formatted=date_formatted,
        sections=digest_data.get("sections", []),
    )

    out_path = DOCS_DIR / "digests" / f"{digest_data['date']}.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    return out_path


def build_index(env, newsletter_title, manifest):
    template = env.get_template("index.html")
    latest_date = manifest[0]["date"] if manifest else None

    html = template.render(
        newsletter_title=newsletter_title,
        latest_date=latest_date,
    )

    with open(DOCS_DIR / "index.html", "w") as f:
        f.write(html)


def build_manifest(digests):
    # Load existing manifest so we don't lose entries when data/ is incomplete (e.g. in CI)
    manifest_path = DOCS_DIR / "manifest.json"
    existing = {}
    if manifest_path.exists():
        with open(manifest_path) as f:
            for entry in json.load(f):
                existing[entry["date"]] = entry

    # New digests overwrite existing entries for the same date
    for d in digests:
        existing[d["date"]] = {
            "date": d["date"],
            "title": d.get("title", ""),
        }

    manifest = sorted(existing.values(), key=lambda x: x["date"], reverse=True)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    return manifest


def main():
    config = load_config()
    newsletter_title = config.get("newsletter", {}).get("title", "Daily AI Digest")

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))

    digest_files = sorted(DATA_DIR.glob("*_digest.json"))
    if not digest_files:
        print("No digest files found. Run generate_digest.py first.")
        return

    digests = []
    for path in digest_files:
        with open(path) as f:
            digests.append(json.load(f))

    print(f"Building site from {len(digests)} digest(s)...")

    for digest in digests:
        out = build_digest_page(digest, env, newsletter_title)
        print(f"  Built {out}")

    manifest = build_manifest(digests)
    build_index(env, newsletter_title, manifest)
    print(f"  Built index.html and manifest.json ({len(manifest)} entries)")
    print("Done!")


if __name__ == "__main__":
    main()
