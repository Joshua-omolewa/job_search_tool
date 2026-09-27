"""Tests for aggregator_clients.py's three newest aggregator fetchers:
fetch_remoteok, fetch_jobicy, fetch_weworkremotely. No real network calls —
mocks httpx.get. Response shapes here were copied verbatim from live
2026-09-27 requests (remoteok.com/api, jobicy.com/api/v2/remote-jobs,
weworkremotely.com/categories/remote-programming-jobs.rss).

Run with: python -m pytest app/tests/test_new_aggregators.py
"""
from unittest.mock import patch, MagicMock

from app import aggregator_clients as ac


def _json_resp(data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=data)
    return resp


def _rss_resp(xml_text):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.text = xml_text
    return resp


# ---------------------------------------------------------------- RemoteOK

def test_remoteok_basic_parsing():
    data = [
        {"legal": "API Terms of Service..."},  # first element, no "position" key
        {"company": "Acme", "position": "Data Engineer", "location": "Toronto",
         "url": "https://remoteok.com/remote-jobs/1", "date": "2026-09-20T00:00:00+00:00",
         "description": "Build pipelines.", "salary_min": 120000, "salary_max": 150000},
    ]
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_remoteok({})
    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Data Engineer"
    assert j["location"] == "Remote - Toronto"
    assert j["salary"] == "120,000–150,000 USD/year"
    print("fetch_remoteok: basic parsing, legal-notice element skipped — OK")


def test_remoteok_blank_location_defaults_to_remote():
    data = [{"legal": "..."}, {"company": "Acme", "position": "Data Engineer", "location": "",
             "url": "https://remoteok.com/remote-jobs/2", "date": None, "description": "", }]
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_remoteok({})
    assert jobs[0]["location"] == "Remote"
    print("fetch_remoteok: blank location -> plain 'Remote' — OK")


def test_remoteok_zero_salary_treated_as_no_salary():
    # Regression test for a real bug caught live: RemoteOK uses
    # salary_min == salary_max == 0 to mean "no salary given", not an
    # actual $0 salary. 83 of 99 real postings sampled had this.
    data = [{"legal": "..."}, {"company": "Acme", "position": "Data Engineer", "location": "",
             "url": "https://remoteok.com/remote-jobs/3", "date": None, "description": "",
             "salary_min": 0, "salary_max": 0}]
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_remoteok({})
    assert jobs[0]["salary"] is None
    print("fetch_remoteok: salary_min=salary_max=0 -> None, not '0 USD/year' — OK")


def test_remoteok_missing_url_falls_back_to_apply_url():
    data = [{"legal": "..."}, {"company": "Acme", "position": "Data Engineer",
             "apply_url": "https://remoteok.com/remote-jobs/4", "description": ""}]
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_remoteok({})
    assert jobs[0]["url"] == "https://remoteok.com/remote-jobs/4"
    print("fetch_remoteok: falls back to apply_url when url is missing — OK")


def test_remoteok_wired_into_fetchers():
    assert ac.FETCHERS["remoteok"] is ac.fetch_remoteok


# ----------------------------------------------------------------- Jobicy

def test_jobicy_basic_parsing_with_salary():
    data = {"jobs": [
        {"companyName": "Socure", "jobTitle": "Senior Data Engineer", "jobGeo": "USA",
         "url": "https://jobicy.com/jobs/1", "pubDate": "2026-09-20 00:00:00",
         "jobDescription": "<p>Build things.</p>", "salaryMin": 160000, "salaryMax": 195000,
         "salaryCurrency": "USD", "salaryPeriod": "yearly"},
    ]}
    with patch("httpx.get", return_value=_json_resp(data)) as mock_get:
        jobs = ac.fetch_jobicy({"count": 10, "tag": "data engineer"})
    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Socure"
    assert j["title"] == "Senior Data Engineer"
    assert j["location"] == "Remote - USA"
    assert j["salary"] == "160,000–195,000 USD/year"
    # params passed straight through to the request
    assert mock_get.call_args.kwargs["params"] == {"count": 10, "tag": "data engineer"}
    print("fetch_jobicy: basic parsing incl. real salary fields, params forwarded — OK")


def test_jobicy_period_normalization():
    data = {"jobs": [
        {"companyName": "Acme", "jobTitle": "Contractor", "jobGeo": "Europe",
         "url": "https://jobicy.com/jobs/2", "pubDate": None, "jobDescription": "",
         "salaryMin": 50, "salaryMax": 80, "salaryCurrency": "USD", "salaryPeriod": "hourly"},
    ]}
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_jobicy({})
    assert jobs[0]["salary"] == "50–80 USD/hour"
    print("fetch_jobicy: 'hourly' -> '/hour' period normalization — OK")


def test_jobicy_no_salary_fields():
    data = {"jobs": [
        {"companyName": "Acme", "jobTitle": "Data Engineer", "jobGeo": "",
         "url": "https://jobicy.com/jobs/3", "pubDate": None, "jobDescription": ""},
    ]}
    with patch("httpx.get", return_value=_json_resp(data)):
        jobs = ac.fetch_jobicy({})
    assert jobs[0]["salary"] is None
    assert jobs[0]["location"] == "Remote"  # blank jobGeo -> plain "Remote"
    print("fetch_jobicy: missing salary fields -> None; blank geo -> 'Remote' — OK")


def test_jobicy_wired_into_fetchers():
    assert ac.FETCHERS["jobicy"] is ac.fetch_jobicy


# --------------------------------------------------------- WeWorkRemotely

_WWR_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<item>
  <title>Acme Corp: Senior Data Engineer</title>
  <region>Anywhere in the World</region>
  <category>Full-Stack Programming</category>
  <description>&lt;p&gt;Build data pipelines.&lt;/p&gt;</description>
  <pubDate>Thu, 25 Sep 2026 09:14:28 +0000</pubDate>
  <guid>https://weworkremotely.com/remote-jobs/acme-corp-senior-data-engineer</guid>
  <link>https://weworkremotely.com/remote-jobs/acme-corp-senior-data-engineer</link>
</item>
<item>
  <title>No Colon Title Here</title>
  <region>USA Only</region>
  <description></description>
  <pubDate>Thu, 25 Sep 2026 09:00:00 +0000</pubDate>
  <guid>https://weworkremotely.com/remote-jobs/2</guid>
  <link>https://weworkremotely.com/remote-jobs/2</link>
</item>
</channel></rss>"""


def test_weworkremotely_basic_parsing():
    with patch("httpx.get", return_value=_rss_resp(_WWR_RSS)) as mock_get:
        jobs = ac.fetch_weworkremotely({})
    assert len(jobs) == 2
    j = jobs[0]
    assert j["company"] == "Acme Corp"
    assert j["title"] == "Senior Data Engineer"
    assert j["location"] == "Remote - Anywhere in the World"
    assert "Build data pipelines" in j["description"]
    assert j["salary"] is None  # no salary field in the WWR feed at all
    assert mock_get.call_args.args[0] == "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    print("fetch_weworkremotely: basic parsing, default category, title split on ': ' — OK")


def test_weworkremotely_title_without_colon_falls_back_to_unknown_company():
    with patch("httpx.get", return_value=_rss_resp(_WWR_RSS)):
        jobs = ac.fetch_weworkremotely({})
    j = jobs[1]
    assert j["company"] == "Unknown"
    assert j["title"] == "No Colon Title Here"
    assert j["location"] == "Remote - USA Only"
    print("fetch_weworkremotely: title with no ': ' -> company='Unknown', full text kept as title — OK")


def test_weworkremotely_custom_category():
    with patch("httpx.get", return_value=_rss_resp(_WWR_RSS)) as mock_get:
        ac.fetch_weworkremotely({"category": "remote-full-stack-programming-jobs"})
    assert mock_get.call_args.args[0] == "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss"
    print("fetch_weworkremotely: custom category builds the right URL — OK")


def test_weworkremotely_wired_into_fetchers():
    assert ac.FETCHERS["weworkremotely"] is ac.fetch_weworkremotely


# --------------------------------------------------------- fetch_aggregator

def test_all_three_wired_into_fetch_aggregator():
    with patch("httpx.get", return_value=_json_resp([{"legal": "..."}])):
        jobs = ac.fetch_aggregator({"type": "remoteok", "params": {}})
    assert jobs == []

    with patch("httpx.get", return_value=_json_resp({"jobs": []})):
        jobs = ac.fetch_aggregator({"type": "jobicy", "params": {}})
    assert jobs == []

    with patch("httpx.get", return_value=_rss_resp('<?xml version="1.0"?><rss><channel></channel></rss>')):
        jobs = ac.fetch_aggregator({"type": "weworkremotely", "params": {}})
    assert jobs == []
    print("fetch_aggregator: remoteok/jobicy/weworkremotely all dispatch correctly — OK")
