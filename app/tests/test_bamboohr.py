"""Tests for ats_clients.fetch_bamboohr — the public, unauthenticated JSON
API backing every BambooHR customer's standalone careers site
({slug}.bamboohr.com/careers). No real network calls: mocks httpx.get for
both the list endpoint (/careers/list) and the per-job detail endpoint
(/careers/{id}/detail).

The response shapes here were copied verbatim from live 2026-09-26 requests
against explorance.bamboohr.com and clearrisk.bamboohr.com — the two
companies already in companies.yaml under ats: bamboohr.

filters.title_is_relevant/location_is_allowed are patched directly rather
than relying on filters.yaml's live content, same reasoning as test_workday.py.

Run with: python -m pytest app/tests/test_bamboohr.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

SLUG = "acme"
BASE = "https://acme.bamboohr.com/careers"


def _list_resp(postings):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"meta": {"totalCount": len(postings)}, "result": postings})
    return resp


def _detail_resp(description="", date_posted=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    job_opening = {"description": description}
    if date_posted is not None:
        job_opening["datePosted"] = date_posted
    resp.json = MagicMock(return_value={"meta": {}, "result": {"jobOpening": job_opening}})
    return resp


def _posting(id="1", title="Software Engineer", city="Montreal", state="Quebec"):
    return {"id": id, "jobOpeningName": title, "location": {"city": city, "state": state}}


def test_basic_parsing_no_gating():
    posting = _posting()
    with patch("httpx.get", return_value=_list_resp([posting])) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)

    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Software Engineer"
    assert j["location"] == "Montreal, Quebec"
    assert j["url"] == f"{BASE}/1"
    assert j["posted_at"] is None
    assert j["description"] == ""

    mock_get.assert_called_once_with(
        f"{BASE}/list",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    print("fetch_bamboohr: basic parsing + list request shape — OK")


def test_no_pagination_single_response_used_directly():
    postings = [_posting(id=str(i)) for i in range(30)]
    with patch("httpx.get", return_value=_list_resp(postings)) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)
    assert len(jobs) == 30
    assert mock_get.call_count == 1  # no offset/limit pagination for this ATS
    print("fetch_bamboohr: single list response covers all postings, no pagination — OK")


def test_detail_fetch_gated_to_promising_postings():
    promising = _posting(id="1", title="Software Engineer", city="Montreal", state="Quebec")
    not_promising_title = _posting(id="2", title="Marketing Manager", city="Montreal", state="Quebec")
    not_promising_location = _posting(id="3", title="Software Engineer", city="Amman", state=None)

    def title_ok(title):
        return "Engineer" in title

    def location_ok(loc):
        return "Montreal" in loc

    with patch("httpx.get", side_effect=[
        _list_resp([promising, not_promising_title, not_promising_location]),
        _detail_resp("Full JD text", date_posted="2026-09-01"),
    ]) as mock_get, \
         patch("app.filters.title_is_relevant", side_effect=title_ok), \
         patch("app.filters.location_is_allowed", side_effect=location_ok):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)

    assert mock_get.call_count == 2  # 1 list call + exactly 1 detail call
    mock_get.assert_called_with(
        f"{BASE}/1/detail",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    by_id = {j["url"].rsplit("/", 1)[-1]: j for j in jobs}
    assert by_id["1"]["description"] == "Full JD text"
    assert by_id["1"]["posted_at"] == "2026-09-01"
    assert by_id["2"]["description"] == ""
    assert by_id["3"]["description"] == ""
    print("fetch_bamboohr: full-description fetch gated to promising postings only — OK")


def test_detail_fetch_failure_degrades_gracefully():
    posting = _posting()
    with patch("httpx.get", side_effect=[_list_resp([posting]), Exception("500 server error")]), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)
    assert jobs[0]["description"] == ""
    assert jobs[0]["posted_at"] is None
    print("fetch_bamboohr: detail-fetch failure -> empty description, no crash — OK")


def test_missing_id_skipped():
    no_id = {"jobOpeningName": "Ghost Posting", "location": {"city": "Montreal", "state": "Quebec"}}
    with patch("httpx.get", return_value=_list_resp([no_id])), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)
    assert jobs == []
    print("fetch_bamboohr: posting with no id skipped, not stored with url='' — OK")


def test_missing_location_fields_handled():
    posting = _posting(city=None, state=None)
    with patch("httpx.get", return_value=_list_resp([posting])), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_bamboohr("Acme", SLUG)
    assert jobs[0]["location"] == ""
    print("fetch_bamboohr: missing city/state -> empty location string, no crash — OK")


def test_wired_into_fetchers_and_fetch_company():
    assert ats_clients.FETCHERS["bamboohr"] is ats_clients.fetch_bamboohr
    with patch("httpx.get", return_value=_list_resp([])):
        jobs = ats_clients.fetch_company({"name": "Acme", "ats": "bamboohr", "slug": SLUG})
    assert jobs == []
    print("FETCHERS/fetch_company: bamboohr wired in correctly — OK")
