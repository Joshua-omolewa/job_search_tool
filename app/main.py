"""MVP orchestrator: fetch -> filter -> dedup -> write candidates.csv

Pulls from two kinds of sources:
  - companies.yaml   — one ATS board per known company (precise, no noise)
  - aggregators.yaml — keyword+location search across many employers at
                        once (broader reach, more noise — filters.py earns
                        its keep here)

Deliberately stops BEFORE the AI evaluation step for this first pass, so
you can eyeball the filtering quality on real data before spending any
Claude Haiku calls. Wiring in the AI scoring step (ai_evaluate.py) is the
next increment once this looks right.

Usage (run from the repo root):
    python -m app.main                       # everything: companies + aggregators
    python -m app.main --company affirm      # just one company, for debugging
    python -m app.main --skip-aggregators    # companies.yaml only
    python -m app.main --skip-companies      # aggregators.yaml only
    python -m app.main --workers 20          # more/fewer concurrent fetches (default 10)

Or `make run` for an interactive prompt instead of remembering flags.

Company/aggregator FETCHES run concurrently (a thread pool, since every
fetcher here is I/O-bound network calls, not CPU-bound work) — with 400+
companies in companies.yaml, one at a time was the actual bottleneck on a
full run (tens of minutes). DB writes (dedup + job_details, via
process_jobs) stay strictly single-threaded on the main thread using the
one shared sqlite3 connection from dedup.connect() — sqlite3.Connection
objects aren't safe to share across threads, so only the network fetch
itself (ats_clients.fetch_company / aggregator_clients.fetch_aggregator)
runs in a worker thread; each one's result is handed back to the main
thread before touching the DB. Progress lines print as each fetch
completes, not in companies.yaml's file order — expected with
concurrency, not a bug.
"""
import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yaml
from dotenv import load_dotenv

from app import aggregator_clients
from app import ats_clients
from app import dedup
from app import discover_companies
from app import filters
load_dotenv()
OUTPUT_CSV = Path("data/candidates.csv")
DEFAULT_WORKERS = 10


def load_yaml_list(path: str, key: str) -> list[dict]:
    with open(path) as f:
        return yaml.safe_load(f)[key]


def process_jobs(jobs: list[dict], conn) -> list[dict]:
    """Apply filters + dedup to a batch of jobs from one source, marking
    every newly-seen job (whether it passed the content filters or not) so
    we never re-fetch/re-consider it on a future run. The full record
    (including the JD description) is always saved to job_details — see
    dedup.save_details — so candidates.csv can stay lean and human-readable
    while the description is still queryable later."""
    kept = []
    for job in jobs:
        if not dedup.is_new(conn, job):
            continue
        dedup.mark_seen(conn, job)
        passed = filters.passes_filters(job)
        dedup.save_details(conn, job, passed)
        if passed:
            kept.append(job)
    return kept


def _fetch_source(source: dict, fetch_fn) -> tuple[dict, list[dict] | None, Exception | None]:
    """Runs in a worker thread — network I/O only, no DB access (that
    stays on the main thread, see `run`). Exceptions are returned rather
    than raised so one source's failure can't take down the whole pool or
    get silently swallowed by ThreadPoolExecutor."""
    try:
        return source, fetch_fn(source), None
    except Exception as e:
        return source, None, e


def _run_sources(sources: list[dict], fetch_fn, conn, workers: int) -> tuple[int, list[dict]]:
    fetched = 0
    candidates = []
    if not sources:
        return fetched, candidates

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_fetch_source, source, fetch_fn) for source in sources]
        for future in as_completed(futures):
            source, jobs, err = future.result()
            if err is not None:
                print(f"[WARN] {source['name']}: fetch failed — {err}", file=sys.stderr)
                continue
            fetched += len(jobs)
            # Dedup/job_details writes happen here, back on the main
            # thread, one source's results at a time — `conn` (a
            # sqlite3.Connection) is never touched from a worker thread.
            kept = process_jobs(jobs, conn)
            candidates.extend(kept)
            print(f"{source['name']:40s} fetched={len(jobs):4d}  new_candidates={len(kept):3d}")
    return fetched, candidates


def run(companies: list[dict], aggregators: list[dict], workers: int = DEFAULT_WORKERS) -> list[dict]:
    all_fetched = 0
    all_candidates = []

    with dedup.connect() as conn:
        fetched, candidates = _run_sources(companies, ats_clients.fetch_company, conn, workers)
        all_fetched += fetched
        all_candidates.extend(candidates)

        fetched, candidates = _run_sources(aggregators, aggregator_clients.fetch_aggregator, conn, workers)
        all_fetched += fetched
        all_candidates.extend(candidates)

    print(f"\nTotal fetched: {all_fetched}  |  Total new candidates after filters+dedup: {len(all_candidates)}")
    return all_candidates


CSV_COLUMNS = ["company", "title", "location", "url", "posted_at"]


def write_csv(candidates: list[dict], path: Path = OUTPUT_CSV) -> None:
    """Writes only CSV_COLUMNS, explicitly — deliberately NOT the whole job
    dict. `description` (and anything else added to the job schema later)
    lives in data/seen_jobs.sqlite3's job_details table instead; dumping a
    multi-KB JD into a CSV cell makes the file unreadable in Excel/Sheets."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = path.exists()
    with open(path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        if not file_exists:
            writer.writeheader()
        for c in candidates:
            writer.writerow(c)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", help="Only run a single company slug, for debugging")
    parser.add_argument("--skip-aggregators", action="store_true")
    parser.add_argument("--skip-companies", action="store_true")
    parser.add_argument("--skip-discovery", action="store_true",
                         help="Don't auto-append newly-resolved Greenhouse/Lever companies to companies.yaml")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                         help=f"Concurrent fetches in flight at once (default {DEFAULT_WORKERS})")
    args = parser.parse_args()

    companies = [] if args.skip_companies else load_yaml_list("companies.yaml", "companies")
    aggregators = [] if args.skip_aggregators else load_yaml_list("aggregators.yaml", "aggregators")

    if args.company:
        companies = [c for c in companies if c["slug"] == args.company]
        aggregators = []
        if not companies:
            sys.exit(f"No company with slug '{args.company}' in companies.yaml")

    candidates = run(companies, aggregators, workers=args.workers)
    write_csv(candidates)
    print(f"Wrote {len(candidates)} new candidates to {OUTPUT_CSV}")

    # Any Adzuna result whose full JD we fetched via a real Greenhouse/Lever
    # API call (see aggregator_clients.fetch_full_description) is a
    # verified-working (ats, slug) — worth tracking directly going forward
    # so future runs get that company's WHOLE board, not just whatever
    # Adzuna happened to surface this run.
    if not args.skip_discovery:
        added = discover_companies.append_new_companies(aggregator_clients.DISCOVERED_COMPANIES)
        if not added:
            print(f"No new companies were added to companies.yaml")
        if added:
            print(f"\nDiscovered {len(added)} new compan{'y' if len(added) == 1 else 'ies'} "
                  f"via Adzuna — appended to companies.yaml:")
            for c in added:
                print(f"  + {c['name']} ({c['ats']}: {c['slug']})")