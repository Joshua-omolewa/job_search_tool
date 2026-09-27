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

from app import ats_clients


def _resp(json_data=None, text=None, status_code=200, content=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    if text is not None:
        resp.text = text
    if content is not None:
        resp.content = content
    resp.cookies = {}
    return resp


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
    result = {
        "postingTitle": "Data Engineer", "locations": [{"name": "Austin, TX, United States"}],
        "positionId": "200661302", "transformedPostingTitle": "data-engineer",
        "postDateInGMT": "2026-09-27T14:34:25Z", "jobSummary": "Build data pipelines.",
    }
    html = _apple_hydration_html([result], total=1)
    with patch("httpx.get", return_value=_resp(text=html)):
        jobs = ats_clients.fetch_apple("Apple", "united-states-USA")

    assert len(jobs) == 1
    j = jobs[0]
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Austin, TX, United States"
    assert j["url"] == "https://jobs.apple.com/en-us/details/200661302/data-engineer"
    assert j["description"] == "Build data pipelines."
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
    }
    for ats_type, fn in expected.items():
        assert ats_clients.FETCHERS[ats_type] is fn
    print("FETCHERS: all 12 new ATS types wired in correctly — OK")
