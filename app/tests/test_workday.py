"""Tests for ats_clients.fetch_workday — the public, unauthenticated CXS
API backing *.myworkdayjobs.com career sites. No real network calls: mocks
httpx.post (list endpoint) and httpx.get (per-job detail endpoint).

The list/detail response shapes here were copied verbatim from a live
2026-09-26 request against a real production Workday board (NVIDIA's) —
see the CONFIDENCE NOTE in ats_clients.py's module docstring.

filters.title_is_relevant/location_is_allowed are patched directly rather
than relying on filters.yaml's live content, so these tests don't depend
on (or get broken by) unrelated edits to that config file.

Run with: python -m pytest app/tests/test_workday.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

SLUG = "acme.wd3/AcmeCareers"
BASE = "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/AcmeCareers"


def _list_resp(postings, total=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"total": total, "jobPostings": postings})
    return resp


def _detail_resp(description="", start_date=None, location=None, additional_locations=None):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    info = {"jobDescription": description}
    if start_date is not None:
        info["startDate"] = start_date
    if location is not None:
        info["location"] = location
    if additional_locations is not None:
        info["additionalLocations"] = additional_locations
    resp.json = MagicMock(return_value={"jobPostingInfo": info})
    return resp


def _posting(title="Software Engineer", locations_text="US, CA, Santa Clara", external_path="/job/US-CA/Software-Engineer_JR1"):
    return {"title": title, "locationsText": locations_text, "externalPath": external_path}


def test_basic_parsing_no_gating():
    posting = _posting()
    with patch("httpx.post", return_value=_list_resp([posting])) as mock_post, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Software Engineer"
    assert j["location"] == "US, CA, Santa Clara"
    assert j["url"] == "https://acme.wd3.myworkdayjobs.com/AcmeCareers/job/US-CA/Software-Engineer_JR1"
    assert j["posted_at"] is None
    assert j["description"] == ""

    call = mock_post.call_args
    assert call.args[0] == f"{BASE}/jobs"
    assert call.kwargs["json"] == {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
    print("fetch_workday: basic parsing + request shape — OK")


def test_pagination_via_offset_stops_on_short_page():
    # `total` is left unset (None) here, same as a tenant that doesn't
    # return a usable total — this exercises _fetch_all_workday_postings'
    # sequential-walk fallback, not the concurrent fast path below.
    page1 = [_posting(external_path=f"/job/JR{i}") for i in range(20)]
    page2 = [_posting(external_path="/job/JR20")]

    with patch("httpx.post", side_effect=[_list_resp(page1), _list_resp(page2)]) as mock_post, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert len(jobs) == 21
    assert mock_post.call_count == 2
    assert mock_post.call_args_list[0].kwargs["json"]["offset"] == 0
    assert mock_post.call_args_list[1].kwargs["json"]["offset"] == 20
    print("fetch_workday: pagination via offset (unknown total), stops on short page — OK")


def test_total_aware_pagination_fetches_remaining_pages_concurrently():
    # When the first page returns a real `total` (as Workday's live API
    # always does), _fetch_all_workday_postings computes every remaining
    # offset upfront and fetches them concurrently instead of walking one
    # page at a time. This checks: (1) exactly the right number of requests
    # go out, (2) results land back in the correct offset order despite
    # concurrent fetching, (3) job count/order stays correct end to end.
    total = 45  # 3 full pages (0, 20, 40) -> pages of 20, 20, 5
    pages_by_offset = {
        0: [_posting(external_path=f"/job/JR{i}") for i in range(20)],
        20: [_posting(external_path=f"/job/JR{i}") for i in range(20, 40)],
        40: [_posting(external_path=f"/job/JR{i}") for i in range(40, 45)],
    }

    def post_side_effect(url, json, **kwargs):
        return _list_resp(pages_by_offset[json["offset"]], total=total)

    with patch("httpx.post", side_effect=post_side_effect) as mock_post, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert mock_post.call_count == 3  # one request per page, no more
    assert len(jobs) == 45
    # Order preserved even though offsets 20 and 40 were fetched concurrently.
    assert [j["url"].rsplit("JR", 1)[-1] for j in jobs] == [str(i) for i in range(45)]
    print("fetch_workday: total-aware pagination fetches remaining pages concurrently, in order — OK")


def test_empty_page_stops_immediately():
    with patch("httpx.post", return_value=_list_resp([])) as mock_post:
        jobs = ats_clients.fetch_workday("Acme", SLUG)
    assert jobs == []
    assert mock_post.call_count == 1
    print("fetch_workday: empty first page -> no jobs, single request — OK")


def test_detail_fetch_gated_to_promising_postings():
    promising = _posting(title="Software Engineer", locations_text="Remote", external_path="/job/JR1")
    not_promising_title = _posting(title="Marketing Manager", locations_text="Remote", external_path="/job/JR2")
    not_promising_location = _posting(title="Software Engineer", locations_text="Warsaw, Poland", external_path="/job/JR3")

    def title_ok(title):
        return "Engineer" in title

    def location_ok(loc):
        return "Remote" in loc

    with patch("httpx.post", return_value=_list_resp([promising, not_promising_title, not_promising_location])), \
         patch("httpx.get", return_value=_detail_resp("Full JD text", start_date="2026-09-01")) as mock_get, \
         patch("app.filters.title_is_relevant", side_effect=title_ok), \
         patch("app.filters.location_is_allowed", side_effect=location_ok):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert mock_get.call_count == 1  # only the promising posting triggers a detail fetch
    mock_get.assert_called_with(
        f"{BASE}/job/JR1",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    by_path = {j["url"].rsplit("_", 1)[0]: j for j in jobs}
    promising_job = next(j for j in jobs if j["url"].endswith("JR1"))
    assert promising_job["description"] == "Full JD text"
    assert promising_job["posted_at"] == "2026-09-01"
    not_title_job = next(j for j in jobs if j["url"].endswith("JR2"))
    assert not_title_job["description"] == ""
    assert not_title_job["posted_at"] is None
    not_loc_job = next(j for j in jobs if j["url"].endswith("JR3"))
    assert not_loc_job["description"] == ""
    print("fetch_workday: detail fetch gated to title+location matches — OK")


def test_multi_location_aggregate_resolved_via_detail():
    # "5 Locations" can't be matched against location_allow_patterns at
    # all — must be treated as unknown-not-disqualifying and resolved via
    # the detail endpoint's `location` + `additionalLocations`, not dropped.
    posting = _posting(title="Software Engineer", locations_text="5 Locations", external_path="/job/JR1")

    with patch("httpx.post", return_value=_list_resp([posting])), \
         patch("httpx.get", return_value=_detail_resp(
             "JD", start_date="2026-09-01",
             location="US, CA, Santa Clara",
             additional_locations=["US, NC, Remote", "US, TX, Remote"],
         )) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=False):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert mock_get.call_count == 1
    assert jobs[0]["location"] == "US, CA, Santa Clara, US, NC, Remote, US, TX, Remote"
    print("fetch_workday: aggregate 'N Locations' resolved via detail fetch — OK")


def test_detail_fetch_failure_degrades_gracefully():
    posting = _posting(title="Software Engineer", locations_text="Remote", external_path="/job/JR1")
    with patch("httpx.post", return_value=_list_resp([posting])), \
         patch("httpx.get", side_effect=Exception("500 server error")), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert len(jobs) == 1
    assert jobs[0]["description"] == ""
    assert jobs[0]["posted_at"] is None
    assert jobs[0]["location"] == "Remote"  # falls back to the list-page value
    print("fetch_workday: detail-fetch failure -> empty description, no crash — OK")


def test_missing_external_path_skipped():
    no_path = _posting(external_path="")
    with patch("httpx.post", return_value=_list_resp([no_path])), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_workday("Acme", SLUG)
    assert jobs == []
    print("fetch_workday: posting with no externalPath skipped, not stored with url='' — OK")


def test_concurrent_detail_fetches_assign_to_correct_jobs():
    # Detail fetches for postings that pass gating now run concurrently
    # (see _WORKDAY_DETAIL_WORKERS in ats_clients.py) instead of one at a
    # time, to speed up big boards (NVIDIA's ~2000 postings) where many
    # postings need a detail fetch. This checks that concurrency doesn't
    # scramble which response lands on which job: each of several
    # simultaneously-dispatched detail requests must still end up attached
    # to the job it actually belongs to, keyed by the real external_path
    # httpx.get was called with rather than call order.
    postings = [_posting(title="Software Engineer", locations_text="Remote", external_path=f"/job/JR{i}") for i in range(10)]

    def detail_side_effect(url, **kwargs):
        job_num = url.rsplit("JR", 1)[-1]
        return _detail_resp(f"JD for job {job_num}", start_date=f"2026-09-{int(job_num) + 1:02d}")

    with patch("httpx.post", return_value=_list_resp(postings)), \
         patch("httpx.get", side_effect=detail_side_effect) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_workday("Acme", SLUG)

    assert mock_get.call_count == 10
    for i, job in enumerate(jobs):
        assert job["description"] == f"JD for job {i}"
        assert job["posted_at"] == f"2026-09-{i + 1:02d}"
    print("fetch_workday: concurrent detail fetches still map back to the correct job — OK")


def test_wired_into_fetchers_and_fetch_company():
    assert ats_clients.FETCHERS["workday"] is ats_clients.fetch_workday
    with patch("httpx.post", return_value=_list_resp([])):
        jobs = ats_clients.fetch_company({"name": "Acme", "ats": "workday", "slug": SLUG})
    assert jobs == []
    print("FETCHERS/fetch_company: workday wired in correctly — OK")
