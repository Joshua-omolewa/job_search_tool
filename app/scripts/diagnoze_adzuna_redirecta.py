"""Diagnostic, not part of the pipeline: are Adzuna's redirect_url values
for YOUR actual queries tracking links that go on to an external ATS, or
do they stay on adzuna.* (self-hosted, no external source)?

Why this exists (2026-08-12): aggregator_clients.fetch_full_description
assumes some redirect_urls resolve to Greenhouse/Lever and tries their
single-job APIs when that happens — but that logic has only ever been
exercised against mocks, never real data. Two real examples from an actual
run both turned out to be adzuna.ca/details/... pages that never redirect
anywhere (403, confirmed). Adzuna's own docs don't document what
redirect_url actually resolves to, and the one third-party claim found
during research ("routes through to the original listing") isn't
verifiable without just... checking. This script checks.

Doesn't touch seen_jobs.sqlite3, doesn't write candidates.csv, doesn't
call fetch_full_description — just samples raw search results and reports
where redirect_url actually ends up. Run this once, read the summary, and
we'll know whether the Greenhouse/Lever path is worth keeping as primary
or is dead code that only the self-hosted fallback ever actually exercises.

Usage (run from the repo root):
    export ADZUNA_APP_ID=... ADZUNA_APP_KEY=...
    python -m app.scripts.diagnoze_adzuna_redirecta                  # default sample
    python -m app.scripts.diagnoze_adzuna_redirecta --sample 50       # bigger sample
"""
import argparse
import os
import re
from collections import Counter
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv

load_dotenv()
USER_AGENT = "job-search-pipeline/0.1 (personal use)"
TIMEOUT = 10.0


def fetch_sample(sample_size: int) -> list[dict]:
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise SystemExit("ADZUNA_APP_ID / ADZUNA_APP_KEY env vars not set.")

    results = []
    page = 1
    while len(results) < sample_size:
        resp = httpx.get(
            f"https://api.adzuna.com/v1/api/jobs/ca/search/{page}",
            params={
                "app_id": app_id, "app_key": app_key, "content-type": "application/json",
                "what": "software engineer", "where": "Alberta", "results_per_page": 50,
            },
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        page_results = resp.json().get("results", [])
        if not page_results:
            break
        results.extend(page_results)
        page += 1
        if page > 10:  # hard stop, don't loop forever on a weird response
            break
    return results[:sample_size]


def classify_final_url(redirect_url: str) -> tuple[str, str]:
    """Returns (category, final_url). category is one of:
    'stayed-on-adzuna', 'greenhouse', 'lever', 'ashby', 'workable',
    'other-external', 'request-failed'."""
    try:
        resp = httpx.get(
            redirect_url, headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT, follow_redirects=True,
        )
        final_url = str(resp.url)
        status = resp.status_code
    except Exception as e:
        return "request-failed", f"({type(e).__name__}: {e})"

    host = urlparse(final_url).netloc.lower()
    if "greenhouse.io" in host:
        cat = "greenhouse"
    elif "lever.co" in host:
        cat = "lever"
    elif "ashbyhq.com" in host:
        cat = "ashby"
    elif "workable.com" in host:
        cat = "workable"
    elif re.search(r"adzuna\.", host):
        cat = "stayed-on-adzuna"
    else:
        cat = "other-external"
    return cat, f"{final_url} [HTTP {status}]"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=25, help="How many Adzuna results to check (default 25)")
    args = parser.parse_args()

    print(f"Fetching a sample of {args.sample} Adzuna results...")
    results = fetch_sample(args.sample)
    print(f"Got {len(results)} results. Resolving each redirect_url (this is slow — one request per job)...\n")

    counts = Counter()
    for i, r in enumerate(results, 1):
        redirect_url = r.get("redirect_url", "")
        title = r.get("title", "")[:45]
        company = (r.get("company") or {}).get("display_name", "?")[:25]
        cat, detail = classify_final_url(redirect_url)
        counts[cat] += 1
        print(f"[{i:2d}/{len(results)}] {cat:18s} | {company:25s} | {title:45s} | {detail}")

    print("\n=== Summary ===")
    total = sum(counts.values())
    for cat, n in counts.most_common():
        print(f"  {cat:20s} {n:3d}/{total} ({100 * n / total:.0f}%)")

    if counts["stayed-on-adzuna"] == total:
        print("\nNone of this sample redirected off Adzuna — for THIS query, fetch_full_description's "
              "Greenhouse/Lever path is dead weight and the short-snippet fallback is the real behavior.")
    elif counts["greenhouse"] + counts["lever"] > 0:
        print(f"\n{counts['greenhouse'] + counts['lever']} result(s) DID resolve to Greenhouse/Lever — "
              "the full-JD-fetch path is real for at least part of this query's results.")
    if counts["other-external"] > 0:
        print(f"\n{counts['other-external']} result(s) landed on an ATS we don't have a parser for yet "
              "(check the detail column above for which one — might be worth adding).")


if __name__ == "__main__":
    main()