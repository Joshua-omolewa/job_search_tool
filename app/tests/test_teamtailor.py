"""Tests for ats_clients.fetch_teamtailor — the public JSON Feed
(jsonfeed.org format) every Teamtailor career site exposes at
{slug}.teamtailor.com/jobs.json. No real network calls: mocks httpx.get.

Response shape here was copied verbatim from a live 2026-09-26 request
against chip.teamtailor.com.

Run with: python -m pytest app/tests/test_teamtailor.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

SLUG = "acme"


def _feed_resp(items):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"version": "https://jsonfeed.org/version/1.1", "title": "Acme", "items": items})
    return resp


def _item(title="Senior Data Analyst", city="London", region=None, country="GB", extra_locations=None):
    locations = [{"address": {"addressLocality": city, "addressRegion": region, "addressCountry": country}}]
    if extra_locations:
        locations += extra_locations
    return {
        "id": "09b580eb",
        "title": title,
        "url": "https://acme.teamtailor.com/jobs/8429681-senior-data-analyst",
        "date_published": "2026-09-21T16:09:27+01:00",
        "content_html": "<p>Full JD text</p>",
        "_jobposting": {
            "title": title,
            "description": "<p>Full JD text</p>",
            "jobLocation": locations,
        },
    }


def test_basic_parsing_single_request_no_gating():
    item = _item()
    with patch("httpx.get", return_value=_feed_resp([item])) as mock_get:
        jobs = ats_clients.fetch_teamtailor("Acme", SLUG)

    assert len(jobs) == 1
    j = jobs[0]
    assert j["company"] == "Acme"
    assert j["title"] == "Senior Data Analyst"
    assert j["location"] == "London, GB"
    assert j["url"] == "https://acme.teamtailor.com/jobs/8429681-senior-data-analyst"
    assert j["posted_at"] == "2026-09-21T16:09:27+01:00"
    assert j["description"] == "<p>Full JD text</p>"

    # Unlike every other ATS client here: no gating, no detail fetch — the
    # single feed request already has everything.
    mock_get.assert_called_once_with(
        f"https://{SLUG}.teamtailor.com/jobs.json",
        headers={"User-Agent": ats_clients.USER_AGENT, "Accept": "application/json"},
        timeout=ats_clients.TIMEOUT,
    )
    print("fetch_teamtailor: basic parsing, single request, no gating — OK")


def test_multi_location_joined():
    item = _item(city="London", country="GB", extra_locations=[
        {"address": {"addressLocality": "Manchester", "addressRegion": None, "addressCountry": "GB"}}
    ])
    with patch("httpx.get", return_value=_feed_resp([item])):
        jobs = ats_clients.fetch_teamtailor("Acme", SLUG)
    assert jobs[0]["location"] == "London, GB; Manchester, GB"
    print("fetch_teamtailor: multiple jobLocation entries joined — OK")


def test_missing_jobposting_or_location_handled():
    item = {"id": "1", "title": "Ghost Posting", "url": "https://acme.teamtailor.com/jobs/1",
            "date_published": None, "content_html": ""}
    with patch("httpx.get", return_value=_feed_resp([item])):
        jobs = ats_clients.fetch_teamtailor("Acme", SLUG)
    assert len(jobs) == 1
    assert jobs[0]["location"] == ""
    assert jobs[0]["posted_at"] is None
    print("fetch_teamtailor: missing _jobposting/location -> empty string, no crash — OK")


def test_wired_into_fetchers_and_fetch_company():
    assert ats_clients.FETCHERS["teamtailor"] is ats_clients.fetch_teamtailor
    with patch("httpx.get", return_value=_feed_resp([])):
        jobs = ats_clients.fetch_company({"name": "Acme", "ats": "teamtailor", "slug": SLUG})
    assert jobs == []
    print("FETCHERS/fetch_company: teamtailor wired in correctly — OK")
