"""Tests for the salary-extraction helpers in ats_clients.py:
_extract_greenhouse_pay_range (Greenhouse's own structured pay-transparency
HTML block) and _extract_salary_from_text (keyword-anchored regex fallback
over free-text JD prose, used by Ashby/Workday primarily and as a fallback
everywhere else).

Fixture HTML/text below is copied (or lightly trimmed) from live 2026-09-26
responses — Greenhouse's double-HTML-entity-encoded `pay-range` block from
Coinbase, Ashby prose from Cerebras/Docker-style postings, Workday prose
from a real NVIDIA posting.

Run with: python -m pytest app/tests/test_salary_extraction.py
"""
from unittest.mock import patch, MagicMock

from app import ats_clients

# Exactly as Greenhouse's API returns it: HTML-entity-encoded TWICE.
COINBASE_PAY_RANGE_CONTENT = (
    "&lt;div&gt;&lt;div class=&quot;title&quot;&gt;Hourly Rate:&lt;/div&gt;"
    "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$40&lt;/span&gt;"
    "&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;"
    "&lt;span&gt;$40 USD&lt;/span&gt;&lt;/div&gt;&lt;/div&gt;"
)

ANTHROPIC_PAY_RANGE_CONTENT = (
    "&lt;div&gt;&lt;div class=&quot;title&quot;&gt;Annual Salary:&lt;/div&gt;"
    "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$222,800&lt;/span&gt;"
    "&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;"
    "&lt;span&gt;$290,000 USD&lt;/span&gt;&lt;/div&gt;&lt;/div&gt;"
)

# Real bug caught live: Robinhood's label for intern hourly rates is a zone
# descriptor, not a period indicator ("Zone 1 (Menlo Park, CA; New York,
# NY; Bellevue, WA; Washington, DC)"), so "hour"-in-label detection alone
# mislabeled a $29/hr rate as "29 USD/year".
ROBINHOOD_INTERN_HOURLY_CONTENT = (
    "&lt;div&gt;&lt;div class=&quot;title&quot;&gt;Zone 1 (Menlo Park, CA; "
    "New York, NY; Bellevue, WA; Washington, DC)&lt;/div&gt;"
    "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$29&lt;/span&gt;"
    "&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;"
    "&lt;span&gt;$29 USD&lt;/span&gt;&lt;/div&gt;&lt;/div&gt;"
)


def test_greenhouse_pay_range_hourly():
    assert ats_clients._extract_greenhouse_pay_range(COINBASE_PAY_RANGE_CONTENT) == "40 USD/hour"
    print("_extract_greenhouse_pay_range: hourly rate block parsed — OK")


def test_greenhouse_pay_range_annual_with_commas():
    assert ats_clients._extract_greenhouse_pay_range(ANTHROPIC_PAY_RANGE_CONTENT) == "222,800–290,000 USD/year"
    print("_extract_greenhouse_pay_range: annual range with thousands separators parsed — OK")


def test_greenhouse_pay_range_infers_hourly_from_magnitude_when_label_is_a_zone():
    # Regression test for a real bug caught via live validation: a label
    # that's a location/zone descriptor rather than "Hourly Rate:"/"Annual
    # Salary:" must not default to "/year" just because it doesn't say
    # "hour" — a 2-3 digit amount is almost certainly hourly.
    assert ats_clients._extract_greenhouse_pay_range(ROBINHOOD_INTERN_HOURLY_CONTENT) == "29 USD/hour"
    print("_extract_greenhouse_pay_range: ambiguous zone label -> magnitude-inferred hourly, not year — OK")


def test_greenhouse_pay_range_rejects_implausible_ratio():
    # Regression test: a real Coinbase posting had "$152,405 — $179,300,152"
    # in its own source content — a ~1,176x spread, evidently a typo/
    # duplication on their end, not a real range. Must not surface it.
    content = (
        "&lt;div&gt;&lt;div class=&quot;title&quot;&gt;Annual base salary range:&lt;/div&gt;"
        "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$152,405&lt;/span&gt;"
        "&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;"
        "&lt;span&gt;$179,300,152 USD&lt;/span&gt;&lt;/div&gt;&lt;/div&gt;"
    )
    assert ats_clients._extract_greenhouse_pay_range(content) is None
    print("_extract_greenhouse_pay_range: implausible min/max ratio rejected — OK")


def test_greenhouse_pay_range_rejects_unfilled_placeholder():
    # Regression test: a real Anthropic posting had "$1 — $2" — an unfilled
    # template placeholder in their own source content, not a real range.
    content = (
        "&lt;div&gt;&lt;div class=&quot;title&quot;&gt;Annual Salary:&lt;/div&gt;"
        "&lt;div class=&quot;pay-range&quot;&gt;&lt;span&gt;$1&lt;/span&gt;"
        "&lt;span class=&quot;divider&quot;&gt;&amp;mdash;&lt;/span&gt;"
        "&lt;span&gt;$2 USD&lt;/span&gt;&lt;/div&gt;&lt;/div&gt;"
    )
    assert ats_clients._extract_greenhouse_pay_range(content) is None
    print("_extract_greenhouse_pay_range: implausibly tiny placeholder value rejected — OK")


def test_greenhouse_pay_range_absent():
    assert ats_clients._extract_greenhouse_pay_range("<p>Just a normal JD, no pay info.</p>") is None
    assert ats_clients._extract_greenhouse_pay_range("") is None
    assert ats_clients._extract_greenhouse_pay_range(None) is None
    print("_extract_greenhouse_pay_range: no block present -> None, no crash — OK")


# Real-shaped prose, lightly trimmed from a live Cerebras (Ashby) posting.
CEREBRAS_PROSE = (
    "You'll work closely with the security team on cluster hardening.\n\n"
    "The salary range for this position is $140,000 - $240,000 annually. "
    "Actual compensation will be determined based on experience."
)

# Real-shaped prose from a live NVIDIA (Workday) posting.
NVIDIA_PROSE = (
    "Your base salary will be determined based on your location, experience, "
    "and the pay of employees in similar positions. The base salary range is "
    "224,000 USD - 356,500 USD for Level 3, and 272,000 USD - 431,250 USD for Level 4."
)


def test_extract_salary_from_text_dollar_form():
    assert ats_clients._extract_salary_from_text(CEREBRAS_PROSE) == "$140,000 - $240,000"
    print("_extract_salary_from_text: $X - $Y form near 'salary range' — OK")


def test_extract_salary_from_text_bare_currency_form():
    # No literal "$" — Workday's "NNN,NNN USD" style. Takes the FIRST range
    # mentioned (Level 3), consistent with the "take first" pattern used
    # elsewhere in this module for multi-value fields.
    assert ats_clients._extract_salary_from_text(NVIDIA_PROSE) == "224,000 USD - 356,500 USD"
    print("_extract_salary_from_text: bare 'NNN,NNN USD' form, first range taken — OK")


def test_extract_salary_from_text_no_keyword_nearby_ignored():
    # A dollar range with no salary-indicating keyword anywhere near it
    # (funding raised, not compensation) must NOT be treated as a salary —
    # this is the false-positive guard the keyword-proximity check exists for.
    text = "We just raised $10,000,000 - $20,000,000 in our Series B round to fuel growth."
    assert ats_clients._extract_salary_from_text(text) is None
    print("_extract_salary_from_text: unrelated dollar range (funding) correctly ignored — OK")


def test_extract_salary_from_text_no_match():
    assert ats_clients._extract_salary_from_text("A totally ordinary job description with no numbers.") is None
    assert ats_clients._extract_salary_from_text("") is None
    assert ats_clients._extract_salary_from_text(None) is None
    print("_extract_salary_from_text: no range present -> None, no crash — OK")


def test_extract_salary_from_text_strips_html():
    html_text = "<p>Compensation: the base salary range is <b>$100,000 - $150,000</b> per year.</p>"
    assert ats_clients._extract_salary_from_text(html_text) == "$100,000 - $150,000"
    print("_extract_salary_from_text: strips HTML tags before matching — OK")


def _gh_list_resp(jobs):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"jobs": jobs})
    return resp


def test_fetch_greenhouse_wires_structured_salary():
    job = {
        "title": "Hourly Contractor",
        "location": {"name": "Remote"},
        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
        "content": COINBASE_PAY_RANGE_CONTENT,
    }
    with patch("httpx.get", return_value=_gh_list_resp([job])):
        jobs = ats_clients.fetch_greenhouse("Acme", "acme")
    assert jobs[0]["salary"] == "40 USD/hour"
    print("fetch_greenhouse: structured pay-range wired into job dict — OK")


def test_fetch_greenhouse_falls_back_to_prose():
    job = {
        "title": "Data Engineer",
        "location": {"name": "Remote"},
        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/2",
        "content": f"<p>{CEREBRAS_PROSE}</p>",
    }
    with patch("httpx.get", return_value=_gh_list_resp([job])):
        jobs = ats_clients.fetch_greenhouse("Acme", "acme")
    assert jobs[0]["salary"] == "$140,000 - $240,000"
    print("fetch_greenhouse: falls back to prose scan when no pay-range block — OK")


def _ashby_list_resp(jobs):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"jobs": jobs})
    return resp


def test_fetch_ashby_wires_prose_salary():
    job = {
        "title": "Senior Manager - Security",
        "location": "Remote",
        "jobUrl": "https://jobs.ashbyhq.com/acme/1",
        "descriptionPlain": CEREBRAS_PROSE,
    }
    with patch("httpx.get", return_value=_ashby_list_resp([job])):
        jobs = ats_clients.fetch_ashby("Acme", "acme")
    assert jobs[0]["salary"] == "$140,000 - $240,000"
    print("fetch_ashby: prose salary wired into job dict (no structured field exists) — OK")


def test_fetch_ashby_no_salary_mention():
    job = {
        "title": "Software Engineer",
        "location": "Remote",
        "jobUrl": "https://jobs.ashbyhq.com/acme/2",
        "descriptionPlain": "A totally ordinary job description with no numbers.",
    }
    with patch("httpx.get", return_value=_ashby_list_resp([job])):
        jobs = ats_clients.fetch_ashby("Acme", "acme")
    assert jobs[0]["salary"] is None
    print("fetch_ashby: no salary mention -> None, no crash — OK")
