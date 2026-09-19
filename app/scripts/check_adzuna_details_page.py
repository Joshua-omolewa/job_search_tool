"""One-off diagnostic (not part of the pipeline): for a single Adzuna URL,
print what a plain httpx GET sees vs. what fetch_via_browser (real headless
Chromium, waits for the page's own JS to finish loading data) sees —
side by side, so we can settle exactly what's on that page instead of
guessing from memory. Doesn't touch any files, doesn't need ADZUNA_APP_ID/
KEY, just needs a URL.

Usage (run from the repo root):
    python -m app.scripts.check_adzuna_details_page "https://www.adzuna.ca/details/5833571011"
"""
import sys

import httpx

from app import aggregator_clients

USER_AGENT = aggregator_clients.USER_AGENT


def main():
    if len(sys.argv) < 2:
        sys.exit("Usage: python -m app.scripts.check_adzuna_details_page <adzuna details url>")
    url = sys.argv[1]

    print(f"=== Plain httpx GET: {url} ===")
    try:
        resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=20.0, follow_redirects=True)
        print(f"status: {resp.status_code}")
        print(f"final url: {resp.url}")
        print(f"raw HTML length: {len(resp.text)} chars")
        plain_text = aggregator_clients._html_page_to_text(resp.text)
        print(f"extracted text length: {len(plain_text)} chars")
        print("--- first 500 chars of extracted text ---")
        print(plain_text[:500])
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")

    print(f"\n=== fetch_via_browser (real Chromium, waits for JS to finish): {url} ===")
    browser_text = aggregator_clients.fetch_via_browser(url)
    if browser_text is None:
        print("FAILED: got nothing back (playwright missing, page errored, or timed out)")
    else:
        print(f"extracted text length: {len(browser_text)} chars")
        print("--- first 500 chars ---")
        print(browser_text[:500])


if __name__ == "__main__":
    main()