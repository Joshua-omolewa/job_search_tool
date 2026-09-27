"""Sanity test for filters.py + dedup.py, using real sample data plus the
actual noise patterns found in a live run on 2026-08-11 (Remote
Poland/Spain/Australia leaking through, and non-engineering titles like
Analyst/Manager/Marketing passing because the old filter was exclusion-only).
Run with: python -m app.tests.test_pipeline
"""
import os
from app import filters
from app import dedup

SAMPLE_JOBS = [
    # Should PASS: real fields, Canada-eligible remote, matches title allowlist.
    # "Data Platform" in the title is what clears title_allow_keywords —
    # filters.yaml is tuned for Data Engineer-shaped titles, so a plain
    # "Software Engineer, Backend" title (no data/analytics/etc keyword) no
    # longer passes on its own; the "Software Engineer" phrasing itself is
    # fine, it just needs a data-flavored qualifier alongside it.
    {"company": "Affirm", "title": "Senior Software Engineer, Data Platform (Batch Infrastructure)",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/1111",
     "description": "We use Python, FastAPI and React."},
    # Should FAIL: excluded title keyword (compliance)
    {"company": "Affirm", "title": "Compliance Lead, Canada",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/7788916003",
     "description": ""},
    # Should FAIL: no data-flavored title-allow keyword at all (plain
    # "Software Engineer" doesn't clear title_allow_keywords). NOTE: this
    # is no longer a location-rejection example — "Remote US" is accepted
    # under the current Canada-first + US-acceptable location policy
    # (location_is_allowed("Remote US") is True), so this drops on title
    # alone now; kept as-is since the end result (DROP) is unchanged.
    {"company": "Affirm", "title": "Staff Software Engineer",
     "location": "Remote US", "url": "https://job-boards.greenhouse.io/affirm/jobs/2222",
     "description": ""},
    # Should FAIL: title DOES clear the allowlist ("analytics engineer" is
    # a literal title_allow_keywords entry) but location doesn't — real
    # noise from a 2026-08-11 run, "Remote Poland" used to leak through the
    # old "\bremote\b(?!.*\bus\b)" pattern.
    {"company": "Affirm", "title": "Analytics Engineer II",
     "location": "Remote Poland", "url": "https://job-boards.greenhouse.io/affirm/jobs/7764109003",
     "description": ""},
    # Should FAIL: real noise — title has no engineering keyword at all,
    # old exclusion-only filter had nothing to catch this on.
    {"company": "Affirm", "title": "Marketing Operations Manager",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/9999",
     "description": ""},
    {"company": "Affirm", "title": "Senior Manager, Talent Brand",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/9998",
     "description": ""},
    # Should PASS: real Alberta on-site posting
    {"company": "Snorkel AI", "title": "Software Engineer, Data Platform — Backend",
     "location": "Calgary, AB", "url": "https://job-boards.greenhouse.io/snorkelai/jobs/4911972004",
     "description": "Backend role, Python and Go."},
    # Should FAIL: excluded keyword (nurse)
    {"company": "Some Co", "title": "Occupational Health Nurse",
     "location": "Remote Canada", "url": "https://example.com/jobs/9999",
     "description": ""},
    # Should FAIL: title matches the allowlist (data-flavored) but JD is a
    # stack dealbreaker — Java shop, no Python/data-stack mentioned
    # anywhere. NOTE: deliberately no "Kafka" in this description — it's
    # in the current stack_core list, so mentioning it would rescue this
    # from the dealbreaker check and defeat the point of this fixture.
    {"company": "BigCorp", "title": "Senior Software Engineer, Data Platform",
     "location": "Remote Canada", "url": "https://example.com/jobs/8888",
     "description": "5+ years of Java and Spring Boot required. On-call rotation expected."},
    # Should PASS: title matches, JD mentions Java AND Python — not a hard
    # dealbreaker, worth letting through for the AI step to judge.
    {"company": "DualStackCo", "title": "Backend Engineer, Data Platform",
     "location": "Remote Canada", "url": "https://example.com/jobs/7777",
     "description": "Our platform is a mix of Java services and a newer Python/FastAPI stack."},
    # Duplicate of the first PASS entry by URL -> should be filtered by dedup on 2nd pass
    {"company": "Affirm", "title": "Senior Software Engineer, Data Platform (Batch Infrastructure)",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/1111",
     "description": "We use Python, FastAPI and React."},
    # Repost under a new URL, same company+title -> should be caught by company+title dedup
    {"company": "Affirm", "title": "Senior Software Engineer, Data Platform (Batch Infrastructure)",
     "location": "Remote Canada", "url": "https://job-boards.greenhouse.io/affirm/jobs/3333-repost",
     "description": "We use Python, FastAPI and React."},
    # Should PASS: real Lever sample (AltaML), Edmonton without "Canada"/"AB" in the string
    {"company": "AltaML", "title": "Intermediate Full Stack Software Engineer, Data Platform",
     "location": "Edmonton, Calgary", "url": "https://jobs.lever.co/altaml/22d02404-b9c9-4b77-9448-add55d961444",
     "description": "Python and React experience preferred."},
    # Should PASS: real 2026-08-11 false-positive fix — an unambiguous
    # PRIORITY_TITLE_KEYWORDS match ("data platform engineer") bypasses
    # EXCLUSION_KEYWORDS entirely, so the "marketing" substring elsewhere
    # in the title doesn't trip it.
    {"company": "Warner Music Group", "title": "Data Platform Engineer, Automated Marketing",
     "location": "Alberta, Canada", "url": "https://www.adzuna.ca/details/5702928490",
     "description": "Build automated marketing tooling. Python and React."},
]

TEST_DB = "data/test_seen_jobs.sqlite3"


def main():
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    print("=== Filter results ===")
    filtered = []
    for job in SAMPLE_JOBS:
        ok = filters.passes_filters(job)
        print(f"{'PASS' if ok else 'DROP':5s} | {job['company']:14s} | {job['title'][:50]:50s} | {job['location']}")
        if ok:
            filtered.append(job)

    print(f"\n{len(filtered)}/{len(SAMPLE_JOBS)} passed title+location+stack filters\n")

    print("=== Dedup results (processing filtered jobs in order) ===")
    kept = []
    with dedup.connect(TEST_DB) as conn:
        for job in filtered:
            if dedup.is_new(conn, job):
                dedup.mark_seen(conn, job)
                kept.append(job)
                print(f"NEW  | {job['company']:14s} | {job['title'][:50]:50s} | {job['url']}")
            else:
                print(f"DUPE | {job['company']:14s} | {job['title'][:50]:50s} | {job['url']}")

    print(f"\nFinal candidates after filters+dedup: {len(kept)}")

    # Assertions to make this a real check, not just eyeballing.
    # PASS (7 filtered, 5 unique after dedup): Affirm SWE x3 occurrences
    # -> x1 unique, Snorkel AI Calgary, DualStackCo (mixed stack, not a
    # hard dealbreaker), AltaML, Warner Music Group (priority-keyword
    # exclusion-bypass case).
    # DROP: Compliance Lead, Remote US (no data-flavored title), Remote
    # Poland (title OK, location isn't), Marketing Ops Manager, Senior
    # Manager Talent Brand, Occupational Health Nurse, BigCorp (Java-only
    # dealbreaker).
    assert len(filtered) == 7, f"expected 7 to pass all filters, got {len(filtered)}"
    assert len(kept) == 5, f"expected 5 unique candidates after dedup, got {len(kept)}"

    # Targeted unit checks for the specific bugs found in the 2026-08-11 run.
    assert not filters.location_is_allowed("Remote Poland")
    assert not filters.location_is_allowed("Remote Spain")
    assert not filters.location_is_allowed("Remote Australia")
    assert filters.location_is_allowed("Remote Canada")
    assert filters.location_is_allowed("Calgary, AB")
    assert not filters.title_is_relevant("Marketing Operations Manager")
    assert not filters.title_is_relevant("Senior Manager, Talent Brand")
    assert not filters.title_is_relevant("Compliance Lead, Canada")
    # A plain "Software Engineer" title with no data-flavored qualifier no
    # longer clears title_allow_keywords — filters.yaml is tuned for Data
    # Engineer-shaped titles now. "Software Engineer" phrasing itself is
    # fine (see the PASS fixtures above); it just needs a data qualifier.
    assert not filters.title_is_relevant("Senior Software Engineer, Backend")
    assert filters.title_is_relevant("Senior Software Engineer, Data Platform")
    # "Data Platform Engineer" is a literal PRIORITY_TITLE_KEYWORDS entry,
    # so it bypasses EXCLUSION_KEYWORDS entirely — the "marketing" elsewhere
    # in the title doesn't trip it.
    assert filters.title_is_relevant("Data Platform Engineer, Automated Marketing")
    assert not filters.title_is_relevant("Sales Engineer")  # priority list shouldn't rescue this
    assert filters.jd_stack_mismatch("5+ years of Java and Spring Boot required.")
    assert not filters.jd_stack_mismatch("Java services and a Python/FastAPI stack.")
    assert not filters.jd_stack_mismatch("")  # no JD available -> don't reject on stack alone

    print("\nAll assertions passed.")


if __name__ == "__main__":
    main()