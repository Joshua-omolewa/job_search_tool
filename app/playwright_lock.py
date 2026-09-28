"""A single shared lock for every Playwright-based fetcher in this
project (ats_clients.fetch_uber, aggregator_clients.fetch_indeed).

Playwright's sync API is not designed for concurrent use from multiple
threads in the same process — confirmed live 2026-09-28: once
app/main.py started fetching companies/aggregators via a thread pool,
a run that included both fetch_uber (companies.yaml) and fetch_indeed
(aggregators.yaml, x2 entries) got visibly stuck — only one worker
thread doing real work at a time despite a pool of 10, consistent with
Playwright's sync driver serializing/contending across threads rather
than running them truly in parallel. Every Playwright-based fetcher
should acquire PLAYWRIGHT_LOCK before opening a `sync_playwright()`
context and hold it for the fetch's full duration — this confines
Playwright usage to one browser session at a time, globally, while
every other (non-Playwright) fetcher in the thread pool still runs
fully concurrently.
"""
import threading

PLAYWRIGHT_LOCK = threading.Lock()
