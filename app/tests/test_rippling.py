"""Tests for ats_clients.fetch_rippling — Rippling's public, documented Job
Board API (developer.rippling.com/documentation/job-board-api). No real
network calls: mocks httpx.get for both the list endpoint
(board/{slug}/jobs) and the per-job detail endpoint (board/{slug}/jobs/{uuid}).

Response shapes here were copied verbatim from live 2026-09-26 requests
against Unchained, Urban SDK, and GenLogs Corporation's real boards.

filters.title_is_relevant/location_is_allowed are patched directly rather
than relying on filters.yaml's live content, same reasoning as test_workday.py.

Run with: python -m pytest app/tests/test_rippling.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

SLUG = "acme"
BASE = f"https://api.rippling.com/platform/api/ats/v1/board/{SLUG}"


def _list_resp(postings):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=postings)
    return resp


def _detail_resp(description_company="", description_role="", created_on=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={
        "description": {"company": description_company, "role": description_role},
        "createdOn": created_on,
    })
    return resp


def _row(uuid="1", name="Software Engineer", location="Austin, TX"):
    return {
        "uuid": uuid, "name": name,
        "url": f"https://ats.rippling.com/{SLUG}/jobs/{uuid}",
        "workLocation": {"label": location, "id": location},
    }


def test_basic_parsing_no_gating():
    row = _row()
    with patch("httpx.get", return_value=_list_resp([row])) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)

    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Software Engineer"
    assert j["location"] == "Austin, TX"
    assert j["url"] == f"https://ats.rippling.com/{SLUG}/jobs/1"
    assert j["posted_at"] is None
    assert j["description"] == ""

    mock_get.assert_called_once_with(
        f"{BASE}/jobs",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    print("fetch_rippling: basic parsing + list request shape — OK")


def test_multi_location_rows_grouped_by_uuid():
    # Rippling lists one row per (job, work-location) pair — same uuid
    # repeats once per location — rather than one row per job.
    rows = [
        _row(uuid="1", name="Data Engineer", location="Austin, TX"),
        _row(uuid="1", name="Data Engineer", location="Remote (United States)"),
    ]
    with patch("httpx.get", return_value=_list_resp(rows)), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)

    assert len(jobs) == 1  # grouped into a single job, not two
    assert jobs[0]["location"] == "Austin, TX, Remote (United States)"
    print("fetch_rippling: multi-location rows grouped back into one job — OK")


def test_detail_fetch_gated_to_promising_postings():
    promising = _row(uuid="1", name="Software Engineer", location="Austin, TX")
    not_promising_title = _row(uuid="2", name="Marketing Manager", location="Austin, TX")
    not_promising_location = _row(uuid="3", name="Software Engineer", location="Manila, PH")

    def title_ok(title):
        return "Engineer" in title

    def location_ok(loc):
        return "Austin" in loc

    with patch("httpx.get", side_effect=[
        _list_resp([promising, not_promising_title, not_promising_location]),
        _detail_resp("About Acme.", "Build things.", created_on="2026-09-01T00:00:00Z"),
    ]) as mock_get, \
         patch("app.filters.title_is_relevant", side_effect=title_ok), \
         patch("app.filters.location_is_allowed", side_effect=location_ok):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)

    assert mock_get.call_count == 2  # 1 list call + exactly 1 detail call
    mock_get.assert_called_with(
        f"{BASE}/jobs/1",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    by_uuid = {j["url"].rsplit("/", 1)[-1]: j for j in jobs}
    assert by_uuid["1"]["description"] == "About Acme.\n\nBuild things."
    assert by_uuid["1"]["posted_at"] == "2026-09-01T00:00:00Z"
    assert by_uuid["2"]["description"] == ""
    assert by_uuid["3"]["description"] == ""
    print("fetch_rippling: full-description fetch gated to promising postings only — OK")


def test_detail_fetch_failure_degrades_gracefully():
    row = _row()
    with patch("httpx.get", side_effect=[_list_resp([row]), Exception("500 server error")]), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)
    assert jobs[0]["description"] == ""
    assert jobs[0]["posted_at"] is None
    print("fetch_rippling: detail-fetch failure -> empty description, no crash — OK")


def test_missing_url_skipped():
    row = _row()
    row["url"] = ""
    with patch("httpx.get", return_value=_list_resp([row])), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)
    assert jobs == []
    print("fetch_rippling: row with no url skipped, not stored with url='' — OK")


def test_non_list_response_treated_as_no_jobs():
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"error_code": "RESOURCE_NOT_FOUND", "message": "Job Board not found"})
    with patch("httpx.get", return_value=resp):
        jobs = ats_clients.fetch_rippling("Acme", SLUG)
    assert jobs == []
    print("fetch_rippling: error-object response (wrong slug) -> no jobs, no crash — OK")


def test_wired_into_fetchers_and_fetch_company():
    assert ats_clients.FETCHERS["rippling"] is ats_clients.fetch_rippling
    with patch("httpx.get", return_value=_list_resp([])):
        jobs = ats_clients.fetch_company({"name": "Acme", "ats": "rippling", "slug": SLUG})
    assert jobs == []
    print("FETCHERS/fetch_company: rippling wired in correctly — OK")
