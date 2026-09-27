"""Tests for aggregator_clients.py's fetch_linkedin and fetch_indeed.

fetch_linkedin is pure httpx + regex, so it's fully mockable — fixture HTML
below is trimmed from a live 2026-09-27 request against LinkedIn's public
Guest API (jobs-guest/jobs/api/seeMoreJobPostings/search) for "data
engineer" / Canada, confirmed to return real results (Data Engineer -
Snowflake @ Propel, Lead Data Engineer @ Nasdaq, etc.).

fetch_indeed drives a real headless Chromium via Playwright — like
fetch_via_browser elsewhere in this module, that's not practical to mock
meaningfully with unittest.mock (Playwright's fluent locator API), so
per that same established precedent this only tests the "playwright not
installed -> [] , no crash" contract via mocking; the actual scraping
logic was verified live (16 real Canada "data engineer" postings from
ca.indeed.com, correct title/company/location/salary, full description
fetched via the gated click-through for title/location matches).

Run with: python -m pytest app/tests/test_linkedin_indeed.py
"""
import sys
from unittest.mock import patch, MagicMock

from app import aggregator_clients as ac

# Trimmed from a real LinkedIn Guest API response (2026-09-27).
_LINKEDIN_PAGE_HTML = """<!DOCTYPE html>
<li>
<div class="base-card relative w-full base-search-card base-search-card--link job-search-card" data-entity-urn="urn:li:jobPosting:1111">
<a class="base-card__full-link" href="https://ca.linkedin.com/jobs/view/data-engineer-snowflake-full-time-at-propel-1111?position=1&amp;pageNum=0">
<span class="sr-only">Data Engineer - Snowflake - Full Time</span>
</a>
<h3 class="base-search-card__title">Data Engineer - Snowflake - Full Time</h3>
<h4 class="base-search-card__subtitle">
<a class="hidden-nested-link" href="https://ca.linkedin.com/company/propel">Propel</a>
</h4>
<span class="job-search-card__location">Toronto, Ontario, Canada</span>
<time class="job-search-card__listdate" datetime="2026-09-23">2 days ago</time>
</div>
</li>
<li>
<div class="base-card relative w-full base-search-card base-search-card--link job-search-card" data-entity-urn="urn:li:jobPosting:2222">
<a class="base-card__full-link" href="https://ca.linkedin.com/jobs/view/lead-data-engineer-at-nasdaq-2222?position=2&amp;pageNum=0">
<span class="sr-only">Lead Data Engineer</span>
</a>
<h3 class="base-search-card__title">Lead Data Engineer</h3>
<h4 class="base-search-card__subtitle">
<a class="hidden-nested-link" href="https://ca.linkedin.com/company/nasdaq">Nasdaq</a>
</h4>
<span class="job-search-card__location">Toronto, Ontario, Canada</span>
<time class="job-search-card__listdate" datetime="2026-09-24">1 day ago</time>
</div>
</li>
"""

# Trimmed from a real LinkedIn job page (2026-09-27) — same nesting depth
# as the real thing: a "description__text description__text--rich" div
# wrapping a "show-more-less-html" section wrapping the actual paragraphs,
# followed by the "description__job-criteria-list" that always comes next.
_LINKEDIN_JOB_HTML = """<div class="description__text description__text--rich">
<section class="show-more-less-html" data-max-lines="5">
<div class="show-more-less-html__markup show-more-less-html__markup--clamp-after-5">
<p><strong>About the Role</strong></p><p>We are looking for a Snowflake Data Engineer to build pipelines.</p>
</div>
</section>
</div>
<ul class="description__job-criteria-list">
<li class="description__job-criteria-item">Seniority level</li>
</ul>"""


def _resp(text):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = text
    return resp


def test_linkedin_basic_parsing():
    with patch("httpx.get", return_value=_resp(_LINKEDIN_PAGE_HTML)), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ac.fetch_linkedin({"keywords": "data engineer", "location": "Canada", "max_pages": 1})
    assert len(jobs) == 2
    j = jobs[0]
    assert j["company"] == "Propel"
    assert j["title"] == "Data Engineer - Snowflake - Full Time"
    assert j["location"] == "Toronto, Ontario, Canada"
    assert j["posted_at"] == "2026-09-23"
    assert j["url"] == "https://ca.linkedin.com/jobs/view/data-engineer-snowflake-full-time-at-propel-1111"
    assert j["description"] == ""  # not gated in (title_is_relevant mocked False)
    assert j["salary"] is None  # LinkedIn never exposes salary, always None
    print("fetch_linkedin: basic parsing, URL query string stripped — OK")


def test_linkedin_pagination_stops_on_empty_page():
    with patch("httpx.get", side_effect=[_resp(_LINKEDIN_PAGE_HTML), _resp("<!DOCTYPE html>\n")]) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=False), \
         patch("time.sleep"):  # don't actually sleep 1.5s between pages in tests
        jobs = ac.fetch_linkedin({"keywords": "data engineer", "location": "Canada", "max_pages": 3})
    assert len(jobs) == 2  # only page 1 had results
    assert mock_get.call_count == 2  # page 2 came back empty -> stopped, never tried page 3
    print("fetch_linkedin: pagination stops on an empty page, doesn't run all max_pages — OK")


def test_linkedin_dedupes_by_url_within_a_run():
    with patch("httpx.get", return_value=_resp(_LINKEDIN_PAGE_HTML)), \
         patch("app.filters.title_is_relevant", return_value=False), \
         patch("time.sleep"):
        jobs = ac.fetch_linkedin({"keywords": "data engineer", "location": "Canada", "max_pages": 2})
    # Same fixture page returned for both requests -> same 2 URLs both times;
    # the second occurrence of each must be dropped within this one fetch.
    assert len(jobs) == 2
    print("fetch_linkedin: same URL seen on a later page isn't duplicated — OK")


def test_linkedin_detail_fetch_gated_to_promising_postings():
    def title_ok(title):
        return "Lead" in title  # only the second card

    with patch("httpx.get", side_effect=[_resp(_LINKEDIN_PAGE_HTML), _resp(_LINKEDIN_JOB_HTML)]) as mock_get, \
         patch("app.filters.title_is_relevant", side_effect=title_ok), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ac.fetch_linkedin({"keywords": "data engineer", "location": "Canada", "max_pages": 1})
    assert mock_get.call_count == 2  # 1 list call + exactly 1 detail call
    by_title = {j["title"]: j for j in jobs}
    assert by_title["Lead Data Engineer"]["description"] != ""
    assert "Snowflake Data Engineer" in by_title["Lead Data Engineer"]["description"]
    assert by_title["Data Engineer - Snowflake - Full Time"]["description"] == ""
    print("fetch_linkedin: full-description fetch gated to promising postings only — OK")


def test_linkedin_detail_fetch_failure_degrades_gracefully():
    with patch("httpx.get", side_effect=[_resp(_LINKEDIN_PAGE_HTML), Exception("500 server error")]), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ac.fetch_linkedin({"keywords": "data engineer", "location": "Canada", "max_pages": 1})
    assert jobs[0]["description"] == ""
    print("fetch_linkedin: detail-fetch failure -> empty description, no crash — OK")


def test_linkedin_no_cards_returns_empty():
    with patch("httpx.get", return_value=_resp("<!DOCTYPE html>\n<div>no results</div>\n")):
        jobs = ac.fetch_linkedin({"keywords": "asdkfjasldkfj", "location": "Nowhere"})
    assert jobs == []
    print("fetch_linkedin: no job cards on the page -> [], no crash — OK")


def test_linkedin_wired_into_fetchers():
    assert ac.FETCHERS["linkedin"] is ac.fetch_linkedin
    with patch("httpx.get", return_value=_resp("<!DOCTYPE html>\n")):
        jobs = ac.fetch_aggregator({"type": "linkedin", "params": {}})
    assert jobs == []
    print("FETCHERS/fetch_aggregator: linkedin wired in correctly — OK")


# --------------------------------------------------------------- fetch_indeed

def test_indeed_missing_playwright_degrades_gracefully():
    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        jobs = ac.fetch_indeed({"query": "data engineer", "location": "Canada"})
    assert jobs == []
    print("fetch_indeed: playwright not installed -> [], no crash — OK")


def test_indeed_wired_into_fetchers():
    assert ac.FETCHERS["indeed"] is ac.fetch_indeed
    print("FETCHERS: indeed wired in correctly — OK")
