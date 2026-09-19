"""One-off backfill: re-clean already-stored job_details.description rows
that were scraped as plain page text (Adzuna generic-scrape/browser
fallback, see aggregator_clients.py's _html_page_to_text/fetch_via_browser)
before _collapse_whitespace existed — they're full of blank lines from
every stripped block-level tag (nav widgets, sidebars, etc.).

Only touches descriptions that don't already contain real HTML tags, so
Greenhouse/Lever/Remotive HTML content is left alone — same detection the
frontend's formatDescription() uses (see web/lib/formatDescription.ts):
if it looks like real HTML, leave it; otherwise it's plain text, run it
through _collapse_whitespace.

BACK UP data/seen_jobs.sqlite3 before running this for real — it mutates
job_details.description in place, one UPDATE per changed row.

Usage (run from the repo root):
    python -m app.scripts.clean_scraped_descriptions --dry-run   # preview count/sample only
    python -m app.scripts.clean_scraped_descriptions             # apply
"""
import argparse
import re

from app import dedup
from app.aggregator_clients import _collapse_whitespace

_HTML_TAG_RE = re.compile(r"<[a-z][\s\S]*?>", re.IGNORECASE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="show what would change, don't write")
    args = parser.parse_args()

    with dedup.connect() as conn:
        rows = conn.execute("SELECT url, description FROM job_details WHERE description IS NOT NULL AND description != ''").fetchall()

        changed = 0
        for url, description in rows:
            if _HTML_TAG_RE.search(description):
                continue  # real HTML — not what this backfill is for
            cleaned = _collapse_whitespace(description)
            if cleaned == description:
                continue
            changed += 1
            if changed <= 3:
                print(f"--- {url} ---")
                print(f"before ({len(description)} chars): {description[:150]!r}")
                print(f"after  ({len(cleaned)} chars): {cleaned[:150]!r}")
            if not args.dry_run:
                conn.execute("UPDATE job_details SET description = ? WHERE url = ?", (cleaned, url))

        if not args.dry_run:
            conn.commit()

    print(f"\n{changed}/{len(rows)} descriptions {'would be' if args.dry_run else ''} cleaned.")
    if args.dry_run:
        print("(dry run — no changes written; drop --dry-run to apply)")


if __name__ == "__main__":
    main()
