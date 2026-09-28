"""Tests for the 12 ATS clients added 2026-09-27: oraclecloud,
successfactors, icims, eightfold, cornerstone, avature, hrdepartment,
gr8people, google, apple, shopify, meta.

No real network calls — every httpx.get/post is mocked. Response shapes
here were copied (or reconstructed from field values) from real live
requests made during development against real companies (Texas
Instruments, Rogers Communications, AMD, Netflix, MACOM, Synopsys,
Sanmina-SCI, gr8people's own demo tenant, Alphabet, Apple, Shopify, and
Meta Platforms) — see each fetcher's own docstring/CONFIDENCE NOTE in
ats_clients.py and companies.yaml's header comment for the full story.

filters.title_is_relevant/location_is_allowed are patched directly rather
than relying on filters.yaml's live content, so these tests don't depend
on (or get broken by) unrelated edits to that config file.

Run with: python -m pytest app/tests/test_new_ats_platforms.py
"""
import json
from unittest.mock import patch, MagicMock

import httpx

from app import ats_clients


def _resp(json_data=None, text=None, status_code=200, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        # A real httpx response only raises on .raise_for_status() when
        # asked to — this MagicMock needs the same wired up explicitly,
        # or a mocked "error" response would silently behave like a
        # success everywhere (the default for every OTHER _resp() caller
        # in this file, which never sets a >=400 status_code).
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code} error", request=MagicMock(), response=resp
        )
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    if text is not None:
        resp.text = text
    if content is not None:
        resp.content = content
    resp.cookies = {}
    resp.headers = {}
    return resp


# ---------------------------------------------------------- _request_with_retry

def test_request_with_retry_retries_on_429_then_succeeds():
    # Real bug caught live 2026-09-28: once app/main.py started fetching
    # companies concurrently, several Workday tenants returned 429 where
    # a sequential run never had (shared backend CDN across tenants) —
    # this checks the retry actually recovers instead of losing the
    # company entirely.
    responses = [_resp(status_code=429), _resp(json_data={"ok": True}, status_code=200)]
    with patch("httpx.get", side_effect=responses) as mock_get, \
         patch("app.ats_clients.time.sleep") as mock_sleep:
        resp = ats_clients._request_with_retry("GET", "https://example.com/jobs")
    assert resp.json() == {"ok": True}
    assert mock_get.call_count == 2
    mock_sleep.assert_called_once()
    print("_request_with_retry: retries once on 429, then succeeds — OK")


def test_request_with_retry_respects_retry_after_header():
    resp_429 = _resp(status_code=429)
    resp_429.headers = {"Retry-After": "3"}
    responses = [resp_429, _resp(json_data={"ok": True}, status_code=200)]
    with patch("httpx.get", side_effect=responses), \
         patch("app.ats_clients.time.sleep") as mock_sleep:
        ats_clients._request_with_retry("GET", "https://example.com/jobs")
    slept_for = mock_sleep.call_args.args[0]
    assert 3 <= slept_for < 3.5  # Retry-After=3 plus up to 0.5s jitter
    print("_request_with_retry: honors Retry-After header — OK")


def test_request_with_retry_exhausts_retries_and_raises():
    with patch("httpx.get", return_value=_resp(status_code=429)) as mock_get, \
         patch("app.ats_clients.time.sleep"):
        try:
            ats_clients._request_with_retry("GET", "https://example.com/jobs", max_retries=2)
            assert False, "expected an exception after exhausting retries"
        except Exception:
            pass
    assert mock_get.call_count == 3  # initial attempt + 2 retries
    print("_request_with_retry: raises after exhausting retries on persistent 429 — OK")


def test_request_with_retry_no_retry_on_non_429_error():
    with patch("httpx.post", return_value=_resp(status_code=500)) as mock_post, \
         patch("app.ats_clients.time.sleep") as mock_sleep:
        try:
            ats_clients._request_with_retry("POST", "https://example.com/jobs")
            assert False, "expected an exception on a 500"
        except Exception:
            pass
    assert mock_post.call_count == 1  # no retry for a real server error, not a rate limit
    mock_sleep.assert_not_called()
    print("_request_with_retry: does not retry a non-429 error — OK")


# ---------------------------------------------------------------- oraclecloud

def test_oraclecloud_basic_and_detail():
    list_json = {
        "items": [{
            "TotalJobsCount": 1,
            "requisitionList": [{
                "Id": "25018230", "Title": "Data Engineer",
                "PrimaryLocation": "Richardson, TX, United States", "PostedDate": "2026-09-27",
            }],
        }]
    }
    detail_json = {"items": [{
        "ExternalDescriptionStr": "<p>Build data pipelines.</p>",
        "ExternalResponsibilitiesStr": "", "ExternalQualificationsStr": "",
        "ExternalPostedStartDate": "2026-09-27T11:06:22+00:00",
    }]}

    def _get(url, **kwargs):
        if "recruitingCEJobRequisitionDetails" in url:
            return _resp(json_data=detail_json)
        return _resp(json_data=list_json)

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_oraclecloud("Texas Instruments", "edbz.fa.us2.oraclecloud.com/CX")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Richardson, TX, United States"
    assert j["url"] == "https://edbz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/25018230"
    assert j["description"] == "<p>Build data pipelines.</p>"
    print("fetch_oraclecloud: basic parsing + gated detail fetch — OK")


def test_oraclecloud_ungated_job_has_no_description():
    list_json = {"items": [{"TotalJobsCount": 1, "requisitionList": [
        {"Id": "1", "Title": "Facilities Manager", "PrimaryLocation": "Manila, Philippines", "PostedDate": "2026-09-27"}
    ]}]}
    with patch("httpx.get", return_value=_resp(json_data=list_json)), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_oraclecloud("Texas Instruments", "edbz.fa.us2.oraclecloud.com/CX")
    assert jobs[0]["description"] == ""
    print("fetch_oraclecloud: ungated job skips detail fetch — OK")


# -------------------------------------------------------------- successfactors

def test_successfactors_basic_parsing():
    xml = """<?xml version="1.0"?>
<rss xmlns:g="http://base.google.com/ns/1.0"><channel>
<item>
<title>Senior Data Engineer (Toronto, ON, CA)</title>
<link>https://jobs.rogers.com/job/Toronto-Senior-Data-Engineer/123/</link>
<description><![CDATA[&lt;p&gt;Build our data platform.&lt;/p&gt;]]></description>
<guid isPermaLink="false">123</guid>
<g:id>123</g:id>
<g:location>Toronto, ON, CA</g:location>
</item>
</channel></rss>"""
    with patch("httpx.get", return_value=_resp(content=xml.encode())):
        jobs = ats_clients.fetch_successfactors("Rogers Communications", "jobs.rogers.com")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Data Engineer (Toronto, ON, CA)"
    assert j["location"] == "Toronto, ON, CA"
    assert j["url"] == "https://jobs.rogers.com/job/Toronto-Senior-Data-Engineer/123/"
    assert "Build our data platform" in j["description"]
    assert j["posted_at"] is None
    print("fetch_successfactors: XML feed parsing, double-unescape — OK")


def test_successfactors_skips_items_without_link():
    xml = """<?xml version="1.0"?>
<rss xmlns:g="http://base.google.com/ns/1.0"><channel>
<item><title>No Link Job</title><description></description></item>
</channel></rss>"""
    with patch("httpx.get", return_value=_resp(content=xml.encode())):
        jobs = ats_clients.fetch_successfactors("Rogers Communications", "jobs.rogers.com")
    assert jobs == []
    print("fetch_successfactors: item with no <link> skipped — OK")


# --------------------------------------------------------------------- icims

def test_icims_single_response_includes_description():
    page_json = {
        "totalCount": 1,
        "jobs": [{"data": {
            "title": "Data Engineer", "location_name": "Austin, TX",
            "apply_url": "https://global-external-amd.icims.com/jobs/1/login",
            "description": "<p>JD text</p>", "posted_date": "2026-09-27T01:38:00+0000",
            "salary_min_value": 0, "salary_max_value": 0,
        }}],
    }
    empty_page_json = {"totalCount": 1, "jobs": []}
    with patch("httpx.get", side_effect=[_resp(json_data=page_json), _resp(json_data=empty_page_json)]) as mock_get:
        jobs = ats_clients.fetch_icims("AMD", "careers.amd.com")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Austin, TX"
    assert j["description"] == "<p>JD text</p>"
    # No per-job detail fetch — the one call already has everything.
    assert mock_get.call_count == 2  # page 1, then page 2 (empty) to stop
    print("fetch_icims: single response has full description, no detail fetch — OK")


def test_icims_zero_salary_treated_as_absent():
    page_json = {"totalCount": 1, "jobs": [{"data": {
        "title": "X", "apply_url": "https://x.icims.com/jobs/1/login",
        "description": "", "salary_min_value": 0, "salary_max_value": 0,
    }}]}
    with patch("httpx.get", return_value=_resp(json_data=page_json)):
        jobs = ats_clients.fetch_icims("Acme", "careers.acme.com")
    assert jobs[0]["salary"] is None
    print("fetch_icims: salary_min/max_value of 0 treated as no salary, not $0 — OK")


# ------------------------------------------------------------------ eightfold

def test_eightfold_list_endpoint_with_gated_detail():
    list_json = {"count": 1, "positions": [{
        "id": 790318631012, "name": "Data Engineer", "locations": ["Vancouver,Canada"],
        "canonicalPositionUrl": "https://explore.jobs.netflix.net/careers/job/790318631012",
        "t_create": 1790208000,
    }]}
    detail_json = {"job_description": "<p>Own our data platform.</p>"}

    def _get(url, **kwargs):
        if url.endswith("/jobs/790318631012"):
            return _resp(json_data=detail_json)
        return _resp(status_code=200, json_data=list_json)

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_eightfold("Netflix", "netflix.eightfold.ai/netflix.com")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Data Engineer"
    assert jobs[0]["description"] == "<p>Own our data platform.</p>"
    print("fetch_eightfold: list endpoint works, gated detail fetch fills description — OK")


def test_eightfold_falls_back_to_sitemap_on_403():
    sitemap_xml = (
        "<urlset><url><loc>https://careers.lumen.com/careers/job/"
        "111-senior-data-engineer-remote?domain=lumen.com</loc></url></urlset>"
    )
    detail_json = {
        "name": "Senior Data Engineer", "locations": ["Remote, US"],
        "job_description": "<p>JD</p>", "canonicalPositionUrl": "https://careers.lumen.com/careers/job/111",
        "t_create": 1700000000,
    }

    def _get(url, **kwargs):
        if "/api/apply/v2/jobs/111" in url:
            return _resp(json_data=detail_json)
        if "/api/apply/v2/jobs" in url:
            return _resp(status_code=403, json_data={"message": "Not authorized for PCSX"})
        if "sitemap.xml" in url:
            return _resp(text=sitemap_xml)
        raise AssertionError(f"unexpected URL {url}")

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_eightfold("Lumen Technologies", "careers.lumen.com/lumen.com")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Data Engineer"
    assert jobs[0]["description"] == "<p>JD</p>"
    print("fetch_eightfold: search 403 falls back to sitemap + per-job detail — OK")


def test_eightfold_sitemap_fallback_filters_out_other_tenants_jobs():
    # Real bug caught live 2026-09-27: Qualcomm's eightfold host is the
    # generic SHARED app.eightfold.ai (not its own dedicated subdomain),
    # and that shared host's /careers/sitemap.xml silently mixes in
    # postings for OTHER companies (Eightfold's own jobs, in the real
    # case) unless filtered by the `domain=` query param each entry's
    # <loc> tag echoes — this fixture has one job for the target domain
    # and one for an unrelated one, sharing the same host.
    sitemap_xml = (
        "<urlset>"
        "<url><loc>https://app.eightfold.ai/careers/job/"
        "111-senior-data-engineer-remote?domain=qualcomm.com</loc></url>"
        "<url><loc>https://app.eightfold.ai/careers/job/"
        "222-sr-ux-designer-bangalore?domain=eightfold.ai</loc></url>"
        "</urlset>"
    )
    detail_json = {
        "name": "Senior Data Engineer", "locations": ["San Diego, CA"],
        "job_description": "<p>JD</p>", "canonicalPositionUrl": "https://app.eightfold.ai/careers/job/111",
        "t_create": 1700000000,
    }

    def _get(url, **kwargs):
        if "/api/apply/v2/jobs/222" in url:
            raise AssertionError("must not fetch detail for a job belonging to a different tenant")
        if "/api/apply/v2/jobs/111" in url:
            return _resp(json_data=detail_json)
        if "/api/apply/v2/jobs" in url:
            return _resp(status_code=403, json_data={"message": "Not authorized for PCSX"})
        if "sitemap.xml" in url:
            return _resp(text=sitemap_xml)
        raise AssertionError(f"unexpected URL {url}")

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_eightfold("Qualcomm", "app.eightfold.ai/qualcomm.com")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Senior Data Engineer"
    print("fetch_eightfold: sitemap fallback on a shared host filters out other tenants' jobs — OK")


# --------------------------------------------------------------- cornerstone

def test_cornerstone_session_then_list_then_detail():
    home_html = (
        '<script>var x = {"token":"tok123","cloud":"https://us.api.csod.com/"};</script>'
    )
    list_json = {"data": {"totalCount": 1, "requisitions": [{
        "requisitionId": 3675, "displayJobTitle": "Data Engineer",
        "locations": [{"city": "Morgan Hill", "state": "CA", "country": "US"}],
        "externalDescription": "short",
    }]}}
    detail_json = {"data": {
        "externalDescription": "<p>Full JD</p>", "openDate": "2026-09-25T18:23:19",
        "companyApplyUrl": "https://macomtech.csod.com/ux/ats/careersite/4/home/requisition/3675?c=macomtech",
    }}

    def _get(url, **kwargs):
        if "job-requisition" in url:
            return _resp(json_data=detail_json)
        return _resp(text=home_html)

    with patch("httpx.get", side_effect=_get), \
         patch("httpx.post", return_value=_resp(json_data=list_json)), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_cornerstone("MACOM", "macomtech/4")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Morgan Hill, CA, US"
    assert j["description"] == "<p>Full JD</p>"
    assert j["url"].endswith("/requisition/3675?c=macomtech")
    print("fetch_cornerstone: session priming + list + gated detail — OK")


# ------------------------------------------------------------------- avature

def test_avature_list_then_gated_detail_resolves_location():
    list_html = (
        '<article class="article article--result">'
        '<a class="link" href="https://acme.avature.net/careers/JobDetail/Data-Engineer/1">Data Engineer</a>'
        "</article>"
    )
    detail_html = (
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">City</div>'
        '<div class="article__content__view__field__value">Austin</div>'
        "</div>"
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">Country</div>'
        '<div class="article__content__view__field__value">United States</div>'
        "</div>"
        "Job Description and Requirements build data pipelines"
    )

    def _get(url, **kwargs):
        if "JobDetail" in url:
            return _resp(text=detail_html)
        return _resp(text=f"1 results {list_html}")

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True):
        jobs = ats_clients.fetch_avature("Synopsys", "acme")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Austin, United States"
    assert "build data pipelines" in j["description"]
    print("fetch_avature: HTML list + gated detail resolves location — OK")


def test_avature_single_location_field_tenant():
    # Real bug caught live 2026-09-27: Bloomberg's Avature tenant uses one
    # combined "Location" field instead of separate "City"/"Country"
    # fields like Synopsys — checking only City/Country produced an empty
    # location for every single Bloomberg posting, which meant EVERY job
    # got silently dropped by the final "no location, no job" filter, even
    # ones that were real, gated, relevant matches with a full description.
    list_html = (
        '<article class="article article--result">'
        '<a class="link" href="https://acme.avature.net/careers/JobDetail/Data-Engineer/1">Data Engineer</a>'
        "</article>"
    )
    detail_html = (
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">Location</div>'
        '<div class="article__content__view__field__value">London</div>'
        "</div>"
        "Job Description and Requirements build data pipelines"
    )

    def _get(url, **kwargs):
        if "JobDetail" in url:
            return _resp(text=detail_html)
        return _resp(text=f"1 results {list_html}")

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True):
        jobs = ats_clients.fetch_avature("Bloomberg", "acme")

    assert len(jobs) == 1
    assert jobs[0]["location"] == "London"
    print("fetch_avature: single-field \"Location\" tenants (not City/Country) resolve too — OK")


def test_avature_custom_domain_slug_builds_correct_urls():
    # Real bug caught live 2026-09-28: EA/BioWare's Avature tenant is
    # served from a fully custom domain (jobs.ea.com), NOT an
    # {slug}.avature.net subdomain like every other tenant checked so
    # far — a slug containing "/" is "{custom-domain}/{locale}" and
    # drives both the list-page request URL and the detail-page fetch.
    list_html = (
        '<article class="article article--result article--non-toggle">'
        '<a class="link link_result" href="https://jobs.ea.com/en_US/careers/JobDetail/Data-Engineer/1">Data Engineer</a>'
        "</article>"
    )
    detail_html = (
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">City</div>'
        '<div class="article__content__view__field__value">Redwood City</div>'
        "</div>"
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">Country</div>'
        '<div class="article__content__view__field__value">United States</div>'
        "</div>"
        "Job Description and Requirements build data pipelines"
    )
    requested_urls = []

    def _get(url, **kwargs):
        requested_urls.append(url)
        if "JobDetail" in url:
            return _resp(text=detail_html)
        return _resp(text=f"1 results {list_html}")

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True):
        jobs = ats_clients.fetch_avature("EA", "jobs.ea.com/en_US")

    assert requested_urls[0] == "https://jobs.ea.com/en_US/careers/SearchJobs/"
    assert len(jobs) == 1
    assert jobs[0]["url"] == "https://jobs.ea.com/en_US/careers/JobDetail/Data-Engineer/1"
    print("fetch_avature: custom-domain slug builds correct list+detail URLs — OK")


def test_avature_copilot_data_fallback_when_no_field_divs_have_location():
    # Real bug caught live 2026-09-28: EA/BioWare's detail pages don't
    # put location in an article__content__view__field div AT ALL (a
    # differently-shaped block instead) — every field-div label check
    # (City/Country, Location, Locations, ...) comes up empty. But EA
    # (and every other Avature tenant checked) also embeds a
    # `legacyViewCopilotData` JS object with clean Location/City/Country
    # keys — used here as a fallback. Also checks the stray leading ", "
    # on EA's real Country value (", India") gets stripped before use.
    detail_html = (
        '<div class="article__content__view__field">'
        '<div class="article__content__view__field__label">Role ID</div>'
        '<div class="article__content__view__field__value">214164</div>'
        "</div>"
        "<script>legacyViewCopilotData = {\"jobs_data\":[{\"Location\":\"Hyderabad\","
        "\"Country\":\", India\",\"Role ID\":\"214164\"}]};</script>"
        "Job Description and Requirements build data pipelines"
    )
    with patch("httpx.get", return_value=_resp(text=detail_html)):
        detail = ats_clients._fetch_avature_detail("https://jobs.ea.com/en_US", "/careers/JobDetail/x/214164")
    assert detail["location"] == "Hyderabad, India"
    print("fetch_avature: legacyViewCopilotData JSON fallback resolves location, strips stray comma — OK")


def test_avature_ungated_job_dropped_for_missing_location():
    list_html = (
        '<article class="article article--result">'
        '<a class="link" href="https://acme.avature.net/careers/JobDetail/Sales-Rep/1">Sales Rep</a>'
        "</article>"
    )
    with patch("httpx.get", return_value=_resp(text=f"1 results {list_html}")), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_avature("Synopsys", "acme")
    assert jobs == []
    print("fetch_avature: ungated job never gets a location and is dropped — OK")


# --------------------------------------------------------------- hrdepartment

def test_hrdepartment_list_then_gated_detail():
    list_html = (
        "Displaying 1 - 1 of 1"
        '<a href="/hr/ats/Posting/view/1"> <span>Data Engineer</span>\n </a></td>'
        "<td>1</td><td> Engineering</br></td><td>North America USA: TX-Austin-SC</td>"
    )
    detail_html = (
        '<div class="col-xs-12 job-detail-input dhtml_editor_render " '
        'id="job_details_ats_requisition_description">'
        "<p>Build data pipelines.</p></div></div>"
    )

    def _get(url, **kwargs):
        if "Posting/view" in url:
            return _resp(text=detail_html)
        return _resp(text=list_html)

    with patch("httpx.get", side_effect=_get), \
         patch("app.filters.title_is_relevant", return_value=True), \
         patch("app.filters.location_is_allowed", return_value=True):
        jobs = ats_clients.fetch_hrdepartment("Sanmina-SCI", "acme")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "North America USA: TX-Austin-SC"
    assert "Build data pipelines" in j["description"]
    print("fetch_hrdepartment: HTML list + gated detail — OK")


def test_hrdepartment_does_not_loop_past_real_total():
    # Confirmed live: requesting a page past the real end just re-serves
    # the last real page instead of an empty one — pagination MUST be
    # driven by the "of N" total, not an empty-page sentinel, or this
    # would loop until the (very high) sanity cap.
    list_html_page1 = (
        "Displaying 1 - 1 of 1"
        '<a href="/hr/ats/Posting/view/1"> <span>Data Engineer</span>\n </a></td>'
        "<td>1</td><td> Engineering</br></td><td>USA</td>"
    )
    with patch("httpx.get", return_value=_resp(text=list_html_page1)) as mock_get, \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_hrdepartment("Sanmina-SCI", "acme")
    assert len(jobs) == 1
    assert mock_get.call_count == 1  # total(1) == len(jobs) -> no further pages requested
    print("fetch_hrdepartment: pagination driven by real total, not empty-page detection — OK")


# ------------------------------------------------------------------ gr8people

def test_gr8people_basic_parsing():
    graphql_json = {"data": {"searchJobs": {"results": {
        "nodes": [{
            "key": "99", "title": "Data Engineer", "postedOn": "2026-09-27T00:00:00Z",
            "primaryPlace": {"name": "Redwood City, CA"}, "descriptionHTML": "<p>JD</p>",
        }],
        "totalCount": 1, "pageInfo": {"hasNextPage": False, "endCursor": None},
    }}}}
    with patch("httpx.post", return_value=_resp(json_data=graphql_json)) as mock_post:
        jobs = ats_clients.fetch_gr8people("Electronic Arts", "ea")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Redwood City, CA"
    assert j["url"] == "https://ea.gr8people.com/jobs/99"
    mock_post.assert_called_once()
    print("fetch_gr8people: GraphQL parsing — OK")


# ---------------------------------------------------------------------- google

_GOOGLE_RECORD = [
    "123", "Data Engineer, Google Cloud",                                     # 0 id, 1 title
    "https://www.google.com/about/careers/applications/signin?jobId=abc",     # 2 apply url
    [None, "<p>Responsibilities</p>"], [None, "<p>Quals</p>"],                # 3, 4 (unused by the fetcher)
    "resource/path", None, "Google", "en-US",                                # 5, 6, 7, 8
    [["Waterloo, ON, Canada", [], "Waterloo", None, "ON", "CA"]],             # 9 locations
    # 10: [_, full description] — real field also carries pay-transparency
    # text at the end, same as a real captured posting (2026-09-27).
    [None, "<p>Responsibilities: build data pipelines.</p>"
           "<p>Individual pay is determined by many factors. Canada: $182000 - $186000 (CAD)</p>"],
    None,                    # 11
    [1790000000, 0],         # 12: [posted_unix_seconds, nanos]
]


def _google_page_html(records):
    payload = json.dumps([records, None, len(records), 20])
    return f"AF_initDataCallback({{key: 'ds:1', hash: '1', data: {payload}}});"


def test_google_parses_positional_record():
    html = _google_page_html([_GOOGLE_RECORD])
    with patch("httpx.get", return_value=_resp(text=html)):
        jobs = ats_clients.fetch_google("Alphabet", "Canada")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer, Google Cloud"
    assert j["location"] == "Waterloo, ON, Canada"
    assert j["url"] == "https://www.google.com/about/careers/applications/jobs/results/123-data-engineer-google-cloud"
    assert "Responsibilities" in j["description"]
    assert j["salary"] == "$182000 - $186000"
    assert j["posted_at"] == ats_clients._epoch_seconds_to_iso(1790000000)
    print("fetch_google: positional-array record parsing incl. no-comma salary — OK")


def test_google_no_marker_returns_empty():
    with patch("httpx.get", return_value=_resp(text="<html>no jobs here</html>")):
        jobs = ats_clients.fetch_google("Alphabet", "Nowhere")
    assert jobs == []
    print("fetch_google: missing AF_initDataCallback marker -> empty, no crash — OK")


# ----------------------------------------------------------------------- apple

def _apple_hydration_html(search_results, total):
    payload = {"loaderData": {"routes/search": {"searchResults": search_results, "totalRecords": total}}}
    raw_json_string = json.dumps(json.dumps(payload))
    return f"<script>window.__staticRouterHydrationData = JSON.parse({raw_json_string})</script>"


def test_apple_parses_search_results():
    # Real shape confirmed live 2026-09-27: `name` is the bare city only
    # ("Austin") — country lives in a SEPARATE `countryName` field on the
    # same object. Both must end up in the built location string.
    result = {
        "postingTitle": "Data Engineer",
        "locations": [{"name": "Austin", "countryName": "United States of America"}],
        "positionId": "200661302", "transformedPostingTitle": "data-engineer",
        "postDateInGMT": "2026-09-27T14:34:25Z", "jobSummary": "Build data pipelines.",
    }
    html = _apple_hydration_html([result], total=1)
    with patch("httpx.get", return_value=_resp(text=html)):
        jobs = ats_clients.fetch_apple("Apple", "en-us/united-states-USA")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Austin, United States of America"
    assert j["url"] == "https://jobs.apple.com/en-us/details/200661302/data-engineer"
    assert j["description"] == "Build data pipelines."


def test_apple_location_includes_country_for_filters_to_match():
    # Real bug caught live 2026-09-27: every Apple posting's `locations[].name`
    # is JUST the city ("Cupertino", "Austin") with no state/country at
    # all — filters.location_is_allowed only ever matches a country/
    # province marker ("united states", "canada", ", on", ...), never a
    # bare city name, so EVERY Apple job was silently failing the location
    # filter and 0 jobs ever made it through the real pipeline, even though
    # dozens of real Data Engineer-titled postings existed. Appending the
    # separate `countryName` field is what fixes it.
    from app import filters

    result = {
        "postingTitle": "Data Engineer",
        "locations": [{"name": "Cupertino", "countryName": "United States of America"}],
        "positionId": "1", "transformedPostingTitle": "data-engineer",
        "postDateInGMT": None, "jobSummary": "",
    }
    html = _apple_hydration_html([result], total=1)
    with patch("httpx.get", return_value=_resp(text=html)):
        jobs = ats_clients.fetch_apple("Apple", "en-us/united-states-USA")

    assert filters.location_is_allowed(jobs[0]["location"])
    print("fetch_apple: location string includes country so filters.location_is_allowed matches — OK")


def test_apple_slug_selects_locale_path_not_just_query_param():
    # Real bug caught live 2026-09-27: Apple partitions results by LOCALE
    # PATH (/en-us/search vs /en-ca/search) — a Canada-shaped location on
    # the US path silently returns 0, not Canada's results. The slug format
    # is "{locale}/{location}"; this asserts the locale actually ends up in
    # the request path, not just carried around unused.
    result = {
        "postingTitle": "Data Engineer",
        "locations": [{"name": "Toronto", "countryName": "Canada"}],
        "positionId": "1", "transformedPostingTitle": "data-engineer",
        "postDateInGMT": None, "jobSummary": "",
    }
    html = _apple_hydration_html([result], total=1)
    seen_urls = []

    def _get(url, **kwargs):
        seen_urls.append(url)
        return _resp(text=html)

    with patch("httpx.get", side_effect=_get):
        jobs = ats_clients.fetch_apple("Apple", "en-ca/canada-CANC")

    assert seen_urls and all(u == "https://jobs.apple.com/en-ca/search" for u in seen_urls)
    assert jobs[0]["location"] == "Toronto, Canada"
    print("fetch_apple: locale from slug drives the request path (/en-ca/search) — OK")
    print("fetch_apple: hydration-data parsing via recursive key search — OK")


# --------------------------------------------------------------------- shopify

def _shopify_chunks():
    # Minimal turbo-stream-style graph: index 0 is the route data, whose
    # "jobPostingsWithJobs" field (name at idx 3) points at idx 4, a list
    # containing one entry (idx 5) with a "jobPosting" field (name at idx
    # 6) pointing at the actual posting object (idx 7).
    chunks = [
        {"_1": 2},                                    # 0: route data {"jobPostingsWithJobs": <2>}
        "jobPostingsWithJobs",                        # 1
        [3],                                          # 2: list of one entry -> idx 3
        {"_4": 5},                                    # 3: entry {"jobPosting": <5>}
        "jobPosting",                                 # 4
        {"_6": 7, "_8": 9, "_10": 11, "_12": 13},     # 5: jobPosting object
        "id",                                         # 6
        "abc-123",                                    # 7
        "title",                                      # 8
        "Data Engineer",                              # 9
        "locationName",                               # 10
        "Remote",                                     # 11
        "publishedDate",                              # 12
        "2026-09-24",                                 # 13
    ]
    return chunks


def test_shopify_parses_turbo_stream_listing():
    with patch("httpx.get", return_value=_resp(json_data=_shopify_chunks())), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_shopify("Shopify", "shopify")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Remote"
    assert j["url"] == "https://www.shopify.com/careers/data-engineer_abc-123"
    print("fetch_shopify: turbo-stream index-reference decoding — OK")


# ------------------------------------------------------------------------ meta

def test_meta_session_then_graphql_search():
    session_html = '<script>["LSD",[],{"token":"lsdtok123"}]</script>'
    graphql_json = {"data": {"job_search_with_featured_jobs": {"all_jobs": [{
        "id": "2045779119402472", "title": "Data Engineer, Product Analytics",
        "locations": ["Menlo Park, CA"],
    }]}}}

    def _get(url, **kwargs):
        return _resp(text=session_html)

    with patch("httpx.get", side_effect=_get), \
         patch("httpx.post", return_value=_resp(json_data=graphql_json)), \
         patch("app.filters.title_is_relevant", return_value=False):
        jobs = ats_clients.fetch_meta("Meta Platforms", "")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer, Product Analytics"
    assert j["location"] == "Menlo Park, CA"
    assert j["url"] == "https://www.metacareers.com/jobs/2045779119402472/"
    print("fetch_meta: session priming (datr/lsd) + GraphQL search — OK")


def test_meta_raises_if_lsd_token_missing():
    with patch("httpx.get", return_value=_resp(text="<html>no lsd here</html>")):
        try:
            ats_clients.fetch_meta("Meta Platforms", "")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "LSD" in str(e)
    print("fetch_meta: missing LSD token raises clearly instead of silently failing — OK")


# ------------------------------------------------------------- FETCHERS wiring

def test_all_new_ats_types_wired_into_fetchers():
    expected = {
        "oraclecloud": ats_clients.fetch_oraclecloud,
        "successfactors": ats_clients.fetch_successfactors,
        "icims": ats_clients.fetch_icims,
        "eightfold": ats_clients.fetch_eightfold,
        "cornerstone": ats_clients.fetch_cornerstone,
        "avature": ats_clients.fetch_avature,
        "hrdepartment": ats_clients.fetch_hrdepartment,
        "gr8people": ats_clients.fetch_gr8people,
        "google": ats_clients.fetch_google,
        "apple": ats_clients.fetch_apple,
        "shopify": ats_clients.fetch_shopify,
        "meta": ats_clients.fetch_meta,
        "amazon": ats_clients.fetch_amazon,
        "ibm": ats_clients.fetch_ibm,
        "uber": ats_clients.fetch_uber,
        "atlassian": ats_clients.fetch_atlassian,
        "kula": ats_clients.fetch_kula,
        "ukg_ultipro": ats_clients.fetch_ukg_ultipro,
        "humi_platform": ats_clients.fetch_humi_platform,
        "tiktok": ats_clients.fetch_tiktok,
        "ongig": ats_clients.fetch_ongig,
    }
    for ats_type, fn in expected.items():
        assert ats_clients.FETCHERS[ats_type] is fn
    print("FETCHERS: all 21 new ATS types wired in correctly — OK")


# ------------------------------------------------------------ playwright_lock

def test_playwright_lock_is_shared_across_modules():
    # Real bug this guards against: if ats_clients.py and
    # aggregator_clients.py each imported/created their OWN lock instead
    # of sharing app.playwright_lock.PLAYWRIGHT_LOCK, fetch_uber and
    # fetch_indeed could still run Playwright concurrently with each
    # other — the whole point of the lock (confirmed live 2026-09-28:
    # concurrent Playwright usage across threads visibly serialized/
    # stalled a real run) would be silently defeated.
    from app import aggregator_clients, playwright_lock
    import threading
    assert isinstance(playwright_lock.PLAYWRIGHT_LOCK, type(threading.Lock()))
    assert ats_clients.playwright_lock.PLAYWRIGHT_LOCK is playwright_lock.PLAYWRIGHT_LOCK
    assert aggregator_clients.playwright_lock.PLAYWRIGHT_LOCK is playwright_lock.PLAYWRIGHT_LOCK
    print("playwright_lock: same lock instance shared by both modules — OK")


# --------------------------------------------------------------------- uber

def test_uber_missing_playwright_degrades_gracefully():
    # fetch_uber drives a real headless Chromium via Playwright (Uber's
    # own JSON API is Cloudflare-bot-walled to a plain request) — like
    # fetch_indeed in aggregator_clients.py, not practical to mock
    # meaningfully with unittest.mock's fluent locator API, so per that
    # same established precedent this only tests the "playwright not
    # installed -> [], no crash" contract; the actual scraping was
    # verified live (439 real postings, correct title/location/salary/
    # description parsing from real jobs.uber.com API responses).
    import sys
    with patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
        jobs = ats_clients.fetch_uber("Uber", "data engineer")
    assert jobs == []
    print("fetch_uber: playwright not installed -> [], no crash — OK")


def test_uber_bodies_to_jobs_parsing():
    # _uber_bodies_to_jobs is pure Python (no Playwright), fully mockable —
    # shape trimmed from a real live response (2026-09-28) against
    # jobs.uber.com/api/jobs/search/?search=data+engineer.
    bodies = [{"jobs": [{
        "Id": "300433",
        "Title": "Sr Software Engineer - Engineer",
        "Urls": [{"Culture": "en-us", "Url": "/en/jobs/300433/", "IsDefault": True}],
        "Locations": [{"City": "San Francisco", "Region": "California", "Country": "United States"}],
        "DisplayDate": "2026-09-23T23:29:27Z",
        "Description": "<p>Build data pipelines.</p>",
        "Salary": {
            "MinValue": None, "MaxValue": None, "Currency": None, "Period": None,
            "Description": "The base salary range for this role is USD $202,000 per year - USD $224,000 per year.",
        },
    }]}]
    jobs = ats_clients._uber_bodies_to_jobs(bodies, "Uber")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Sr Software Engineer - Engineer"
    assert j["location"] == "San Francisco, California, United States"
    assert j["url"] == "https://jobs.uber.com/en/jobs/300433/"
    assert j["posted_at"] == "2026-09-23T23:29:27Z"
    assert "Build data pipelines" in j["description"]
    assert j["salary"] == "USD $202,000 per year - USD $224,000"
    print("_uber_bodies_to_jobs: parsing, structured-then-text salary fallback — OK")


def test_uber_bodies_to_jobs_dedupes_across_pages():
    bodies = [
        {"jobs": [{"Id": "1", "Title": "X", "Urls": [{"Url": "/en/jobs/1/", "IsDefault": True}],
                   "Locations": [], "Description": ""}]},
        {"jobs": [{"Id": "1", "Title": "X", "Urls": [{"Url": "/en/jobs/1/", "IsDefault": True}],
                   "Locations": [], "Description": ""}]},
    ]
    jobs = ats_clients._uber_bodies_to_jobs(bodies, "Uber")
    assert len(jobs) == 1
    print("_uber_bodies_to_jobs: duplicate Id across pages collapsed — OK")


# ------------------------------------------------------------------ atlassian

def test_atlassian_basic_parsing():
    # Shape trimmed from a real live response (2026-09-28) against
    # www.atlassian.com/endpoint/careers/listings — payRanges is always
    # null and compensation is boilerplate text with no real numbers on
    # every posting sampled, so salary only ever comes from the
    # free-text fallback over the assembled description.
    postings = [{
        "id": 25584,
        "title": "Senior Data Engineer, DX",
        "locations": ["Salt Lake City - United States -   Salt Lake City, Utah 84044 United States", "Remote - Remote"],
        "category": "Engineering",
        "overview": "<p>Build the data platform.</p>",
        "responsibilities": "<p>Own pipelines.</p>",
        "qualifications": "<p>Strong SQL. Compensation range: $150,000 - $190,000 annually.</p>",
        "applyUrl": "https://globalcareers-atlassian.icims.com/jobs/25584/senior-data-engineer/job",
        "payRanges": None,
        "compensation": "<p data-compensation='true'>We strive to design equitable, competitive compensation.</p>",
        "portalJobPost": {"portalId": 17, "portalUrl": "https://globalcareers-atlassian.icims.com/jobs/25584/x/job",
                           "id": 25584, "updatedDate": "2026-09-22 12:42 AM"},
    }]
    with patch("httpx.get", return_value=_resp(json_data=postings)):
        jobs = ats_clients.fetch_atlassian("Atlassian", "unused")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Data Engineer, DX"
    assert "Salt Lake City" in j["location"] and "Remote - Remote" in j["location"]
    assert j["url"] == "https://globalcareers-atlassian.icims.com/jobs/25584/senior-data-engineer/job"
    assert j["posted_at"] == "2026-09-22 12:42 AM"
    assert "Build the data platform" in j["description"] and "Own pipelines" in j["description"]
    assert j["salary"] == "$150,000 - $190,000"
    print("fetch_atlassian: basic parsing, description assembly, text-fallback salary — OK")


def test_atlassian_dedupes_and_skips_incomplete():
    postings = [
        {"id": 1, "title": "A", "locations": [], "overview": "", "applyUrl": "https://x/1", "portalJobPost": {}},
        {"id": 1, "title": "A", "locations": [], "overview": "", "applyUrl": "https://x/1", "portalJobPost": {}},
        {"id": 2, "title": "", "locations": [], "overview": "", "applyUrl": "https://x/2", "portalJobPost": {}},
        {"id": None, "title": "C", "locations": [], "overview": "", "applyUrl": "https://x/3", "portalJobPost": {}},
    ]
    with patch("httpx.get", return_value=_resp(json_data=postings)):
        jobs = ats_clients.fetch_atlassian("Atlassian", "unused")
    assert len(jobs) == 1 and jobs[0]["url"] == "https://x/1"
    print("fetch_atlassian: dedupes by id, skips missing title/id — OK")


# ----------------------------------------------------------------------- kula

def test_kula_basic_parsing_and_url_construction():
    # Shape trimmed from a real live response (2026-09-28) against
    # careers.kula.ai/api/internal/ats_job_posts?accountName=vidyard —
    # the apply URL isn't in the payload at all, it's constructed, so
    # this specifically checks it matches the real site's own anchor
    # href for this exact job (verified separately by loading
    # careers.kula.ai/vidyard directly).
    page1 = {
        "data": [{
            "id": 34567,
            "title": "Future Opportunities: Enterprise Account Manager",
            "launch_at": "2026-05-13T20:38:29.000Z",
            "ats_job": {
                "job_description": "<p>Build pipelines. Compensation range: $120,000 - $150,000 annually.</p>",
                "offices": [{"location": "Quebec, Canada", "name": "Remote - Canada"}],
            },
        }],
        "meta": {"count": 1, "page": 1, "items": 99, "pages": 1},
    }
    with patch("httpx.get", return_value=_resp(json_data=page1)):
        jobs = ats_clients.fetch_kula("Vidyard", "vidyard")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Future Opportunities: Enterprise Account Manager"
    assert j["location"] == "Quebec, Canada"
    assert j["url"] == "https://careers.kula.ai/vidyard/34567-future-opportunities-enterprise-account-manager"
    assert j["posted_at"] == "2026-05-13T20:38:29.000Z"
    assert "Build pipelines" in j["description"]
    assert j["salary"] == "$120,000 - $150,000"
    print("fetch_kula: basic parsing, apply-URL construction, text-fallback salary — OK")


def test_kula_paginates_and_dedupes():
    page1 = {"data": [{"id": 1, "title": "A", "ats_job": {}}], "meta": {"pages": 2}}
    page2 = {"data": [{"id": 2, "title": "B", "ats_job": {}}], "meta": {"pages": 2}}
    with patch("httpx.get", side_effect=[_resp(json_data=page1), _resp(json_data=page2)]):
        jobs = ats_clients.fetch_kula("Vidyard", "vidyard")
    assert {j["title"] for j in jobs} == {"A", "B"}
    print("fetch_kula: pagination across pages — OK")


# ------------------------------------------------------------------ ukg_ultipro

def test_ukg_ultipro_basic_parsing_and_slug_split():
    # Shape trimmed from a real live response (2026-09-28) against MDA
    # Space's Canada board. slug is "{host}/{tenant}/{boardGuid}" — this
    # checks all three get split out and used correctly in both the
    # request URL and the constructed detail URL.
    search_json = {"opportunities": [{
        "Id": "7a5d7ce7-e8dc-404b-a09c-cf1f0de625be",
        "Title": "Senior Data Platform Engineer",
        "Locations": [{"Address": {"City": "Brampton", "State": {"Code": "ON", "Name": "Ontario"},
                                    "Country": {"Code": "CAN", "Name": "Canada"}}}],
        "PostedDate": "2026-09-28T15:25:43.037Z",
        "BriefDescription": "Build the data platform. Compensation range: $110,000 - $140,000 annually.",
    }]}
    with patch("httpx.post", return_value=_resp(json_data=search_json)) as mock_post:
        jobs = ats_clients.fetch_ukg_ultipro(
            "MDA Space", "recruiting.ultipro.ca/MAC5000MCDW/664818ff-3594-4bec-9f30-3394e59e19f3"
        )
    assert mock_post.call_args.args[0] == (
        "https://recruiting.ultipro.ca/MAC5000MCDW/JobBoard/"
        "664818ff-3594-4bec-9f30-3394e59e19f3/JobBoardView/LoadSearchResults"
    )
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Data Platform Engineer"
    assert j["location"] == "Brampton, Ontario, Canada"
    assert j["url"] == (
        "https://recruiting.ultipro.ca/MAC5000MCDW/JobBoard/664818ff-3594-4bec-9f30-3394e59e19f3/"
        "OpportunityDetail?opportunityId=7a5d7ce7-e8dc-404b-a09c-cf1f0de625be"
    )
    assert j["posted_at"] == "2026-09-28T15:25:43.037Z"
    assert j["salary"] == "$110,000 - $140,000"
    print("fetch_ukg_ultipro: basic parsing, slug split, URL construction — OK")


def test_ukg_ultipro_dedupes_by_id():
    search_json = {"opportunities": [
        {"Id": "1", "Title": "A", "Locations": [], "BriefDescription": ""},
        {"Id": "1", "Title": "A", "Locations": [], "BriefDescription": ""},
    ]}
    with patch("httpx.post", return_value=_resp(json_data=search_json)):
        jobs = ats_clients.fetch_ukg_ultipro("MDA Space", "host/tenant/guid")
    assert len(jobs) == 1
    print("fetch_ukg_ultipro: dedupes by Id — OK")


# ------------------------------------------------------------------ humi_platform

def test_humi_platform_basic_parsing_via_json_ld_detail():
    # Shape trimmed from real live pages (2026-09-28): list page is
    # ecopiatech.applytojobs.ca/v1/embedded (plain SSR HTML, title+url
    # only), detail page embeds a real schema.org JobPosting JSON-LD
    # block with STRUCTURED jobLocation/baseSalary — a much more
    # reliable source than splitting the list page's combined location/
    # type/date text blob. A non-relevant title ("General: Want to join
    # us?") should never trigger a detail fetch and gets dropped for
    # having no location, same gating as fetch_avature.
    list_html = '''
    <section class="humi-job-board-postings">
        <div class="humi-job-board-posting">
            <h3 class="humi-job-board-posting-title">
                <a href="https://ecopiatech.applytojobs.ca/talent/50826" target="_blank">General: Want to join us? Apply here!</a>
            </h3>
        </div>
        <div class="humi-job-board-posting">
            <h3 class="humi-job-board-posting-title">
                <a href="https://ecopiatech.applytojobs.ca/engineering/50825" target="_blank">Senior Data Platform Engineer</a>
            </h3>
        </div>
    </section>
    '''
    detail_html = '''<script type="application/ld+json">
    {"title": "Senior Data Platform Engineer", "datePosted": "2026-09-09T16:09:54+00:00",
     "description": "&lt;div&gt;Build the data platform.&lt;/div&gt;",
     "baseSalary": {"@type": "MonetaryAmount", "minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
     "jobLocation": {"address": {"addressLocality": "Toronto", "addressRegion": "Ontario", "addressCountry": "Canada"}}}
    </script>'''
    with patch("httpx.get", side_effect=[_resp(text=list_html), _resp(text=detail_html)]):
        jobs = ats_clients.fetch_humi_platform("Ecopia AI", "ecopiatech")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Data Platform Engineer"
    assert j["location"] == "Toronto, Ontario, Canada"
    assert j["url"] == "https://ecopiatech.applytojobs.ca/engineering/50825"
    assert j["posted_at"] == "2026-09-09T16:09:54+00:00"
    assert "Build the data platform" in j["description"]
    assert j["salary"] == "100,000–150,000/year"
    print("fetch_humi_platform: JSON-LD detail parsing, non-relevant title dropped — OK")


def test_humi_platform_detail_fetch_failure_degrades_to_empty():
    with patch("httpx.get", side_effect=Exception("boom")):
        result = ats_clients._fetch_humi_platform_detail("https://ecopiatech.applytojobs.ca/engineering/99999")
    assert result == {"location": "", "posted_at": None, "description": "", "salary": None}
    print("_fetch_humi_platform_detail: request failure -> empty dict, no crash — OK")


# --------------------------------------------------------------------- tiktok

def test_tiktok_basic_parsing_and_nested_location():
    # Shape trimmed from a real live response (2026-09-28) against
    # api.lifeattiktok.com — city_info is a linked-list of {en_name,
    # parent} nodes (city -> state/province -> country), walked here into
    # one "City, State, Country" string.
    search_json = {"code": 0, "data": {"count": 1, "job_post_list": [{
        "id": "7117084434700110094",
        "title": "Data Engineer, E-Commerce",
        "description": "Build pipelines.",
        "requirement": "5+ years SQL. Compensation range: $150,000 - $200,000 annually.",
        "city_info": {"en_name": "San Jose", "parent": {"en_name": "California",
                                                          "parent": {"en_name": "United States of America"}}},
        "job_post_info": {"min_salary": None, "max_salary": None, "currency": None},
    }]}}
    with patch("httpx.post", return_value=_resp(json_data=search_json)) as mock_post:
        jobs = ats_clients.fetch_tiktok("TikTok", "data engineer")

    assert mock_post.call_args.kwargs["json"]["keyword"] == "data engineer"
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer, E-Commerce"
    assert j["location"] == "San Jose, California, United States of America"
    assert j["url"] == "https://lifeattiktok.com/search/7117084434700110094"
    assert "Build pipelines" in j["description"]
    assert j["salary"] == "$150,000 - $200,000"
    print("fetch_tiktok: basic parsing, nested city_info walk, keyword param — OK")


def test_tiktok_paginates_and_dedupes():
    page1 = {"code": 0, "data": {"count": 2, "job_post_list": [
        {"id": "1", "title": "A", "description": "", "requirement": "", "city_info": {}, "job_post_info": {}},
    ]}}
    page2 = {"code": 0, "data": {"count": 2, "job_post_list": [
        {"id": "2", "title": "B", "description": "", "requirement": "", "city_info": {}, "job_post_info": {}},
    ]}}
    with patch("httpx.post", side_effect=[_resp(json_data=page1), _resp(json_data=page2)]), \
         patch("app.ats_clients._TIKTOK_PAGE_SIZE", 1):
        jobs = ats_clients.fetch_tiktok("TikTok", "data engineer")
    assert {j["title"] for j in jobs} == {"A", "B"}
    print("fetch_tiktok: pagination via offset — OK")


# ---------------------------------------------------------------------- ongig

def test_ongig_csrf_dance_then_paginated_search():
    # Shape trimmed from a real live response (2026-09-28) against
    # jobs.elastic.co (group_id 1509). Two-step: GET /sanctum/csrf-cookie
    # sets an XSRF-TOKEN cookie, POST /api/appSearch echoes it back
    # URL-decoded as the x-xsrf-token header — this checks both the
    # header value and that group_id from the slug reaches the filter.
    csrf_resp = _resp(status_code=204)
    csrf_resp.cookies = {"XSRF-TOKEN": "abc%3D%3D"}  # URL-encoded "abc=="
    page1 = {
        "meta": {"page": {"total_pages": 1}},
        "results": [{
            "id": {"raw": "999"}, "title": {"raw": "Senior Data Engineer"},
            "location": {"raw": "United States"},
            "content": {"raw": "Build the platform. Compensation range: $140,000 - $180,000 annually."},
        }],
    }
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = csrf_resp
    mock_client.post.return_value = _resp(json_data=page1)

    with patch("httpx.Client", return_value=mock_client):
        jobs = ats_clients.fetch_ongig("Elastic", "jobs.elastic.co/1509")

    assert mock_client.get.call_args.args[0] == "https://jobs.elastic.co/sanctum/csrf-cookie"
    assert mock_client.post.call_args.kwargs["headers"]["x-xsrf-token"] == "abc=="
    assert mock_client.post.call_args.kwargs["json"]["filters"]["all"][0]["any"][0]["group_id"] == 1509
    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Senior Data Engineer"
    assert j["location"] == "United States"
    assert j["url"] == "https://jobs.elastic.co/jobs/999"
    assert "Build the platform" in j["description"]
    assert j["salary"] == "$140,000 - $180,000"
    print("fetch_ongig: CSRF dance, group_id filter, basic parsing — OK")


def test_ongig_missing_csrf_cookie_returns_empty():
    csrf_resp = _resp(status_code=204)
    csrf_resp.cookies = {}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = csrf_resp

    with patch("httpx.Client", return_value=mock_client):
        jobs = ats_clients.fetch_ongig("Elastic", "jobs.elastic.co/1509")
    assert jobs == []
    mock_client.post.assert_not_called()
    print("fetch_ongig: missing XSRF-TOKEN cookie -> [], no crash — OK")


def test_ongig_paginates_across_pages():
    csrf_resp = _resp(status_code=204)
    csrf_resp.cookies = {"XSRF-TOKEN": "abc"}
    page1 = {"meta": {"page": {"total_pages": 2}},
             "results": [{"id": {"raw": "1"}, "title": {"raw": "A"}, "location": {"raw": ""}, "content": {"raw": ""}}]}
    page2 = {"meta": {"page": {"total_pages": 2}},
             "results": [{"id": {"raw": "2"}, "title": {"raw": "B"}, "location": {"raw": ""}, "content": {"raw": ""}}]}
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.get.return_value = csrf_resp
    mock_client.post.side_effect = [_resp(json_data=page1), _resp(json_data=page2)]

    with patch("httpx.Client", return_value=mock_client):
        jobs = ats_clients.fetch_ongig("Elastic", "jobs.elastic.co/1509")
    assert {j["title"] for j in jobs} == {"A", "B"}
    print("fetch_ongig: pagination across pages — OK")


# ------------------------------------------------------------------------ ibm

def test_ibm_basic_parsing_and_city_country_dedup():
    # Real shape confirmed live 2026-09-28: field_keyword_19 is already
    # "City, CC" (e.g. "Austin, US") — appending the full country name
    # too without stripping the code first would render as
    # "Austin, US, United States", which this checks doesn't happen.
    search_json = {"hits": {"total": {"value": 1}, "hits": [{"_source": {
        "title": "Data Engineer", "url": "https://careers.ibm.com/careers/JobDetail?jobId=127183",
        "description": "Build pipelines. Compensation range: $120,000 - $150,000 annually.",
        "field_keyword_05": "United States", "field_keyword_19": "Austin, US", "field_keyword_17": "Hybrid",
    }}]}}
    with patch("httpx.post", return_value=_resp(json_data=search_json)):
        jobs = ats_clients.fetch_ibm("IBM", "United States/data engineer")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Austin, United States"
    assert j["url"] == "https://careers.ibm.com/careers/JobDetail?jobId=127183"
    assert j["salary"] == "$120,000 - $150,000"
    print("fetch_ibm: basic parsing, city/country-code dedup, salary fallback — OK")


def test_ibm_multiple_cities_falls_back_to_country():
    search_json = {"hits": {"total": {"value": 1}, "hits": [{"_source": {
        "title": "Data Engineer", "url": "https://careers.ibm.com/careers/JobDetail?jobId=1",
        "description": "", "field_keyword_05": "United States", "field_keyword_19": "Multiple Cities",
    }}]}}
    with patch("httpx.post", return_value=_resp(json_data=search_json)):
        jobs = ats_clients.fetch_ibm("IBM", "United States/data engineer")
    assert jobs[0]["location"] == "United States"
    print("fetch_ibm: \"Multiple Cities\" falls back to just the country — OK")


# --------------------------------------------------------------------- amazon

def test_amazon_basic_parsing_country_param_in_request():
    # Real bug avoided 2026-09-28: the naive/guessable param shape
    # `country[]=CAN` (array-style) is silently IGNORED by this endpoint —
    # it still returns worldwide results instead of erroring, so a wrong
    # param shape here wouldn't fail loudly, it would just quietly return
    # the wrong country's jobs. The real UI uses a plain `country=CAN`.
    search_json = {
        "hits": 1,
        "jobs": [{
            "id_icims": "10496221", "title": "Data Engineer II, Alexa Audio",
            "job_path": "/en/jobs/10496221/data-engineer-ii-alexa-audio",
            "location": "CA, BC, Vancouver", "city": "Vancouver", "state": "BC", "country_code": "CAN",
            "posted_date": "August  7, 2026",
            "description": "Build data pipelines. Compensation: $150,000 - $180,000 salary.",
            "basic_qualifications": "", "preferred_qualifications": "",
        }],
    }
    seen_params = []

    def _get(url, params=None, **kwargs):
        seen_params.append(params)
        return _resp(json_data=search_json)

    with patch("httpx.get", side_effect=_get):
        jobs = ats_clients.fetch_amazon("Amazon", "CAN/data engineer")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer II, Alexa Audio"
    assert j["location"] == "CA, BC, Vancouver"
    assert j["url"] == "https://www.amazon.jobs/en/jobs/10496221/data-engineer-ii-alexa-audio"
    assert "Build data pipelines" in j["description"]
    assert j["salary"] == "$150,000 - $180,000"
    assert all(p["country"] == "CAN" and "[]" not in str(p) for p in seen_params)
    print("fetch_amazon: basic parsing + plain (non-array) country param — OK")


def test_amazon_dedupes_by_job_id():
    search_json = {
        "hits": 1,
        "jobs": [
            {"id_icims": "1", "title": "Data Engineer", "job_path": "/en/jobs/1/x",
             "location": "USA", "description": "", "basic_qualifications": "", "preferred_qualifications": ""},
            {"id_icims": "1", "title": "Data Engineer", "job_path": "/en/jobs/1/x",
             "location": "USA", "description": "", "basic_qualifications": "", "preferred_qualifications": ""},
        ],
    }
    with patch("httpx.get", return_value=_resp(json_data=search_json)):
        jobs = ats_clients.fetch_amazon("Amazon", "USA/data engineer")
    assert len(jobs) == 1
    print("fetch_amazon: duplicate id_icims across pages collapsed — OK")
