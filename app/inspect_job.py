"""Look up a job's full stored record (including JD description) from
data/seen_jobs.sqlite3 — useful for checking why something was included or
excluded without re-fetching it from the ATS.

Usage (run from the repo root):
    python -m app.inspect_job <url>              # full record for one job
    python -m app.inspect_job --rejected [N]     # last N rejected jobs (title + why), default 20
"""
import sys

from app import dedup
from app import filters


def show_one(url: str) -> None:
    with dedup.connect() as conn:
        details = dedup.get_details_by_url(conn, url)
    if details is None:
        print(f"No stored record for {url}")
        return
    for k, v in details.items():
        if k == "description":
            continue
        print(f"{k:16s}: {v}")

    if not details.get("passed_filters"):
        reasons = []
        if not filters.title_is_relevant(details.get("title") or ""):
            reasons.append("title")
        if not filters.location_is_allowed(details.get("location") or ""):
            reasons.append("location")
        if filters.jd_stack_mismatch(details.get("description") or ""):
            reasons.append("stack")
        print(f"filtered out by: {'+'.join(reasons) or '? (check filters.py — none of the three checks tripped)'}")

    print("-" * 60)
    print(details.get("description") or "(no description stored)")


def show_rejected(limit: int = 20) -> None:
    with dedup.connect() as conn:
        cur = conn.execute(
            "SELECT company, title, location, url, description FROM job_details "
            "WHERE passed_filters = 0 ORDER BY fetched_at DESC LIMIT ?",
            (limit,),
        )
        rows = cur.fetchall()

    for company, title, location, url, description in rows:
        reasons = []
        if not filters.title_is_relevant(title):
            reasons.append("title")
        if not filters.location_is_allowed(location):
            reasons.append("location")
        if filters.jd_stack_mismatch(description or ""):
            reasons.append("stack")
        print(f"[{'+'.join(reasons) or '?':16s}] {company:20s} | {title[:50]:50s} | {location}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    if sys.argv[1] == "--rejected":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        show_rejected(n)
    else:
        show_one(sys.argv[1])