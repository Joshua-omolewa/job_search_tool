"""Tests for ats_clients.fetch_gem — the undocumented GraphQL batch API
(jobs.gem.com/api/public/graphql/batch) that Gem's public careers page's
own React app calls, reverse-engineered from that app's minified JS
bundles since Gem's DOCUMENTED Job Board API is a separate, authenticated
product. No real network calls: mocks httpx.post for both the list query
(JobBoardList) and the per-job detail query (ExternalJobPosting).

Response shapes here were copied verbatim from live 2026-09-26 requests
against jobs.gem.com/supio and jobs.gem.com/detections-ai.

filters.title_is_relevant/location_is_allowed are patched directly rather
than relying on filters.yaml's live content, same reasoning as test_workday.py.

Run with: python -m pytest app/tests/test_gem.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

SLUG = "acme"
URL = "https://jobs.gem.com/api/public/graphql/batch"


def _batch_resp(data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=[{"data": data}])
    return resp


def _list_data(postings):
    return {"oatsExternalJobPostings": {"jobPostings": postings}}


def _posting(ext_id="ext1", title="Software Engineer", locations=None):
    return {"extId": ext_id, "title": title, "locations": locations or [{"name": "Seattle"}]}


def _detail_data(description="", published_ts=None):
    return {"oatsExternalJobPosting": {"descriptionHtml": description, "firstPublishedTsSec": published_ts}}


def test_basic_parsing_no_gating():
    posting = _posting()
    with patch("httpx.post", return_value=_batch_resp(_list_data([posting]))) as mock_post, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_gem("Acme", SLUG)

    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Software Engineer"
    assert j["location"] == "Seattle"
    assert j["url"] == f"https://jobs.gem.com/{SLUG}/ext1"
    assert j["posted_at"] is None
    assert j["description"] == ""

    call = mock_post.call_args
    assert call.args[0] == URL
    body = call.kwargs["json"][0]
    assert body["operationName"] == "JobBoardList"
    assert body["variables"] == {"boardId": SLUG}
    print("fetch_gem: basic parsing + request shape — OK")


def test_multi_location_joined():
    posting = _posting(locations=[{"name": "Seattle"}, {"name": "San Francisco"}])
    with patch("httpx.post", return_value=_batch_resp(_list_data([posting]))), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_gem("Acme", SLUG)
    assert jobs[0]["location"] == "Seattle, San Francisco"
    print("fetch_gem: multiple locations joined — OK")


def test_detail_fetch_gated_to_promising_postings():
    promising = _posting(ext_id="1", title="Data Engineer", locations=[{"name": "Remote"}])
    not_promising_title = _posting(ext_id="2", title="Marketing Manager", locations=[{"name": "Remote"}])
    not_promising_location = _posting(ext_id="3", title="Data Engineer", locations=[{"name": "Manila"}])

    def title_ok(title):
        return "Engineer" in title

    def location_ok(loc):
        return "Remote" in loc

    with patch("httpx.post", side_effect=[
        _batch_resp(_list_data([promising, not_promising_title, not_promising_location])),
        _batch_resp(_detail_data("Full JD text", published_ts=1735689600)),
    ]) as mock_post, \
         patch("app.filters.title_is_relevant", side_effect=title_ok), \
         patch("app.filters.location_is_allowed", side_effect=location_ok):
        jobs = ats_clients.fetch_gem("Acme", SLUG)

    assert mock_post.call_count == 2  # 1 list call + exactly 1 detail call
    detail_body = mock_post.call_args.kwargs["json"][0]
    assert detail_body["operationName"] == "ExternalJobPosting"
    assert detail_body["variables"] == {"boardId": SLUG, "extId": "1"}

    by_ext_id = {j["url"].rsplit("/", 1)[-1]: j for j in jobs}
    assert by_ext_id["1"]["description"] == "Full JD text"
    assert by_ext_id["1"]["posted_at"] == "2025-01-01T00:00:00+00:00"
    assert by_ext_id["2"]["description"] == ""
    assert by_ext_id["3"]["description"] == ""
    print("fetch_gem: full-description fetch gated to promising postings only — OK")


def test_detail_fetch_failure_degrades_gracefully():
    posting = _posting()
    with patch("httpx.post", side_effect=[_batch_resp(_list_data([posting])), Exception("500 server error")]), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_gem("Acme", SLUG)
    assert jobs[0]["description"] == ""
    assert jobs[0]["posted_at"] is None
    print("fetch_gem: detail-fetch failure -> empty description, no crash — OK")


def test_missing_ext_id_skipped():
    no_ext_id = {"extId": None, "title": "Ghost Posting", "locations": []}
    with patch("httpx.post", return_value=_batch_resp(_list_data([no_ext_id]))), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_gem("Acme", SLUG)
    assert jobs == []
    print("fetch_gem: posting with no extId skipped, not stored with url='' — OK")


def test_wired_into_fetchers_and_fetch_company():
    assert ats_clients.FETCHERS["gem"] is ats_clients.fetch_gem
    with patch("httpx.post", return_value=_batch_resp(_list_data([]))):
        jobs = ats_clients.fetch_company({"name": "Acme", "ats": "gem", "slug": SLUG})
    assert jobs == []
    print("FETCHERS/fetch_company: gem wired in correctly — OK")
