"""Thin clients for each ATS's public job-board API.

Every function returns a list of plain dicts with a common shape:
    {"company": str, "title": str, "location": str, "url": str,
     "posted_at": str|None, "description": str, "salary": str|None}

`description` is HTML or plain text, whatever the ATS gives us, and is
consumed by filters.jd_stack_mismatch() for the JD-based stack dealbreaker
check. It's fetched from the SAME request as the listing wherever the ATS
supports that (Greenhouse ?content=true, Ashby/Lever include it by
default) — no extra per-job request needed, so this doesn't multiply your
call volume. Workable's widget API may or may not include it depending on
account; if absent, `description` is just "" and the stack check is
skipped for that job (title/location filters still apply).

`salary` (added 2026-09-26) is a best-effort "X–Y CUR/period" string —
None where nothing usable is found. Two extraction paths, in priority
order per ATS:
  1. A genuine structured field, when the ATS has one — confirmed live
     with real, populated data: Lever (`salaryRange`, list-level, no
     gating), SmartRecruiters (`compensation`, detail-level, gated),
     Teamtailor (`baseSalary`, list-level), Greenhouse (`pay-range` HTML
     block — Greenhouse's own pay-transparency module, not company text;
     see _extract_greenhouse_pay_range). Adzuna/Remotive (aggregator_clients.py)
     have their own numeric/plain-text fields, same idea.
  2. _extract_salary_from_text: a keyword-anchored regex over the JD
     description prose, used as a fallback everywhere — the primary path
     for Ashby and Workday (no structured field exists at all there;
     confirmed real disclosed ranges on Docker, Cerebras, NVIDIA), and a
     fallback for every other ATS when step 1 comes up empty for that
     specific posting (common — pay disclosure is per-company, not
     per-ATS). Validated live across many companies/ATSes; it's inherently
     a heuristic over free text, not a real field, so treat it with
     correspondingly less confidence than a structured hit.

_extract_greenhouse_pay_range guards against real DATA-ENTRY ERRORS caught
live in the source content itself, not this code: an unfilled "$1 — $2"
template placeholder (Anthropic) and an evidently mistyped "$152,405 —
$179,300,152" range (Coinbase, ~1,176x spread) both get rejected as
implausible rather than shown. Its period (hour vs. year) is also decided
by MAGNITUDE, not the label text next to the range — the label is
sometimes a zone/location descriptor, not a period at all (Robinhood), or
a stale copy-paste (Samsara: "Annual Base Salary" labeling an actual
$35–$58/hr co-op rate) — both caught live before this fix.

NOTE ON SANDBOXES: an earlier dev environment used for this project had
its outbound HTTP blocked by a network allowlist, which is why some
CONFIDENCE NOTEs below mention verifying via WebFetch instead of a direct
request. That was a property of that one sandbox, not of the ATS APIs —
outbound HTTP works fine in the environment these fetchers are actually
maintained in now (and will on a normal laptop/server/cron box/VM).

CONFIDENCE NOTE on fetch_smartrecruiters specifically (2026-08-12): unlike
Greenhouse/Ashby/Workable/Lever, which were each confirmed against a real
live response during development, SmartRecruiters' shape here is built
from their docs (developers.smartrecruiters.com) plus two independent
third-party integrations that describe the same shape (jobspipe.dev,
an Apify scraper) — this sandbox's WebFetch got blocked by
api.smartrecruiters.com's robots.txt, so it's NOT been hit live the way
the others were. Verify it the same way Coveo/Treewalk's slugs got
verified: `python -m app.main --company <slug>` against a real
SmartRecruiters company before trusting it in a real run.

CONFIDENCE NOTE on fetch_workday (2026-09-26): confirmed live against a
real production Workday board (NVIDIA's, nvidia.wd5.myworkdayjobs.com) —
both the list endpoint (POST .../wday/cxs/{tenant}/{site}/jobs) and the
per-job detail endpoint (GET the same base + the listing's own
externalPath) were hit directly, not just inferred from docs. Two things
this can't rule out: (1) some tenants may configure custom facets/fields
Nvidia's board doesn't use, and (2) the CXS API is undocumented/unofficial
(it's what the public careers SPA itself calls, not a published Workday
product) so it could change without notice. If a newly-configured Workday
company in companies.yaml comes back with 0 jobs, verify tenant/wdN/site
by opening that company's careers page and checking the network tab for
the same POST request before assuming the code is wrong.

PERFORMANCE NOTE on fetch_workday (2026-09-26): a big board (NVIDIA's
~2000 postings) originally took minutes — ~100 sequential list-page
requests plus one sequential detail request per title/location match.
_fetch_all_workday_postings now uses the `total` count Workday's API
already returns on page 1 to fetch every remaining page concurrently
(falling back to the old one-at-a-time walk if `total` is missing, so
behavior is unchanged for a tenant that doesn't provide it), and
fetch_workday runs its per-job detail fetches concurrently too. NVIDIA's
full board (2000 jobs, 12 gated-in matches) went from not completing in
150s to ~17s live-verified. Both changes only affect request scheduling —
gating, output shape, and error handling (best-effort, empty description
on failure) are unchanged.

CONFIDENCE NOTE on fetch_bamboohr (2026-09-26): confirmed live against the
two companies already in companies.yaml under this ats type — Explorance
(22 jobs) and ClearRisk (1 job) — for both the list endpoint
(/careers/list) and the per-job detail endpoint (/careers/{id}/detail).

CONFIDENCE NOTE on fetch_rippling (2026-09-26): confirmed live against
several real companies (Unchained, Urban SDK, GenLogs). The public,
unauthenticated endpoint (api.rippling.com/platform/api/ats/v1/board/
{slug}/jobs) is Rippling's own documented Job Board API
(developer.rippling.com/documentation/job-board-api) — not undocumented
like Workday's CXS, so this one's on firmer ground. Its listing endpoint
returns one row per (job, work-location) pair rather than one row per job
— fetch_rippling groups those back together by uuid.

CONFIDENCE NOTE on fetch_teamtailor (2026-09-26): confirmed live against
several real companies (Chip, Yousign/"Youtrust"). Uses the public
`{slug}.teamtailor.com/jobs.json` JSON Feed (jsonfeed.org format) every
Teamtailor career site exposes — no auth, and unlike every other ATS here,
title/location/description/date all come back in ONE request per company,
so there's no gating or per-job detail fetch to write.

CONFIDENCE NOTE on fetch_gem (2026-09-26): Gem's public Job Board API
(help.gem.com) is a different, authenticated product. This instead calls
the SAME undocumented GraphQL batch endpoint (jobs.gem.com/api/public/
graphql/batch) the public careers page's own React app calls — reverse-
engineered from that app's minified JS bundles (2026-09-26) since no
public docs describe it, then confirmed live against 5 real companies
(11x.ai, Aarden, Apartment List, detections.ai, Supio). Being reverse-
engineered from an internal API (like Workday's CXS), it could change
without notice — same caveat, same verification approach if a
newly-added Gem company comes back empty.

CONFIDENCE NOTES on the 12 clients added 2026-09-27 (fetch_oraclecloud,
fetch_successfactors, fetch_icims, fetch_eightfold, fetch_cornerstone,
fetch_avature, fetch_hrdepartment, fetch_gr8people, fetch_google,
fetch_apple, fetch_shopify, fetch_meta): each was built to cover companies
this pipeline had previously checked and deliberately NOT added because
they run an enterprise ATS or fully custom career site with no obvious
public API — a closer look found each one is actually reachable
unauthenticated, just not through a clean documented REST endpoint the
way Greenhouse/Ashby/Lever are. Full detail (exact endpoint, request
shape, response shape, how it was found, and what was verified live) is
in each function's own docstring/inline comment, right above it — kept
there instead of duplicated here since there's a lot of it. The short
version, and the shared caveat across all 12: every one of these is
either an undocumented internal API a company's own frontend calls (same
risk profile as Workday's CXS/Gem's GraphQL API above — could change
without notice) or, where no API exists at all (avature, hrdepartment),
plain HTML scraped with a regex (same approach already used for LinkedIn
in aggregator_clients.py) — more fragile than a typed JSON contract, so
verify a newly-added company under any of these 12 with
`python -m app.main --company <slug>` before trusting it, same as every
other undocumented-API fetcher in this file. One exception:
fetch_gr8people's query shape is confirmed working against the platform
vendor's own demo tenant, but NOT against a real customer — the one real
customer checked (EA) blocks every request from this project's own
network at the edge (a 403 on a plain HTML fetch, unrelated to the query
itself) — so treat a newly-added gr8people company as unverified until
you've confirmed it yourself.
"""
import html
import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import httpx

from app import filters

USER_AGENT = "job-search-pipeline/0.1 (personal use)"
TIMEOUT = 20.0

_MULTI_LOCATION_RE = re.compile(r"^\d+\s+Locations?$", re.IGNORECASE)


def _epoch_millis_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _epoch_seconds_to_iso(sec: int | None) -> str | None:
    if sec is None:
        return None
    return datetime.fromtimestamp(sec, tz=timezone.utc).isoformat()


def _format_money_range(min_val, max_val, currency: str | None, period: str | None) -> str | None:
    """Best-effort human-readable "X–Y CUR/period" string from whatever
    min/max/currency/period values an ATS gives us. None if there's no
    usable number at all (a currency/period with no amount is useless)."""
    def _num(v):
        try:
            return f"{float(v):,.0f}"
        except (TypeError, ValueError):
            return None

    lo, hi = _num(min_val), _num(max_val)
    if not lo and not hi:
        return None
    amount = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi)
    text = " ".join(p for p in (amount, currency) if p)
    return f"{text}/{period}" if period else text


def _lever_interval_to_period(interval: str | None) -> str | None:
    # e.g. "per-year-salary" -> "year", "per-hour-wage" -> "hour".
    if not interval:
        return None
    label = interval.removeprefix("per-")
    for suffix in ("-salary", "-wage"):
        if label.endswith(suffix):
            label = label[: -len(suffix)]
    return label or None


_SMARTRECRUITERS_PERIOD_LABELS = {
    "YEARLY": "year", "MONTHLY": "month", "HOURLY": "hour", "WEEKLY": "week", "DAILY": "day",
}


def _generic_compensation_to_salary(value) -> str | None:
    """Best-effort formatting for an ATS-specific compensation field whose
    exact POPULATED shape isn't confirmed live — the field exists in the
    API (BambooHR's `compensation`, Rippling's `payRangeDetails`) but was
    null/empty in every real posting sampled during development, so this
    can't be pinned down the way Lever/SmartRecruiters/Teamtailor's shapes
    were. Handles a plain string, or a dict with recognizable
    min/max/currency/period-ish keys under common aliases; a list takes its
    first entry (Rippling: one entry per work location). Returns None for
    anything else rather than risk showing garbled text."""
    if isinstance(value, list):
        value = value[0] if value else None
    if not value:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        def pick(*names):
            for n in names:
                v = value.get(n)
                if v is not None:
                    return v
            return None
        min_v = pick("min", "minValue", "low", "minimum")
        max_v = pick("max", "maxValue", "high", "maximum")
        currency = pick("currency", "currencyCode")
        period = pick("period", "interval", "unitText", "unit")
        if min_v is not None or max_v is not None:
            return _format_money_range(min_v, max_v, currency, (period or "").lower() or None)
    return None


_GREENHOUSE_PAY_RANGE_RE = re.compile(
    r'<div class="title">[^<]*</div>\s*<div class="pay-range">\s*<span>\$?([\d,]+)</span>.*?<span>\$?([\d,]+)\s*([A-Za-z]*)</span>',
    re.DOTALL,
)


def _extract_greenhouse_pay_range(content: str) -> str | None:
    """Greenhouse's own pay-transparency compensation module renders into
    the job's `content` HTML as a `<div class="title">LABEL</div><div
    class="pay-range"><span>MIN</span>...<span>MAX CUR</span></div>` block,
    double HTML-entity-encoded in the API response (`&lt;div&gt;...`) —
    confirmed live and IDENTICALLY structured across many unrelated
    companies (Coinbase, Robinhood, Pinterest, Databricks, Airbnb, Samsara,
    Anthropic all matched on real postings), so this is Greenhouse's own
    template, not company-specific text — much more reliable than
    _extract_salary_from_text below. A job open in multiple pay zones lists
    one block per zone; this takes the first. Not every Greenhouse company
    uses this module (Stripe's postings had none in samples checked).

    Period (hour vs. year) is inferred from MAGNITUDE, not the LABEL text
    — caught two real cases live where the label can't be trusted: (1) the
    label is sometimes just a zone/location descriptor (Robinhood: "Zone 1
    (Menlo Park, CA; New York, NY; ...)"), not a period indicator at all;
    (2) it can be a stale copy-paste (Samsara's co-op posting says "Annual
    Base Salary" but lists $35–$58, an hourly rate). No real annual salary
    is under ~$1,000 and essentially no hourly rate reaches four digits, so
    magnitude alone is the more reliable signal.

    Also guards against real data-entry errors seen live IN THE SOURCE
    CONTENT, not this parser: Anthropic had an unfilled "$1 — $2" template
    placeholder; Coinbase had "$152,405 — $179,300,152" (evidently a typo/
    duplication on their end). Both get treated as unparseable — an
    implausible number is worse to show than none at all."""
    if not content or "pay-range" not in content:
        return None
    decoded = html.unescape(html.unescape(content))  # double-encoded, needs two passes
    m = _GREENHOUSE_PAY_RANGE_RE.search(decoded)
    if not m:
        return None
    lo_num, hi_num = int(m.group(1).replace(",", "")), int(m.group(2).replace(",", ""))
    currency = m.group(3)
    mn, mx = min(lo_num, hi_num), max(lo_num, hi_num)
    if mx < 10 or (mn > 0 and mx / mn > 20):
        return None
    period = "hour" if mx < 1000 else "year"
    return _format_money_range(lo_num, hi_num, currency or None, period)


_SALARY_KEYWORD_RE = re.compile(
    r"salary|compensation|base pay|pay range|\bOTE\b|annual pay|hourly rate|individual pay",
    re.IGNORECASE,
)
# Comma-grouped ("182,000") or a bare 4-6 digit number ("182000") — the
# latter added after finding Google's own salary text uses no thousands
# separator at all ("Canada: $182000 - $186000 (CAD)", confirmed live
# 2026-09-27) and was silently missed. Still gated by the keyword-proximity
# check below, so this doesn't meaningfully raise false-positive risk.
_SALARY_NUMBER = r"(?:[\d]{2,3}(?:,\d{3})+(?:\.\d+)?|\d{4,6})"
_SALARY_RANGE_RE = re.compile(
    # \s* (not \s?) around the $ signs: strip_html can leave more than one
    # space where a source page split the amount across two HTML elements
    # (e.g. Avature: "<font>...$</font><span>180000</span>" -> "$  180000",
    # confirmed live) — a single optional space silently missed that.
    # Optional currency-code PREFIX before the $ sign too ("USD $202,000 per
    # year - USD $224,000 per year", confirmed live on Uber 2026-09-28) —
    # the currency-as-suffix form below already covered "182,000 USD", but
    # not this equally common "USD $182,000" ordering.
    # An optional "per year"/"per hour"/"annually" etc. can sit between the
    # first number and the separating dash ("USD $202,000 per year - USD
    # $224,000 per year", same Uber text) — without allowing for it, the
    # dash never got reached and the whole match silently failed.
    rf"(?:(?:USD|CAD|EUR|GBP)\s*)?\$\s*({_SALARY_NUMBER})\s*[kK]?"
    rf"(?:\s*per\s*(?:year|hour|annum|month))?\s*(?:-|–|—|to)\s*"
    rf"(?:(?:USD|CAD|EUR|GBP)\s*)?\$?\s*({_SALARY_NUMBER})\s*[kK]?"
    rf"|(?:({_SALARY_NUMBER})\s*(?:USD|CAD|EUR|GBP)\s*(?:-|–|—|to)\s*({_SALARY_NUMBER})\s*(?:USD|CAD|EUR|GBP))"
)


def _extract_salary_from_text(description: str) -> str | None:
    """Best-effort salary-range extraction from free-text JD prose, for
    ATSes with no structured compensation field at all (Ashby, Workday) or
    where this specific posting's structured field came back empty
    (everywhere else, as a fallback). Only accepts a dollar-range match
    within ~150 characters AFTER a salary-indicating keyword, to avoid
    grabbing an unrelated number range (funding raised, headcount, a
    version number) — validated live against real postings on
    Greenhouse/Ashby/Workday/Workable/Gem with no observed false positive,
    but unlike the structured extractions in this module, this is
    inherently a heuristic over free text, not a real field."""
    if not description:
        return None
    plain = filters.strip_html(description)
    for m in _SALARY_RANGE_RE.finditer(plain):
        window = plain[max(0, m.start() - 150):m.start()]
        if _SALARY_KEYWORD_RE.search(window):
            return " ".join(m.group(0).split())
    return None


def fetch_greenhouse(company_display_name: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = httpx.get(
        url, params={"content": "true"}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        content = j.get("content", "")  # HTML
        # No dedicated salary field in Greenhouse's API itself (checked
        # several companies' `metadata` custom fields, found no consistent
        # name across accounts) — but when a company enables Greenhouse's
        # own pay-transparency module, it's a reliable structured block
        # inside `content` (see _extract_greenhouse_pay_range); when that's
        # not present either, fall back to scanning the JD prose.
        salary = _extract_greenhouse_pay_range(content) or _extract_salary_from_text(content)
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("first_published") or j.get("updated_at"),
            "description": content,
            "salary": salary,
        })
    return jobs


def fetch_ashby(company_display_name: str, slug: str) -> list[dict]:
    # Ashby's public posting-API endpoint (no auth needed for public boards).
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("location") or j.get("locationName") or ""
        description = j.get("descriptionPlain") or j.get("descriptionHtml") or ""
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "posted_at": j.get("publishedAt"),
            "description": description,
            # No compensation field anywhere in Ashby's public posting API
            # (confirmed live across several companies) — not even a null
            # key. Some companies do disclose it in the JD prose instead
            # (Docker: ~half of postings; Cerebras: some; OpenAI/Snowflake:
            # none observed) — worth the scan since there's no dedicated
            # field to prefer over it.
            "salary": _extract_salary_from_text(description),
        })
    return jobs


def fetch_workable(company_display_name: str, slug: str) -> list[dict]:
    # Workable's public widget API.
    url = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("location") or {}
        loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
        if loc.get("workplace") == "remote":
            loc_str = f"Remote ({loc_str})" if loc_str else "Remote"
        # Workable's widget API doesn't reliably include a description
        # field across all accounts — treat missing as unknown, not
        # as "no dealbreaker language", filters.py handles empty safely.
        description = j.get("description", "")
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": loc_str,
            "url": j.get("url") or j.get("shortlink", ""),
            "posted_at": j.get("published_on") or j.get("created_at"),
            "description": description,
            # No dedicated salary field in Workable's widget API (confirmed
            # live); some accounts disclose it in the description prose.
            "salary": _extract_salary_from_text(description),
        })
    return jobs


def fetch_lever(company_display_name: str, slug: str) -> list[dict]:
    # Lever's public postings API.
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data:
        cats = j.get("categories", {}) or {}
        loc = cats.get("location", "")
        all_locs = cats.get("allLocations") or []
        if all_locs:
            loc = ", ".join(all_locs)
        # Lever's public API DOES expose a structured salary field when a
        # company has pay transparency enabled (confirmed live — real data
        # on several companies, e.g. {"min": 150000, "max": 200000,
        # "currency": "USD", "interval": "per-year-salary"}); empty/absent
        # otherwise, same as everything else here.
        salary_range = j.get("salaryRange") or {}
        description = j.get("descriptionPlain") or j.get("description", "")
        salary = _format_money_range(
            salary_range.get("min"), salary_range.get("max"),
            salary_range.get("currency"), _lever_interval_to_period(salary_range.get("interval")),
        ) or _extract_salary_from_text(description)
        jobs.append({
            "company": company_display_name,
            "title": j.get("text", ""),
            "location": loc,
            "url": j.get("hostedUrl", ""),
            "posted_at": _epoch_millis_to_iso(j.get("createdAt")),
            "description": description,
            "salary": salary,
        })
    return jobs


def _fetch_smartrecruiters_detail(slug: str, posting_id: str) -> dict:
    """SmartRecruiters' list endpoint (fetch_smartrecruiters below) doesn't
    include the JD text — only the per-posting detail endpoint does, one
    extra request per job. Best-effort: any failure (private board, 404,
    timeout) just means no description/salary, not a crashed run;
    filters.py treats an empty description as "unknown, don't reject on
    stack alone".

    The same detail response also carries a structured `compensation`
    field ({"min", "max", "currency", "period"}, e.g. {"min": 150000,
    "max": 170000, "currency": "CAD", "period": "YEARLY"}) when the
    company discloses pay — confirmed live — so this comes for free
    alongside the description, no extra request."""
    try:
        resp = httpx.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}",
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        sections = (data.get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(key) or {}).get("text", "")
            for key in ("jobDescription", "qualifications", "additionalInformation")
        ]
        description = "\n\n".join(p for p in parts if p)
        comp = data.get("compensation") or {}
        salary = _format_money_range(
            comp.get("min"), comp.get("max"), comp.get("currency"),
            _SMARTRECRUITERS_PERIOD_LABELS.get(comp.get("period"), (comp.get("period") or "").lower() or None),
        ) or _extract_salary_from_text(description)
        return {"description": description, "salary": salary}
    except Exception:
        return {"description": "", "salary": None}


def fetch_smartrecruiters(company_display_name: str, slug: str) -> list[dict]:
    # SmartRecruiters' public Posting API — only enabled per-account (not
    # every customer turns it on), same "might just 404" caveat as the
    # other ATSes here. Paginated via limit/offset, 100 postings per page.
    jobs = []
    offset = 0
    limit = 100
    while True:
        url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
        resp = httpx.get(
            url, params={"limit": limit, "offset": offset},
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [])
        if not content:
            break

        for p in content:
            loc = p.get("location") or {}
            loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("region"), loc.get("country")]))
            if loc.get("remote"):
                loc_str = f"Remote ({loc_str})" if loc_str else "Remote"

            title = p.get("name", "")
            posting_id = p.get("id", "")
            job_url = p.get("applyUrl") or p.get("ref") or (
                f"https://jobs.smartrecruiters.com/{slug}/{posting_id}" if posting_id else ""
            )
            if not job_url:
                # No applyUrl/ref/id to build any identifier from — skip
                # rather than store url="", which would make dedup.is_new()
                # treat every subsequent url-less posting as a duplicate of
                # the first one (an exact-match dedup key collision).
                continue

            description = ""
            salary = None
            # Only worth the extra per-job request (see
            # _fetch_smartrecruiters_detail) for postings that already
            # look like real candidates — same gating idea as
            # aggregator_clients.fetch_adzuna uses for its full-JD fetch,
            # so a company with hundreds of postings doesn't turn into
            # hundreds of extra requests for roles that'd get filtered
            # out on title/location alone anyway. Salary rides along with
            # that same request, so it's only ever populated for postings
            # that pass this gate too.
            if posting_id and filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
                detail = _fetch_smartrecruiters_detail(slug, posting_id)
                description = detail["description"]
                salary = detail["salary"]

            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": loc_str,
                "url": job_url,
                "posted_at": p.get("releasedDate"),
                "description": description,
                "salary": salary,
            })

        if len(content) < limit:
            break
        offset += limit
    return jobs


def _fetch_workday_detail(base_url: str, external_path: str) -> dict:
    """The list endpoint only gives a locale-formatted `postedOn` string
    ("Posted 3 Days Ago") and no description at all — both live on the
    per-job detail endpoint, one extra request per job. Best-effort: any
    failure (bad tenant config, timeout) just means no description/date,
    not a crashed run, same as _fetch_smartrecruiters_detail.

    Also resolves the real location(s): a multi-location posting's list-page
    `locationsText` is just "5 Locations", but the detail response has the
    primary `location` plus an `additionalLocations` list with the actual
    place names — needed for filters.location_is_allowed to mean anything.

    No structured compensation field anywhere in the CXS API (checked
    jobPostingInfo's full key list across 4 different tenants), but when
    disclosed at all it's free text inside `jobDescription` (confirmed
    live on NVIDIA — "The base salary range is 224,000 USD - 356,500 USD
    for Level 3...") — worth the scan."""
    try:
        resp = httpx.get(
            f"{base_url}{external_path}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo") or {}
        locations = [info["location"]] if info.get("location") else []
        locations += info.get("additionalLocations") or []
        description = info.get("jobDescription", "")
        return {
            "description": description,
            "posted_at": info.get("startDate"),
            "location": ", ".join(locations) or None,
            "salary": _extract_salary_from_text(description),
        }
    except Exception:
        return {"description": "", "posted_at": None, "location": None, "salary": None}


_WORKDAY_DETAIL_WORKERS = 8  # bounded so a big board doesn't hammer the tenant's API
_WORKDAY_LIST_WORKERS = 8


def _fetch_workday_page(base: str, offset: int, limit: int) -> list[dict]:
    resp = httpx.post(
        f"{base}/jobs",
        json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("jobPostings", [])


def _fetch_all_workday_postings(base: str) -> list[dict]:
    """CXS hard-caps each page at 20 postings but does return the true
    `total` count on the very first page. Once we have that, every
    remaining page is independent and can be fetched concurrently instead
    of walked one at a time — this is what made a big board (NVIDIA's
    ~2000 postings, ~100 sequential pages) slow. Falls back to the
    original page-by-page walk if a tenant doesn't return a usable `total`
    (treated as unknown, not zero) — slower, but exactly as correct as
    the walk always was."""
    limit = 20  # hard page size — CXS silently returns an empty page above this
    resp = httpx.post(
        f"{base}/jobs",
        json={"appliedFacets": {}, "limit": limit, "offset": 0, "searchText": ""},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    first_page_json = resp.json()
    first_page = first_page_json.get("jobPostings", [])
    if len(first_page) < limit:
        return first_page  # everything fit on page 1

    total = first_page_json.get("total")
    if not isinstance(total, int) or total <= limit:
        # Unknown or inconsistent total — fall back to the safe sequential
        # walk (identical to the pre-optimization behavior).
        postings = list(first_page)
        offset = limit
        while True:
            page = _fetch_workday_page(base, offset, limit)
            if not page:
                break
            postings.extend(page)
            if len(page) < limit:
                break
            offset += limit
        return postings

    postings = list(first_page)
    offsets = range(limit, total, limit)
    with ThreadPoolExecutor(max_workers=_WORKDAY_LIST_WORKERS) as pool:
        # pool.map preserves input order in its results even though the
        # pages are fetched concurrently, so postings stay in the same
        # overall order the sequential walk would have produced.
        for page in pool.map(lambda off: _fetch_workday_page(base, off, limit), offsets):
            postings.extend(page)
    return postings


def fetch_workday(company_display_name: str, slug: str) -> list[dict]:
    # Workday-hosted career sites (*.myworkdayjobs.com) are backed by the
    # same public, unauthenticated CXS ("Candidate Experience System") API
    # their own SPA calls. `slug` here is "{tenant}.wd{N}/{site}" — e.g. a
    # careers URL like https://nvidia.wd5.myworkdayjobs.com/en-US/
    # NVIDIAExternalCareerSite/... gives slug "nvidia.wd5/NVIDIAExternalCareerSite".
    # See companies.yaml's header comment for how to read this off a real URL.
    host, _, site = slug.partition("/")
    tenant = host.split(".")[0]
    base = f"https://{host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}"

    # With title/location gating still done inline here (cheap, no I/O), we
    # first collect every job needing a detail fetch, then run those
    # requests concurrently instead of one-at-a-time — same total request
    # count, same gating, just not serialized. This is on top of
    # _fetch_all_workday_postings already parallelizing the list pagination
    # itself — together these are what made a big board (NVIDIA's ~2000
    # postings) slow.
    jobs = []
    pending_detail = []  # (index into `jobs`, external_path)
    for p in _fetch_all_workday_postings(base):
        title = p.get("title", "")
        loc = p.get("locationsText", "") or ""
        external_path = p.get("externalPath", "")
        if not external_path:
            # No externalPath to build a URL from — skip rather than
            # store url="", same dedup-collision reasoning as
            # fetch_smartrecruiters' missing-job_url case.
            continue

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc,
            "url": f"https://{host}.myworkdayjobs.com/{site}{external_path}",
            "posted_at": None,
            "description": "",
            "salary": None,
        })

        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea as
        # fetch_smartrecruiters/fetch_adzuna, so a Workday tenant with
        # thousands of postings doesn't turn into thousands of extra
        # requests for roles that'd get filtered out on title/location
        # alone anyway. An aggregate "N Locations" string can't be
        # matched against location_allow_patterns at all, so treat it as
        # unknown-not-disqualifying and resolve it via the detail fetch
        # rather than dropping a job that might well match.
        if filters.title_is_relevant(title) and (
            _MULTI_LOCATION_RE.match(loc.strip()) or filters.location_is_allowed(loc)
        ):
            pending_detail.append((len(jobs) - 1, external_path))

    if pending_detail:
        with ThreadPoolExecutor(max_workers=_WORKDAY_DETAIL_WORKERS) as pool:
            future_to_index = {
                pool.submit(_fetch_workday_detail, base, external_path): idx
                for idx, external_path in pending_detail
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["description"] = detail["description"]
                jobs[idx]["posted_at"] = detail["posted_at"]
                jobs[idx]["salary"] = detail["salary"]
                if detail["location"]:
                    jobs[idx]["location"] = detail["location"]

    return jobs


def _fetch_bamboohr_detail(base_url: str, job_id: str) -> dict:
    """The list endpoint has no description and no real posted date — both
    live on the per-job detail endpoint, one extra request per job.
    Best-effort: any failure just means no description/date, not a crashed
    run, same as _fetch_smartrecruiters_detail.

    The detail response does have a `compensation` field (confirmed it
    exists live) but every real posting sampled during development had it
    null, so its populated shape is unconfirmed —
    _generic_compensation_to_salary makes a best effort."""
    try:
        resp = httpx.get(
            f"{base_url}/{job_id}/detail",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        info = (resp.json().get("result") or {}).get("jobOpening") or {}
        description = info.get("description", "")
        salary = _generic_compensation_to_salary(info.get("compensation")) or _extract_salary_from_text(description)
        return {"description": description, "posted_at": info.get("datePosted"), "salary": salary}
    except Exception:
        return {"description": "", "posted_at": None, "salary": None}


def fetch_bamboohr(company_display_name: str, slug: str) -> list[dict]:
    # Every BambooHR customer gets a standalone public careers site at
    # {slug}.bamboohr.com/careers (e.g. https://explorance.bamboohr.com/careers/)
    # backed by an unauthenticated JSON API — `slug` here is just that
    # subdomain, same idea as Greenhouse's board slug. Unlike the paginated
    # ATSes above, /careers/list returns every open posting in one response
    # (no offset/limit — confirmed live against Explorance and ClearRisk,
    # both companies already in companies.yaml under this ats type).
    base = f"https://{slug}.bamboohr.com/careers"
    resp = httpx.get(
        f"{base}/list", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    postings = resp.json().get("result", [])

    jobs = []
    for p in postings:
        job_id = p.get("id")
        title = p.get("jobOpeningName", "")
        loc = p.get("location") or {}
        loc_str = ", ".join(filter(None, [loc.get("city"), loc.get("state")]))
        if not job_id:
            # No id to build a URL or detail request from — skip rather
            # than store url="", same dedup-collision reasoning as
            # fetch_smartrecruiters' missing-job_url case.
            continue

        description = ""
        posted_at = None
        salary = None
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea as
        # fetch_smartrecruiters/fetch_workday.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_bamboohr_detail(base, job_id)
            description = detail["description"]
            posted_at = detail["posted_at"]
            salary = detail["salary"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": f"{base}/{job_id}",
            "posted_at": posted_at,
            "description": description,
            "salary": salary,
        })
    return jobs


def _fetch_rippling_detail(slug: str, job_uuid: str) -> dict:
    """The list endpoint has no description at all — it lives on the
    per-job detail endpoint, one extra request per job. Best-effort: any
    failure just means no description/date, not a crashed run.

    The detail response does have a `payRangeDetails` list (pay range per
    work location, if disclosed) but every real posting sampled during
    development had it empty, so its populated shape is unconfirmed —
    _generic_compensation_to_salary makes a best effort."""
    try:
        resp = httpx.get(
            f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{job_uuid}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        info = resp.json()
        desc = info.get("description") or {}
        # "company" and "role" are separate HTML fragments (about-the-company
        # blurb + the actual role description) — join them into one JD.
        description = "\n\n".join(v for v in (desc.get("company"), desc.get("role")) if v)
        salary = _generic_compensation_to_salary(info.get("payRangeDetails")) or _extract_salary_from_text(description)
        return {"description": description, "posted_at": info.get("createdOn"), "salary": salary}
    except Exception:
        return {"description": "", "posted_at": None, "salary": None}


def fetch_rippling(company_display_name: str, slug: str) -> list[dict]:
    # Rippling's public, documented Job Board API
    # (developer.rippling.com/documentation/job-board-api) — `slug` here is
    # the board slug from the company's careers URL, e.g.
    # https://ats.rippling.com/urban-sdk/jobs gives slug "urban-sdk".
    resp = httpx.get(
        f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    postings = resp.json()
    if not isinstance(postings, list):
        postings = []

    # The list endpoint returns one row per (job, work-location) pair — the
    # same job uuid repeats once per location it's open in — rather than one
    # row per job. Group those back together before deciding anything.
    by_uuid: dict[str, dict] = {}
    for p in postings:
        job_uuid = p.get("uuid")
        if not job_uuid:
            continue
        entry = by_uuid.setdefault(job_uuid, {"name": p.get("name", ""), "url": p.get("url", ""), "locations": []})
        loc = (p.get("workLocation") or {}).get("label")
        if loc and loc not in entry["locations"]:
            entry["locations"].append(loc)

    jobs = []
    for job_uuid, entry in by_uuid.items():
        if not entry["url"]:
            # No URL to link to or fetch detail from — skip rather than
            # store url="", same dedup-collision reasoning as
            # fetch_smartrecruiters' missing-job_url case.
            continue
        title = entry["name"]
        loc_str = ", ".join(entry["locations"])

        description = ""
        posted_at = None
        salary = None
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea used throughout this
        # module.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_rippling_detail(slug, job_uuid)
            description = detail["description"]
            posted_at = detail["posted_at"]
            salary = detail["salary"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": entry["url"],
            "posted_at": posted_at,
            "description": description,
            "salary": salary,
        })
    return jobs


def _teamtailor_salary(jobposting: dict) -> str | None:
    # schema.org MonetaryAmount, confirmed live and populated on real
    # companies (Yousign, Implicity) — e.g. {"currency": "EUR", "value":
    # {"unitText": "YEAR", "minValue": "65000", "maxValue": "85000"}}, or a
    # single "value" instead of a min/max range for a fixed salary.
    base_salary = jobposting.get("baseSalary")
    if not base_salary:
        return None
    currency = base_salary.get("currency") or None
    value = base_salary.get("value") or {}
    period = (value.get("unitText") or "").lower() or None
    min_v, max_v, single = value.get("minValue"), value.get("maxValue"), value.get("value")
    if min_v is not None or max_v is not None:
        return _format_money_range(min_v, max_v, currency, period)
    if single is not None:
        return _format_money_range(single, None, currency, period)
    return None


def fetch_teamtailor(company_display_name: str, slug: str) -> list[dict]:
    # Every Teamtailor career site exposes a public JSON Feed
    # (jsonfeed.org format, no auth) at {slug}.teamtailor.com/jobs.json —
    # `slug` here is just that subdomain. Unlike every other ATS in this
    # module, title/location/full-description/posted-date all come back in
    # this ONE request per company — schema.org JobPosting data is embedded
    # under each item's `_jobposting` key — so there's no gating or
    # per-job detail fetch needed here at all.
    resp = httpx.get(
        f"https://{slug}.teamtailor.com/jobs.json",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("items", [])

    jobs = []
    for it in items:
        jobposting = it.get("_jobposting") or {}
        loc_parts = []
        for loc in jobposting.get("jobLocation") or []:
            addr = loc.get("address") or {}
            loc_parts.append(
                ", ".join(filter(None, [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]))
            )
        content_html = it.get("content_html", "")
        jobs.append({
            "company": company_display_name,
            "title": it.get("title", ""),
            "location": "; ".join(filter(None, loc_parts)),
            "url": it.get("url", ""),
            "posted_at": it.get("date_published"),
            "description": content_html,
            "salary": _teamtailor_salary(jobposting) or _extract_salary_from_text(content_html),
        })
    return jobs


_GEM_LIST_QUERY = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      extId
      title
      locations { name city isoCountry isRemote }
    }
  }
}
"""

_GEM_DETAIL_QUERY = """
query ExternalJobPosting($boardId: String!, $extId: String!) {
  oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
    descriptionHtml
    firstPublishedTsSec
    compensationHtml
  }
}
"""


def _gem_graphql(operation_name: str, query: str, variables: dict) -> dict:
    resp = httpx.post(
        "https://jobs.gem.com/api/public/graphql/batch",
        json=[{"operationName": operation_name, "variables": variables, "query": query}],
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()[0].get("data") or {}


def _fetch_gem_detail(slug: str, ext_id: str) -> dict:
    """The list query has no description at all — it lives on the per-job
    detail query, one extra request per job. Best-effort: any failure just
    means no description/date, not a crashed run.

    `compensationHtml` (a short HTML fragment, e.g. "Salary range: $X -
    $Y") was null on every real posting sampled during development, but
    the field itself is real — confirmed present in the app's own GraphQL
    query — so it's extracted (stripped to plain text) when populated."""
    try:
        data = _gem_graphql("ExternalJobPosting", _GEM_DETAIL_QUERY, {"boardId": slug, "extId": ext_id})
        info = data.get("oatsExternalJobPosting") or {}
        comp_html = info.get("compensationHtml")
        description = info.get("descriptionHtml", "")
        salary = (filters.strip_html(comp_html).strip() if comp_html else None) or _extract_salary_from_text(description)
        return {
            "description": description,
            "posted_at": _epoch_seconds_to_iso(info.get("firstPublishedTsSec")),
            "salary": salary,
        }
    except Exception:
        return {"description": "", "posted_at": None, "salary": None}


def fetch_gem(company_display_name: str, slug: str) -> list[dict]:
    # Gem's DOCUMENTED Job Board API (help.gem.com) is a different,
    # authenticated product — this instead calls the same undocumented
    # GraphQL batch endpoint the public careers page's own React app calls.
    # `slug` here is the vanity path from that page's URL, e.g.
    # https://jobs.gem.com/11x-ai gives slug "11x-ai" (it's passed straight
    # through as the GraphQL boardId — no separate lookup needed).
    data = _gem_graphql("JobBoardList", _GEM_LIST_QUERY, {"boardId": slug})
    postings = ((data.get("oatsExternalJobPostings") or {}).get("jobPostings")) or []

    jobs = []
    for p in postings:
        title = p.get("title", "")
        ext_id = p.get("extId")
        loc_str = ", ".join(filter(None, (loc.get("name") for loc in (p.get("locations") or []))))
        if not ext_id:
            # No extId to build a URL or detail request from — skip rather
            # than store url="", same dedup-collision reasoning as
            # fetch_smartrecruiters' missing-job_url case.
            continue

        description = ""
        posted_at = None
        salary = None
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea used throughout this
        # module.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_gem_detail(slug, ext_id)
            description = detail["description"]
            posted_at = detail["posted_at"]
            salary = detail["salary"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": f"https://jobs.gem.com/{slug}/{ext_id}",
            "posted_at": posted_at,
            "description": description,
            "salary": salary,
        })
    return jobs


def _fetch_oraclecloud_detail(host: str, site: str, job_id: str) -> dict:
    """List endpoint has no description at all — one extra request per job,
    same shape as _fetch_workday_detail. No structured salary field exists
    anywhere in either endpoint (checked the full key list on 3 different
    tenants) — only the free-text fallback applies."""
    try:
        resp = httpx.get(
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
            params={"finder": f'ById;Id="{job_id}",siteNumber={site}'},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        items = resp.json().get("items") or [{}]
        info = items[0] if items else {}
        description = info.get("ExternalDescriptionStr", "") or ""
        responsibilities = info.get("ExternalResponsibilitiesStr", "") or ""
        qualifications = info.get("ExternalQualificationsStr", "") or ""
        full_description = "\n".join(p for p in (description, responsibilities, qualifications) if p)
        return {
            "description": full_description,
            "posted_at": info.get("ExternalPostedStartDate"),
            "salary": _extract_salary_from_text(full_description),
        }
    except Exception:
        return {"description": "", "posted_at": None, "salary": None}


_ORACLECLOUD_LIST_WORKERS = 8
_ORACLECLOUD_DETAIL_WORKERS = 8


def _fetch_oraclecloud_page(host: str, site: str, offset: int, limit: int) -> list[dict]:
    resp = httpx.get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
        params={
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
            "finder": f"findReqs;siteNumber={site},limit={limit},offset={offset},sortBy=POSTING_DATES_DESC",
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    items = resp.json().get("items") or [{}]
    return (items[0] if items else {}).get("requisitionList") or []


def fetch_oraclecloud(company_display_name: str, slug: str) -> list[dict]:
    # Oracle Fusion Cloud Recruiting ("Candidate Experience") sites expose a
    # public, unauthenticated REST API (hcmRestApi/resources/latest/
    # recruitingCEJobRequisitions) that the careers page's own frontend
    # calls — confirmed live 2026-09-27 against Oracle itself, Dell, Texas
    # Instruments, Emerson, and ON Semiconductor. `slug` is "{host}/{site-id}"
    # — {host} is whatever hostname the company's real public careers page
    # redirects to (a raw *.fa.{region}.oraclecloud.com host, or a
    # customer's own CNAME'd vanity domain — both answer this API directly,
    # no need to reconstruct the raw hostname), {site-id} is the trailing
    # path segment of that redirect (e.g. careers.ti.com -> .../en/sites/CX
    # gives site-id "CX"). See companies.yaml's header comment.
    host, _, site = slug.partition("/")
    limit = 200  # server hard-caps the page size here regardless of what's requested

    first_resp = httpx.get(
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
        params={
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
            "finder": f"findReqs;siteNumber={site},limit={limit},offset=0,sortBy=POSTING_DATES_DESC",
        },
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    first_resp.raise_for_status()
    first_items = first_resp.json().get("items") or [{}]
    first_item = first_items[0] if first_items else {}
    total = first_item.get("TotalJobsCount") or 0
    postings = list(first_item.get("requisitionList") or [])

    if total > len(postings):
        offsets = range(limit, total, limit)
        with ThreadPoolExecutor(max_workers=_ORACLECLOUD_LIST_WORKERS) as pool:
            for page in pool.map(lambda off: _fetch_oraclecloud_page(host, site, off, limit), offsets):
                postings.extend(page)

    jobs = []
    pending_detail = []  # (index into `jobs`, job_id)
    for p in postings:
        job_id = p.get("Id")
        title = p.get("Title", "")
        loc = p.get("PrimaryLocation", "") or ""
        if not job_id:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc,
            "url": f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{job_id}",
            "posted_at": p.get("PostedDate"),
            "description": "",
            "salary": None,
        })
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc):
            pending_detail.append((len(jobs) - 1, job_id))

    if pending_detail:
        with ThreadPoolExecutor(max_workers=_ORACLECLOUD_DETAIL_WORKERS) as pool:
            future_to_index = {
                pool.submit(_fetch_oraclecloud_detail, host, site, job_id): idx
                for idx, job_id in pending_detail
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["description"] = detail["description"]
                if detail["posted_at"]:
                    jobs[idx]["posted_at"] = detail["posted_at"]
                jobs[idx]["salary"] = detail["salary"]

    return jobs


_SUCCESSFACTORS_NS = {"g": "http://base.google.com/ns/1.0"}


def fetch_successfactors(company_display_name: str, slug: str) -> list[dict]:
    # SAP SuccessFactors Career Site Builder sites are classic server-
    # rendered jQuery pages with no JSON SPA API — but every one of them
    # exposes a public, unauthenticated "Google for Jobs" XML feed with the
    # full job list AND full HTML descriptions in ONE request, no
    # pagination or per-job detail fetch needed (closer to fetch_teamtailor
    # than fetch_workday). `slug` is just the company's own careers
    # hostname (e.g. "jobs.bce.ca") — see companies.yaml's header comment.
    # Confirmed live 2026-09-27 against BCE, Corning, Rogers, and TELUS.
    # `/sitemap.xml` serves this feed on some tenants but NOT all (TELUS
    # reserves it for a plain URL sitemap instead) — `/googleforjobs.xml`
    # is the one path confirmed to work identically across all 4.
    resp = httpx.get(
        f"https://{slug}/googleforjobs.xml",
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    jobs = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        location = (item.findtext("g:location", namespaces=_SUCCESSFACTORS_NS) or "").strip()
        description_raw = item.findtext("description") or ""
        # SF double-HTML-escapes the CDATA body, same as Greenhouse's own
        # pay-range block — needs two unescape passes to get real HTML.
        description = html.unescape(html.unescape(description_raw))
        if not link:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": link,
            "posted_at": None,  # not present in this feed on any tenant checked
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


def fetch_icims(company_display_name: str, slug: str) -> list[dict]:
    # iCIMS "Attract"-branded custom career-site front-ends (NOT the classic
    # {tenant}.icims.com portal, which is WAF/CAPTCHA-blocked to a
    # non-browser client) expose a public, unauthenticated /api/jobs
    # endpoint their own SPA calls — confirmed live 2026-09-27 on AMD (1250
    # jobs) and Keysight (636 jobs). `slug` is the company's own custom
    # careers domain (e.g. "careers.amd.com"), NOT an icims.com subdomain —
    # see companies.yaml's header comment. Full HTML description comes back
    # in the SAME response as the listing, no per-job detail fetch needed.
    # Page size is hard-fixed at 10 server-side regardless of any
    # `num`/`size` param tried — pagination is `page=1,2,3...` only.
    jobs = []
    page = 1
    while True:
        resp = httpx.get(
            f"https://{slug}/api/jobs",
            params={"page": page},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        page_jobs = resp.json().get("jobs") or []
        if not page_jobs:
            break
        for entry in page_jobs:
            j = entry.get("data") or {}
            title = j.get("title", "")
            location = j.get("location_name") or ""
            apply_url = j.get("apply_url", "")
            if not apply_url:
                continue
            description = j.get("description", "") or ""
            salary_min, salary_max = j.get("salary_min_value"), j.get("salary_max_value")
            salary = (
                (_format_money_range(salary_min, salary_max, None, None) if (salary_min or salary_max) else None)
                or _extract_salary_from_text(description)
            )
            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": location,
                "url": apply_url,
                "posted_at": j.get("posted_date"),
                "description": description,
                "salary": salary,
            })
        page += 1
        if page > 500:  # sanity guard against an unbounded loop on a malformed response
            break
    return jobs


def _fetch_eightfold_detail(host: str, domain: str, job_id) -> dict | None:
    """Returns the raw detail JSON (title/locations/job_description/etc all
    live in one shape, list vs. detail just differ in whether
    job_description is populated) or None on any failure — best-effort,
    same as every other detail fetch in this module."""
    try:
        resp = httpx.get(
            f"https://{host}/api/apply/v2/jobs/{job_id}",
            params={"domain": domain},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


_EIGHTFOLD_SITEMAP_JOB_RE = re.compile(r"/careers/job/(\d+)-([a-z0-9-]+)")
_EIGHTFOLD_LIST_WORKERS = 8
_EIGHTFOLD_DETAIL_WORKERS = 8


def fetch_eightfold(company_display_name: str, slug: str) -> list[dict]:
    # Eightfold AI-powered career sites expose a public, unauthenticated
    # search API their own SPA calls (.../api/apply/v2/jobs?domain=...) —
    # confirmed live 2026-09-27 on Netflix (484 jobs). `slug` is
    # "{host}/{domain}" — {host} is whatever domain the careers site itself
    # lives on (may be *.eightfold.ai or the company's own custom domain),
    # {domain} is the company's plain public domain used as the API's own
    # company-scoping param (e.g. "netflix.com") — found embedded in the
    # careers page's own HTML (a `"domain":"..."` field in an inline
    # pcsx-data script block). See companies.yaml's header comment.
    #
    # Some tenants (Lumen, Microsoft) block this search endpoint outright
    # (403 "Not authorized for PCSX") even though the single-job DETAIL
    # endpoint on the same host stays open — confirmed live on both. Falls
    # back to walking /careers/sitemap.xml (lists every job's canonical
    # URL, id embedded in the slug) and fetching each job's detail directly
    # when that happens; title is parsed out of the URL slug for gating
    # (no other pre-detail signal exists on that path) and an ambiguous
    # parse is fetched anyway rather than silently dropped.
    host, _, domain = slug.partition("/")
    jobs = []

    first = httpx.get(
        f"https://{host}/api/apply/v2/jobs",
        params={"domain": domain, "start": 0, "num": 10},
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )

    if first.status_code == 200:
        first_json = first.json()
        total = first_json.get("count") or 0
        positions = list(first_json.get("positions") or [])

        if total > len(positions):
            offsets = range(10, total, 10)

            def _page(start):
                r = httpx.get(
                    f"https://{host}/api/apply/v2/jobs",
                    params={"domain": domain, "start": start, "num": 10},
                    headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                    timeout=TIMEOUT,
                )
                r.raise_for_status()
                return r.json().get("positions") or []

            with ThreadPoolExecutor(max_workers=_EIGHTFOLD_LIST_WORKERS) as pool:
                for page in pool.map(_page, offsets):
                    positions.extend(page)

        pending_detail = []  # (index into `jobs`, job_id)
        for p in positions:
            job_id = p.get("id")
            title = p.get("name", "")
            locations = p.get("locations") or ([p["location"]] if p.get("location") else [])
            location = ", ".join(locations)
            url = p.get("canonicalPositionUrl", "")
            if not job_id or not url:
                continue
            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": location,
                "url": url,
                "posted_at": _epoch_seconds_to_iso(p.get("t_create")),
                "description": "",
                "salary": None,
            })
            if filters.title_is_relevant(title) and filters.location_is_allowed(location):
                pending_detail.append((len(jobs) - 1, job_id))

        if pending_detail:
            with ThreadPoolExecutor(max_workers=_EIGHTFOLD_DETAIL_WORKERS) as pool:
                future_to_index = {
                    pool.submit(_fetch_eightfold_detail, host, domain, job_id): idx
                    for idx, job_id in pending_detail
                }
                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    info = future.result() or {}
                    description = info.get("job_description", "") or ""
                    jobs[idx]["description"] = description
                    jobs[idx]["salary"] = _extract_salary_from_text(description)

        return jobs

    # Search endpoint blocked (403) — fall back to the sitemap. `domain`
    # MUST be passed here too: some tenants (Qualcomm confirmed live
    # 2026-09-27) are hosted on the generic shared app.eightfold.ai rather
    # than their own dedicated subdomain, and that shared host's
    # /careers/sitemap.xml silently returns EIGHTFOLD'S OWN jobs instead of
    # an error if `domain` is left off — a real, not hypothetical, way to
    # end up quietly attributing a different company's postings to this
    # one. Each sitemap `<loc>` also echoes `?domain=...`, so filtering on
    # that is a second, cheap line of defense against the same failure
    # mode even if some other tenant configuration ignores the param.
    sitemap = httpx.get(
        f"https://{host}/careers/sitemap.xml",
        params={"domain": domain},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    sitemap.raise_for_status()
    job_ids = [
        (m.group(1), m.group(2).replace("-", " "))
        for m in _EIGHTFOLD_SITEMAP_JOB_RE.finditer(sitemap.text)
        if f"domain={domain}" in sitemap.text[m.start():m.start() + 300]
    ]

    def _fetch_and_gate(job_id, slug_title):
        if slug_title and not filters.title_is_relevant(slug_title):
            return None
        return job_id, slug_title, _fetch_eightfold_detail(host, domain, job_id)

    with ThreadPoolExecutor(max_workers=_EIGHTFOLD_DETAIL_WORKERS) as pool:
        for result in pool.map(lambda t: _fetch_and_gate(*t), job_ids):
            if result is None:
                continue
            job_id, slug_title, info = result
            if not info:
                continue
            description = info.get("job_description", "") or ""
            if not description:
                continue
            locations = info.get("locations") or ([info["location"]] if info.get("location") else [])
            location = ", ".join(locations)
            if location and not filters.location_is_allowed(location):
                continue
            jobs.append({
                "company": company_display_name,
                "title": info.get("name") or slug_title.title(),
                "location": location,
                "url": info.get("canonicalPositionUrl") or f"https://{host}/careers/job/{job_id}?domain={domain}",
                "posted_at": _epoch_seconds_to_iso(info.get("t_create")),
                "description": description,
                "salary": _extract_salary_from_text(description),
            })
    return jobs


def _fetch_cornerstone_session(tenant: str, career_site_id: str) -> tuple[str, str, dict]:
    """CSOD needs a short-lived anonymous JWT + session cookies before any
    API call works — both come from just loading the career site's own
    home page once. Returns (token, cloud_host, cookies)."""
    resp = httpx.get(
        f"https://{tenant}.csod.com/ux/ats/careersite/{career_site_id}/home",
        params={"c": tenant},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
    )
    resp.raise_for_status()
    token_match = re.search(r'"token"\s*:\s*"([^"]+)"', resp.text)
    cloud_match = re.search(r'"cloud"\s*:\s*"([^"]+)"', resp.text)
    if not token_match or not cloud_match:
        raise RuntimeError("csod: could not find session token/cloud host on home page")
    cloud_host = cloud_match.group(1).removeprefix("https://").removeprefix("http://").rstrip("/")
    return token_match.group(1), cloud_host, dict(resp.cookies)


def _fetch_cornerstone_detail(tenant: str, token: str, cookies: dict, requisition_id) -> dict:
    try:
        resp = httpx.get(
            f"https://{tenant}.csod.com/services/x/job-requisition/v2/requisitions/{requisition_id}/jobDetails",
            params={"cultureId": 1},
            headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}"},
            cookies=cookies,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        info = resp.json().get("data") or {}
        description = info.get("externalDescription", "") or ""
        return {
            "description": description,
            "posted_at": info.get("openDate"),
            "url": info.get("companyApplyUrl"),
            "salary": _extract_salary_from_text(description),
        }
    except Exception:
        return {"description": "", "posted_at": None, "url": None, "salary": None}


_CORNERSTONE_DETAIL_WORKERS = 8


def fetch_cornerstone(company_display_name: str, slug: str) -> list[dict]:
    # Cornerstone OnDemand (CSOD) career sites expose a real, undocumented
    # JSON API their own SPA calls — confirmed live 2026-09-27 against MACOM
    # (236 jobs). `slug` is "{tenant}/{careerSiteId}" — both read off the
    # company's real careers URL, e.g. macomtech.csod.com/ux/ats/careersite/
    # 4/home?c=macomtech gives slug "macomtech/4". See companies.yaml's
    # header comment. Unlike every other fetcher here, this needs a short
    # session-priming step first (one GET of the site's own home page) to
    # get an anonymous JWT + cookies before the real API calls work.
    tenant, _, career_site_id = slug.partition("/")
    token, cloud_host, cookies = _fetch_cornerstone_session(tenant, career_site_id)

    resp = httpx.post(
        f"https://{cloud_host}/rec-job-search/external/jobs",
        json={
            "careerSiteId": int(career_site_id), "careerSitePageId": int(career_site_id),
            "pageNumber": 1, "pageSize": 100, "cultureId": 1, "searchText": "",
            "cultureName": "en-US", "states": [], "countryCodes": [], "cities": [],
            "placeID": "", "radius": None, "postingsWithinDays": None,
            "customFieldCheckboxKeys": [], "customFieldDropdowns": [], "customFieldRadios": [],
        },
        headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    total = data.get("totalCount") or 0
    requisitions = list(data.get("requisitions") or [])

    if total > len(requisitions):
        for page_number in range(2, (total // 100) + 2):
            r = httpx.post(
                f"https://{cloud_host}/rec-job-search/external/jobs",
                json={
                    "careerSiteId": int(career_site_id), "careerSitePageId": int(career_site_id),
                    "pageNumber": page_number, "pageSize": 100, "cultureId": 1, "searchText": "",
                    "cultureName": "en-US", "states": [], "countryCodes": [], "cities": [],
                    "placeID": "", "radius": None, "postingsWithinDays": None,
                    "customFieldCheckboxKeys": [], "customFieldDropdowns": [], "customFieldRadios": [],
                },
                headers={"User-Agent": USER_AGENT, "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=TIMEOUT,
            )
            r.raise_for_status()
            page_reqs = (r.json().get("data") or {}).get("requisitions") or []
            if not page_reqs:
                break
            requisitions.extend(page_reqs)

    jobs = []
    pending_detail = []  # (index into `jobs`, requisition_id)
    for r in requisitions:
        req_id = r.get("requisitionId")
        title = r.get("displayJobTitle", "")
        locs = r.get("locations") or []
        location = ", ".join(
            ", ".join(p for p in (loc.get("city"), loc.get("state"), loc.get("country")) if p) for loc in locs
        )
        if req_id is None:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://{tenant}.csod.com/ux/ats/careersite/{career_site_id}/home/requisition/{req_id}?c={tenant}",
            "posted_at": None,
            "description": r.get("externalDescription", "") or "",
            "salary": None,
        })
        if filters.title_is_relevant(title) and filters.location_is_allowed(location):
            pending_detail.append((len(jobs) - 1, req_id))

    if pending_detail:
        with ThreadPoolExecutor(max_workers=_CORNERSTONE_DETAIL_WORKERS) as pool:
            future_to_index = {
                pool.submit(_fetch_cornerstone_detail, tenant, token, cookies, req_id): idx
                for idx, req_id in pending_detail
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                if detail["description"]:
                    jobs[idx]["description"] = detail["description"]
                jobs[idx]["posted_at"] = detail["posted_at"]
                jobs[idx]["salary"] = detail["salary"] or _extract_salary_from_text(jobs[idx]["description"])
                if detail["url"]:
                    jobs[idx]["url"] = detail["url"]

    return jobs


def _google_parse_page(html_text: str) -> tuple[list, int, int]:
    """Google's careers page has no separate JSON API call — the full
    result set for that page is rendered server-side straight into the
    HTML as `AF_initDataCallback({key: 'ds:1', ..., data: [...]})`, a JS
    object literal whose `data:` value happens to be valid JSON:
    `[records, null, total_count, page_size]`. Returns
    (records, total_count, page_size) — records still in Google's own
    positional-array shape, unpacked by the caller. ([], 0, 0) if the
    marker isn't found (e.g. a past-the-end page)."""
    idx = html_text.find("key: 'ds:1'")
    if idx == -1:
        return [], 0, 0
    data_idx = html_text.find("data:", idx)
    if data_idx == -1:
        return [], 0, 0
    start = html_text.find("[", data_idx)
    if start == -1:
        return [], 0, 0
    try:
        parsed, _ = json.JSONDecoder().raw_decode(html_text[start:])
    except Exception:
        return [], 0, 0
    if not parsed or not isinstance(parsed[0], list):
        return [], 0, 0
    total = parsed[2] if len(parsed) > 2 and isinstance(parsed[2], int) else 0
    page_size = parsed[3] if len(parsed) > 3 and isinstance(parsed[3], int) else 20
    return parsed[0], total, page_size


def _google_records_to_jobs(records: list, company_display_name: str) -> list[dict]:
    jobs = []
    for r in records:
        try:
            job_id, title = r[0], r[1]
            locations = r[9] or []
            location = ", ".join(loc[0] for loc in locations if loc and loc[0])
            description = (r[10] or [None, ""])[1] or ""
            created = r[12] if len(r) > 12 else None
            posted_at = _epoch_seconds_to_iso(created[0]) if created else None
        except (IndexError, TypeError):
            continue
        if not job_id or not title:
            continue
        slug_title = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://www.google.com/about/careers/applications/jobs/results/{job_id}-{slug_title}",
            "posted_at": posted_at,
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


_GOOGLE_LIST_WORKERS = 8


def fetch_google(company_display_name: str, slug: str) -> list[dict]:
    # Google's careers site (careers.google.com -> google.com/about/careers)
    # has no third-party ATS — job data is server-side-rendered straight
    # into the HTML (see _google_parse_page), reachable with a plain
    # unauthenticated GET, confirmed live 2026-09-27 (no rate-limiting hit
    # across repeated requests). `slug` is the `location` query param
    # Google's own site uses to scope the search server-side (e.g. "United
    # States") — Google's real board has thousands of postings worldwide,
    # so this keeps the fetch to a relevant subset, the same way
    # aggregators.yaml's `where` scopes Adzuna.
    #
    # Each job record is a POSITIONAL array (protobuf-JSON-ish, not a named
    # object) — field indices confirmed live against a real posting:
    # 0=id, 1=title, 9=locations, 10=[_, description_html],
    # 12=[posted_unix_seconds, _]. This is Google's own internal render
    # payload, not a documented contract — could shift without notice, same
    # caveat as Workday's CXS/Gem's GraphQL API elsewhere in this module.
    #
    # The response gives the true total on page 1, so remaining pages are
    # fetched concurrently instead of walked one at a time — same idea as
    # _fetch_all_workday_postings, needed here too since "United States" is
    # thousands of postings across ~200+ pages.
    def _fetch_page_records(page_num: int) -> list:
        resp = httpx.get(
            "https://www.google.com/about/careers/applications/jobs/results/",
            params={"location": slug, "page": page_num},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        records, _, _ = _google_parse_page(resp.text)
        return records

    first_resp = httpx.get(
        "https://www.google.com/about/careers/applications/jobs/results/",
        params={"location": slug, "page": 1},
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    first_resp.raise_for_status()
    first_records, total, page_size = _google_parse_page(first_resp.text)
    if not first_records:
        return []
    all_records = list(first_records)

    if total > len(all_records) and page_size:
        total_pages = -(-total // page_size)  # ceil
        with ThreadPoolExecutor(max_workers=_GOOGLE_LIST_WORKERS) as pool:
            for records in pool.map(_fetch_page_records, range(2, total_pages + 1)):
                all_records.extend(records)

    return _google_records_to_jobs(all_records, company_display_name)


def _apple_parse_hydration(html_text: str) -> dict | None:
    """Apple's careers site is a custom React app whose own client API is
    CSRF/session-gated, BUT the initial page is server-rendered with the
    full result set embedded as a JSON string inside
    `window.__staticRouterHydrationData = JSON.parse("...")` — reachable
    with a plain unauthenticated GET."""
    idx = html_text.find("window.__staticRouterHydrationData")
    if idx == -1:
        return None
    start = html_text.find("JSON.parse(", idx)
    if start == -1:
        return None
    start += len("JSON.parse(")
    try:
        raw_string, _ = json.JSONDecoder().raw_decode(html_text[start:])
        return json.loads(raw_string)
    except Exception:
        return None


def _find_by_keys(node, *required_keys):
    """Recursively finds the first dict in `node` containing every one of
    `required_keys` — used instead of a hardcoded route-id path since React
    Router's internal route keys aren't a stable public contract."""
    if isinstance(node, dict):
        if all(k in node for k in required_keys):
            return node
        for v in node.values():
            found = _find_by_keys(v, *required_keys)
            if found is not None:
                return found
    elif isinstance(node, list):
        for v in node:
            found = _find_by_keys(v, *required_keys)
            if found is not None:
                return found
    return None


def _apple_results_to_jobs(results: list, company_display_name: str) -> list[dict]:
    jobs = []
    for r in results:
        title = r.get("postingTitle", "")
        locations = r.get("locations") or []
        # `name` alone is just the city ("Cupertino", "Austin") with no
        # state/country — confirmed live 2026-09-27 that EVERY Apple
        # posting's location string was failing filters.location_is_allowed
        # as a result, since that only ever matches on a country/province
        # marker ("united states", "canada", ", on", etc.), never a bare
        # city name alone. `countryName` ("United States of America") is a
        # separate field on the same location object — appending it is
        # what makes a real US posting actually pass the filter.
        location = ", ".join(
            ", ".join(p for p in (loc.get("name"), loc.get("countryName")) if p)
            for loc in locations if loc.get("name")
        )
        position_id = r.get("positionId", "")
        path_title = r.get("transformedPostingTitle", "")
        if not position_id or not title:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://jobs.apple.com/en-us/details/{position_id}/{path_title}",
            "posted_at": r.get("postDateInGMT"),
            "description": r.get("jobSummary", "") or "",
            "salary": None,
        })
    return jobs


_APPLE_LIST_WORKERS = 8
_APPLE_PAGE_SIZE = 20  # observed page size, confirmed live 2026-09-27


def fetch_apple(company_display_name: str, slug: str) -> list[dict]:
    # Apple's careers site (jobs.apple.com) has no third-party ATS — see
    # _apple_parse_hydration for how job data is reached without auth.
    # `slug` is "{locale}/{location}" — Apple partitions results by LOCALE
    # PATH, not just the `location` query param: /en-us/search only ever
    # returns US results (even a Canada-shaped location value on that path
    # returns 0) and /en-ca/search only ever returns Canada results, each
    # with its own location-slug format (confirmed live 2026-09-27:
    # "en-us/united-states-USA" vs "en-ca/canada-CANC" — note "CANC" not
    # "CAN"). This was missed initially (hardcoded to /en-us/search), which
    # meant a Canada entry using a Canada-shaped location on that path
    # would have silently returned nothing rather than erroring.
    locale, _, location = slug.partition("/")
    # Only the short listing-page `jobSummary` is used as description here
    # (no per-job detail fetch) to keep this fetcher's request volume
    # bounded — Apple's board is large and a full per-job fetch would be a
    # lot of extra requests for a company with no salary data available
    # either way; title/location filtering is unaffected, only the
    # JD-based stack-dealbreaker check runs on a shorter text than usual.
    #
    # totalRecords on page 1 drives concurrent fetching of the rest, same
    # idea as fetch_google/fetch_workday — a broad location like "United
    # States" is thousands of postings across ~200+ pages.
    def _fetch_page_search_data(page_num: int) -> dict:
        resp = httpx.get(
            f"https://jobs.apple.com/{locale}/search",
            params={"location": location, "page": page_num},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = _apple_parse_hydration(resp.text)
        return _find_by_keys(data, "searchResults", "totalRecords") if data else {}

    first_search_data = _fetch_page_search_data(1) or {}
    first_results = first_search_data.get("searchResults") or []
    if not first_results:
        return []
    all_results = list(first_results)
    total = first_search_data.get("totalRecords") or 0

    if total > len(all_results):
        total_pages = -(-total // _APPLE_PAGE_SIZE)  # ceil
        with ThreadPoolExecutor(max_workers=_APPLE_LIST_WORKERS) as pool:
            for search_data in pool.map(_fetch_page_search_data, range(2, total_pages + 1)):
                all_results.extend((search_data or {}).get("searchResults") or [])

    return _apple_results_to_jobs(all_results, company_display_name)


def _shopify_resolve(data: list, idx: int, depth: int = 0, max_depth: int = 8):
    """React Router 7 "single fetch" `.data` responses are a flat JSON
    array where most entries are either literal values or REFERENCE dicts
    of the shape {"_<name_index>": <value_index_or_literal>}: the key's
    numeric suffix is itself an index into `data` holding the property's
    NAME (a string), and the value is either another index to recursively
    resolve, or a literal (bool/string/null) used as-is. A negative int
    value is React Router's own "undefined-ish" sentinel, not an index —
    treated as None. Empirically reverse-engineered against a real
    response (2026-09-27) and verified to reproduce known field values
    (e.g. "url" -> "https://www.shopify.com/careers") before use here."""
    if depth > max_depth or not (0 <= idx < len(data)):
        return None
    val = data[idx]
    if isinstance(val, dict) and val and all(k.startswith("_") and k[1:].isdigit() for k in val):
        out = {}
        for k, v in val.items():
            name = data[int(k[1:])]
            if isinstance(v, int):
                out[name] = _shopify_resolve(data, v, depth + 1, max_depth) if v >= 0 else None
            else:
                out[name] = v
        return out
    if isinstance(val, list):
        return [
            (_shopify_resolve(data, v, depth + 1, max_depth) if v >= 0 else None) if isinstance(v, int) else v
            for v in val
        ]
    return val


def _shopify_find_route_index(data: list, name_needle: str) -> int | None:
    for i, v in enumerate(data):
        if isinstance(v, dict) and any(
            k.startswith("_") and k[1:].isdigit() and data[int(k[1:])] == name_needle for k in v
        ):
            return i
    return None


def _shopify_slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def fetch_shopify(company_display_name: str, slug: str) -> list[dict]:
    # Shopify's careers site (shopify.com/careers) has no third-party ATS
    # exposed publicly — it's a custom React Router 7 (Remix) SSR app, whose
    # route-loader JSON is reachable at `{page-url}.data` with no auth (the
    # `.data` suffix is React Router's own "single fetch" convention).
    # Confirmed live 2026-09-27: all 114 open jobs come back in ONE request,
    # no pagination needed. `slug` is unused (single-company fetcher).
    # See _shopify_resolve for the response format.
    resp = httpx.get(
        "https://www.shopify.com/careers.data",
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    route_idx = _shopify_find_route_index(data, "jobPostingsWithJobs")
    if route_idx is None:
        return []
    route_data = _shopify_resolve(data, route_idx, max_depth=6)
    entries = (route_data or {}).get("jobPostingsWithJobs") or []

    jobs = []
    for entry in entries:
        jp = (entry or {}).get("jobPosting") or {}
        job_id = jp.get("id")
        title = jp.get("title", "")
        if not job_id or not title:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": jp.get("locationName", "") or "",
            "url": f"https://www.shopify.com/careers/{_shopify_slugify(title)}_{job_id}",
            "posted_at": jp.get("publishedDate"),
            "description": "",
            "salary": None,
        })

    pending_detail = [
        (i, j["url"]) for i, j in enumerate(jobs)
        if filters.title_is_relevant(j["title"]) and filters.location_is_allowed(j["location"])
    ]

    def _fetch_detail(url: str) -> dict:
        try:
            r = httpx.get(f"{url}.data", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
            r.raise_for_status()
            d = r.json()
            ridx = _shopify_find_route_index(d, "jobPosting")
            resolved = _shopify_resolve(d, ridx, max_depth=8) if ridx is not None else {}
            jp = (resolved or {}).get("jobPosting") or {}
            description = jp.get("descriptionHtml", "") or ""
            return {"description": description, "salary": _extract_salary_from_text(description)}
        except Exception:
            return {"description": "", "salary": None}

    if pending_detail:
        with ThreadPoolExecutor(max_workers=8) as pool:
            future_to_index = {pool.submit(_fetch_detail, url): idx for idx, url in pending_detail}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["description"] = detail["description"]
                jobs[idx]["salary"] = detail["salary"]

    return jobs


def _meta_session() -> tuple[str, dict]:
    """Meta's GraphQL endpoint validates an `lsd` token against an anonymous
    session tied to a `datr` cookie — both come from just loading the
    public job-search page once, no login needed. Meta's edge only sets the
    `datr` cookie on a request that carries the `Sec-Fetch-*` headers a
    real top-level browser navigation sends (confirmed live 2026-09-27:
    identical request minus these headers gets a 200 with no cookie at
    all) — these describe how the fetch is being made, not a different
    identity, so this module's own USER_AGENT is unchanged."""
    resp = httpx.get(
        "https://www.metacareers.com/jobsearch/",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Site": "none",
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    m = re.search(r'\["LSD",\[\],\{"token":"([^"]+)"', resp.text)
    if not m:
        raise RuntimeError("meta: could not find LSD token on jobsearch page")
    return m.group(1), dict(resp.cookies)


_META_SEARCH_DOC_ID = "27506805582236862"  # CareersJobSearchResultsDataQuery, persisted GraphQL query


def fetch_meta(company_display_name: str, slug: str) -> list[dict]:
    # Meta's careers site (metacareers.com) has no third-party ATS — it
    # calls its own internal GraphQL endpoint, reverse-engineered from the
    # page's own JS the same way fetch_gem's endpoint was, confirmed live
    # 2026-09-27 (1017 open postings returned in ONE request — no
    # pagination observed/needed). Unlike every other fetcher here, this
    # needs a short session-priming step first (one GET of the public job
    # search page) to get a `datr` cookie + `lsd` token before the GraphQL
    # call is accepted — still fully anonymous, no login. `slug` is unused
    # (single-company fetcher).
    lsd, cookies = _meta_session()
    variables = {
        "isLoggedIn": False,
        "search_input": {
            "q": None, "divisions": [], "offices": [], "roles": [], "leadership_levels": [],
            "saved_jobs": [], "saved_searches": [], "sub_teams": [], "teams": [],
            "is_leadership": False, "is_remote_only": False, "sort_by_new": False,
            "page": 1, "results_per_page": None,
        },
        "viewasUserID": None,
    }
    resp = httpx.post(
        "https://www.metacareers.com/api/graphql/",
        data={
            "doc_id": _META_SEARCH_DOC_ID,
            "variables": json.dumps(variables),
            "fb_dtsg": "",
            "lsd": lsd,
        },
        headers={
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.metacareers.com/jobsearch/",
            "Origin": "https://www.metacareers.com",
            "Accept": "*/*",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Site": "same-origin",
        },
        cookies=cookies,
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    result = resp.json().get("data") or {}
    all_jobs = (result.get("job_search_with_featured_jobs") or {}).get("all_jobs") or []

    jobs = []
    for p in all_jobs:
        job_id = p.get("id")
        title = p.get("title", "")
        locations = p.get("locations") or []
        location = ", ".join(locations)
        if not job_id or not title:
            continue
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://www.metacareers.com/jobs/{job_id}/",
            "posted_at": None,
            "description": "",
            "salary": None,
        })

    def _fetch_detail(job_id) -> dict:
        try:
            r = httpx.get(f"https://www.metacareers.com/jobs/{job_id}/", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True)
            r.raise_for_status()
            m = re.search(r'"description"\s*:\s*"((?:[^"\\]|\\.)*)"', r.text)
            description = json.loads(f'"{m.group(1)}"') if m else ""
            comp_m = re.search(
                r'"compensation_amount_minimum"\s*:\s*"([^"]+)"\s*,\s*"compensation_amount_maximum"\s*:\s*"([^"]+)"',
                r.text,
            )
            salary = f"{comp_m.group(1)} – {comp_m.group(2)}" if comp_m else _extract_salary_from_text(description)
            return {"description": description, "salary": salary}
        except Exception:
            return {"description": "", "salary": None}

    pending_detail = [
        (i, j["url"].rstrip("/").rsplit("/", 1)[-1]) for i, j in enumerate(jobs)
        if filters.title_is_relevant(j["title"]) and filters.location_is_allowed(j["location"])
    ]
    if pending_detail:
        with ThreadPoolExecutor(max_workers=8) as pool:
            future_to_index = {pool.submit(_fetch_detail, job_id): idx for idx, job_id in pending_detail}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["description"] = detail["description"]
                jobs[idx]["salary"] = detail["salary"]

    return jobs


_AVATURE_RESULT_RE = re.compile(
    r'<article class="article article--result"[^>]*>.*?<a class="link" href="([^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.DOTALL,
)
_AVATURE_FIELD_RE = re.compile(
    r'<div class="article__content__view__field[^"]*">\s*'
    r'<div class="article__content__view__field__label">\s*([^<]+?)\s*</div>\s*'
    r'<div class="article__content__view__field__value">\s*([^<]*?)\s*</div>',
    re.DOTALL,
)


def _fetch_avature_detail(tenant: str, path: str) -> dict:
    """Avature has no JSON API at all — the job-detail page is plain
    server-rendered HTML with a consistent label/value div structure per
    field, but the LABELS a given tenant uses for location are not
    standardized: Synopsys splits it into separate "City"/"Country"
    fields, Bloomberg uses one combined "Location" field instead
    (confirmed live on both, real postings) — checking only City/Country
    silently produced an empty location, and therefore no fetched job at
    all, for every Bloomberg posting. Falls back through several known
    label spellings rather than assuming one tenant's shape is universal.
    Best-effort: any parse failure means an empty description, same as
    every other detail fetch here."""
    try:
        resp = httpx.get(f"https://{tenant}.avature.net{path}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        fields = {label.strip(): value.strip() for label, value in _AVATURE_FIELD_RE.findall(resp.text)}
        city, country = fields.get("City", ""), fields.get("Country", "")
        location = ", ".join(p for p in (city, country) if p)
        if not location:
            for key in ("Location", "Locations", "City/Country", "Primary Location"):
                if fields.get(key):
                    location = fields[key]
                    break
        # Starts from the first field block rather than the "Job
        # Description" label specifically: some custom fields (e.g. a
        # "Base Salary Range: $X - $Y" block, confirmed live on a real
        # Synopsys posting) have no label at all and render BEFORE the
        # description in the page — starting here instead of at the
        # description label is what lets _extract_salary_from_text below
        # actually see it.
        desc_idx = resp.text.find('class="article__content__view__field')
        description = filters.strip_html(resp.text[desc_idx:desc_idx + 20000]) if desc_idx != -1 else ""
        return {
            "location": location,
            "posted_at": fields.get("Date Posted"),
            "description": description,
            "salary": _extract_salary_from_text(description),
        }
    except Exception:
        return {"location": "", "posted_at": None, "description": "", "salary": None}


_AVATURE_DETAIL_WORKERS = 6


def fetch_avature(company_display_name: str, slug: str) -> list[dict]:
    # Avature career portals have no public JSON/XML API of any kind — pure
    # server-rendered HTML, confirmed live 2026-09-27 against Synopsys (528
    # open postings). `slug` is just the tenant subdomain (e.g. "synopsys"
    # for synopsys.avature.net). List pages have title/URL/job-ID/posted-
    # date but no location — that only lives on the per-job detail page, so
    # (unlike most fetchers here) every candidate needs a detail fetch to
    # even know its location, not just its description.
    # jobRecordsPerPage is accepted but silently ignored — confirmed live
    # 2026-09-27 that requesting 100 still only returns 6 records per call,
    # so this has to be treated as a genuinely fixed page size and paged
    # (concurrently) by real 6-record steps, not a tunable one.
    page_size = 6
    seen_urls = set()

    def _fetch_list_page(offset: int) -> list[dict]:
        resp = httpx.get(
            f"https://{slug}.avature.net/careers/SearchJobs/",
            params={"jobRecordsPerPage": page_size, "jobOffset": offset},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text

    first_html = _fetch_list_page(0)
    total_match = re.search(r"([\d,]+)\s+results?", first_html, re.IGNORECASE)
    total = int(total_match.group(1).replace(",", "")) if total_match else 0

    jobs = []

    def _extract(html_text: str):
        # The regex's href capture is already a full absolute URL (Avature
        # renders it that way, not a relative path) — use it as-is; do NOT
        # prepend the domain again (that silently produced a malformed
        # double-prefixed URL here during development, which the detail
        # fetch's try/except swallowed into an empty result for every job).
        for url, title in _AVATURE_RESULT_RE.findall(html_text):
            if url in seen_urls:
                continue
            seen_urls.add(url)
            jobs.append({
                "company": company_display_name,
                "title": title.strip(),
                "location": "",
                "url": url,
                "posted_at": None,
                "description": "",
                "salary": None,
            })

    _extract(first_html)

    if total > len(jobs):
        offsets = range(page_size, total, page_size)
        with ThreadPoolExecutor(max_workers=_AVATURE_DETAIL_WORKERS) as pool:
            for html_text in pool.map(_fetch_list_page, offsets):
                _extract(html_text)

    pending_detail = [
        (i, j["url"].removeprefix(f"https://{slug}.avature.net"))
        for i, j in enumerate(jobs) if filters.title_is_relevant(j["title"])
    ]
    if pending_detail:
        with ThreadPoolExecutor(max_workers=_AVATURE_DETAIL_WORKERS) as pool:
            future_to_index = {pool.submit(_fetch_avature_detail, slug, path): idx for idx, path in pending_detail}
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["location"] = detail["location"]
                jobs[idx]["posted_at"] = detail["posted_at"]
                jobs[idx]["description"] = detail["description"]
                jobs[idx]["salary"] = detail["salary"]

    # Location only resolves via the (gated) detail fetch above, so a job
    # whose title didn't look relevant never gets a location at all and
    # would incorrectly fail location_is_allowed downstream — drop those
    # ungated rows here instead of shipping them with a blank location.
    return [j for j in jobs if j["location"]]


_HRDEPARTMENT_ROW_RE = re.compile(
    r'<a href="(/hr/ats/Posting/view/\d+)">\s*<span>([^<]+)</span>\s*</a></td>\s*'
    r'<td>(\d+)</td>\s*<td>\s*([^<]*?)\s*</br>\s*</td>\s*<td>([^<]*)</td>',
    re.DOTALL,
)


def _fetch_hrdepartment_detail(base_url: str, path: str) -> dict:
    try:
        resp = httpx.get(f"{base_url}{path}", headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        m = re.search(
            r'id="job_details_ats_requisition_description"[^>]*>(.*?)</div>\s*</div>', resp.text, re.DOTALL
        )
        description = filters.strip_html(m.group(1)) if m else ""
        return {"description": description, "salary": _extract_salary_from_text(description)}
    except Exception:
        return {"description": "", "salary": None}


_HRDEPARTMENT_DETAIL_WORKERS = 6


def fetch_hrdepartment(company_display_name: str, slug: str) -> list[dict]:
    # HRDepartment ATS career portals have no public JSON/XML API — pure
    # server-rendered HTML, confirmed live 2026-09-27 against Sanmina-SCI
    # (984 open postings). `slug` is the full subdomain prefix up to
    # ".hrdepartment.com" (e.g. "sanminacareers.mua" for
    # sanminacareers.mua.hrdepartment.com). No location filter is possible
    # up front — location is on the list page (unlike Avature) but there's
    # no separate salary/date-posted field found anywhere on this platform.
    base_url = f"https://{slug}.hrdepartment.com"
    page_size = 100
    seen_paths = set()
    jobs = []

    def _fetch_list_page(page_num: int) -> str:
        resp = httpx.get(
            f"{base_url}/hr/ats/JobSearch/viewAll/jobSearchPaginationExternal_pageSize:{page_size}"
            f"/jobSearchPaginationExternal_page:{page_num}",
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return resp.text

    def _extract(html_text: str):
        for path, title, _req_num, _category, location in _HRDEPARTMENT_ROW_RE.findall(html_text):
            if path in seen_paths:
                continue
            seen_paths.add(path)
            jobs.append({
                "company": company_display_name,
                "title": title.strip(),
                "location": location.strip(),
                "url": f"{base_url}{path}",
                "posted_at": None,
                "description": "",
                "salary": None,
            })

    first_html = _fetch_list_page(1)
    _extract(first_html)
    # "Displaying 1 - 100 of 984" gives the true total — driving pagination
    # off this (like every other multi-page fetcher here) rather than
    # walking until a page comes back empty, because this platform doesn't
    # do that: querying a page number past the real end just re-serves the
    # LAST real page's content again instead of an empty result, which
    # would otherwise loop until an arbitrary sanity cap (confirmed live
    # 2026-09-27 — a real, easy-to-hit bug, not a hypothetical one).
    total_match = re.search(r"of\s+([\d,]+)", first_html)
    total = int(total_match.group(1).replace(",", "")) if total_match else len(jobs)

    if total > len(jobs):
        total_pages = -(-total // page_size)  # ceil
        with ThreadPoolExecutor(max_workers=_HRDEPARTMENT_DETAIL_WORKERS) as pool:
            for html_text in pool.map(_fetch_list_page, range(2, total_pages + 1)):
                _extract(html_text)

    pending_detail = [
        (i, j["url"].removeprefix(base_url)) for i, j in enumerate(jobs)
        if filters.title_is_relevant(j["title"]) and filters.location_is_allowed(j["location"])
    ]
    if pending_detail:
        with ThreadPoolExecutor(max_workers=_HRDEPARTMENT_DETAIL_WORKERS) as pool:
            future_to_index = {
                pool.submit(_fetch_hrdepartment_detail, base_url, path): idx for idx, path in pending_detail
            }
            for future in as_completed(future_to_index):
                idx = future_to_index[future]
                detail = future.result()
                jobs[idx]["description"] = detail["description"]
                jobs[idx]["salary"] = detail["salary"]

    return jobs


_GR8PEOPLE_SEARCH_QUERY = """
query searchJobs($start: Int, $first: Int) {
  searchJobs: searchGoogleJobDiscovery(start: $start, first: $first) {
    results {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        key
        title
        postedOn
        primaryPlace { name }
        descriptionHTML
      }
    }
  }
}
"""


def fetch_gr8people(company_display_name: str, slug: str) -> list[dict]:
    # gr8people career sites expose a real, public, unauthenticated GraphQL
    # API at {tenant}.gr8people.com/graphql, confirmed live 2026-09-27
    # against gr8people's own demo tenant (careers.gr8people.com) — every
    # customer runs the same underlying app, so this same query shape
    # should carry over to a newly-added tenant. `slug` is the tenant
    # subdomain (e.g. "ea" for ea.gr8people.com). NOTE: this specific
    # platform's dev-time verification could not be completed against a
    # real customer tenant from this codebase's own network (EA's
    # ea.gr8people.com 403-blocked every request here, from a plain HTML
    # fetch to the GraphQL endpoint itself — looks like a tenant-level
    # WAF/CDN rule, not anything wrong with the query) — verify a newly-
    # added gr8people company with `python -m app.main --company <slug>`
    # before trusting it, same as any other undocumented-API fetcher here.
    jobs = []
    start = 0
    page_size = 50
    while True:
        resp = httpx.post(
            f"https://{slug}.gr8people.com/graphql",
            json={
                "operationName": "searchJobs",
                "variables": {"start": start, "first": page_size},
                "query": _GR8PEOPLE_SEARCH_QUERY,
            },
            headers={"User-Agent": USER_AGENT, "Content-Type": "application/json", "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        results = ((resp.json().get("data") or {}).get("searchJobs") or {}).get("results") or {}
        nodes = results.get("nodes") or []
        if not nodes:
            break
        for n in nodes:
            key = n.get("key")
            title = n.get("title", "")
            place = n.get("primaryPlace") or {}
            location = place.get("name", "") or ""
            description = n.get("descriptionHTML", "") or ""
            if not key or not title:
                continue
            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": location,
                "url": f"https://{slug}.gr8people.com/jobs/{key}",
                "posted_at": n.get("postedOn"),
                "description": description,
                "salary": _extract_salary_from_text(description),
            })
        if not (results.get("pageInfo") or {}).get("hasNextPage"):
            break
        start += page_size
        if start > 20000:  # sanity guard
            break
    return jobs


_AMAZON_PAGE_SIZE = 100
_AMAZON_LIST_WORKERS = 8


def fetch_amazon(company_display_name: str, slug: str) -> list[dict]:
    # Amazon's careers site (amazon.jobs) has no third-party ATS, but its
    # own frontend calls a real, public, unauthenticated JSON search API —
    # confirmed live 2026-09-28 (this was missed on an earlier pass that
    # concluded "no supported ATS found"; the actual UI request uses plain
    # `country=CAN`, not the `country[]=CAN` array-style param a naive
    # guess would produce, which silently ignores the filter and returns
    # unrelated worldwide results instead of erroring — a real gotcha
    # caught by watching the real page's own network request rather than
    # guessing the query shape). `slug` is "{country_code}/{base_query}"
    # — country_code is Amazon's own 3-letter code ("USA", "CAN", not
    # ISO's "US"/"CA"), base_query is the keyword search Amazon's own site
    # uses (e.g. "data engineer") — see companies.yaml's header comment.
    # Full HTML description comes back in the SAME response as the
    # listing, no per-job detail fetch needed. No structured salary field,
    # only the free-text fallback applies, same as Ashby/Workday.
    country, _, base_query = slug.partition("/")

    def _fetch_page(offset: int) -> tuple[list, int]:
        resp = httpx.get(
            "https://www.amazon.jobs/en/search.json",
            params={
                "offset": offset, "result_limit": _AMAZON_PAGE_SIZE, "sort": "relevant",
                "base_query": base_query, "country": country,
            },
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("jobs") or [], data.get("hits") or 0

    first_jobs, total = _fetch_page(0)
    if not first_jobs:
        return []
    all_raw = list(first_jobs)

    if total > len(all_raw):
        offsets = range(_AMAZON_PAGE_SIZE, total, _AMAZON_PAGE_SIZE)
        with ThreadPoolExecutor(max_workers=_AMAZON_LIST_WORKERS) as pool:
            for page_jobs, _ in pool.map(_fetch_page, offsets):
                all_raw.extend(page_jobs)

    jobs = []
    seen_ids = set()
    for j in all_raw:
        job_id = j.get("id_icims") or j.get("id")
        title = j.get("title", "")
        job_path = j.get("job_path", "")
        if not job_id or not title or not job_path or job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        location = j.get("location", "") or ", ".join(
            p for p in (j.get("city"), j.get("state"), j.get("country_code")) if p
        )
        description = "\n".join(
            p for p in (j.get("description"), j.get("basic_qualifications"), j.get("preferred_qualifications")) if p
        )
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://www.amazon.jobs{job_path}",
            "posted_at": j.get("posted_date"),  # human string ("August 7, 2026"), not ISO
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


_IBM_PAGE_SIZE = 100
_IBM_LIST_WORKERS = 8
_IBM_SOURCE_FIELDS = ["title", "url", "description", "field_keyword_05", "field_keyword_19", "field_keyword_17"]


def _ibm_search(base_query: str, country: str, offset: int) -> tuple[list, int]:
    resp = httpx.post(
        "https://www-api.ibm.com/search/api/v2",
        json={
            "appId": "careers",
            "scopes": ["careers2"],
            "query": {"bool": {"must": [{"multi_match": {"query": base_query, "fields": ["title", "description"]}}]}},
            "post_filter": {"term": {"field_keyword_05": country}},
            "size": _IBM_PAGE_SIZE,
            "from": offset,
            "_source": _IBM_SOURCE_FIELDS,
            "lang": "zz",
        },
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    hits = data.get("hits") or {}
    return hits.get("hits") or [], (hits.get("total") or {}).get("value") or 0


def fetch_ibm(company_display_name: str, slug: str) -> list[dict]:
    # IBM's real careers site (careers.ibm.com) is hard AWS-WAF-blocked
    # (HTTP 202 "challenge" response, every path, confirmed live
    # 2026-09-28 — this includes the per-job JobDetail page, so no full
    # description is reachable, only the short search snippet). But
    # careers.ibm.com is a FRONTEND for a separate, NOT blocked search
    # backend — https://www-api.ibm.com/search/api/v2 — a generic
    # Elasticsearch-query-DSL proxy IBM's own www.ibm.com/careers/search
    # page calls, reverse-engineered by watching that real page's own
    # network request (also found this way: `field_keyword_05` = country,
    # `field_keyword_19` = city, `field_keyword_17` = work arrangement
    # (Hybrid/Remote/onsite) — these numbered field names aren't
    # documented anywhere, they're internal IBM search-index field IDs).
    # `slug` is "{country}/{base_query}" — country is IBM's own facet
    # value (e.g. "United States", "Canada" — plain English, not a code),
    # base_query is a free-text keyword search matched against title AND
    # description server-side (real relevance-ranked results, not just a
    # category filter). No structured salary field, only the free-text
    # fallback applies, same as Ashby/Workday.
    country, _, base_query = slug.partition("/")

    first_hits, total = _ibm_search(base_query, country, 0)
    if not first_hits:
        return []
    all_hits = list(first_hits)

    if total > len(all_hits):
        offsets = range(_IBM_PAGE_SIZE, total, _IBM_PAGE_SIZE)
        with ThreadPoolExecutor(max_workers=_IBM_LIST_WORKERS) as pool:
            for page_hits, _ in pool.map(lambda off: _ibm_search(base_query, country, off), offsets):
                all_hits.extend(page_hits)

    jobs = []
    seen_urls = set()
    for h in all_hits:
        src = h.get("_source") or {}
        title = src.get("title", "")
        url = src.get("url", "")
        if not title or not url or url in seen_urls:
            continue
        seen_urls.add(url)
        # field_keyword_19 is already "City, CC" (e.g. "Austin, US") — drop
        # the 2-letter code before appending the full country name so the
        # result isn't "Austin, US, United States".
        city = (src.get("field_keyword_19", "") or "").rsplit(",", 1)[0].strip()
        location = ", ".join(p for p in (city, country) if p and p != "Multiple Cities")
        description = src.get("description", "") or ""
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location or country,
            "url": url,
            "posted_at": None,  # not present in this search index
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


def fetch_uber(company_display_name: str, slug: str) -> list[dict]:
    # Uber's real careers site (jobs.uber.com) has no third-party ATS and
    # calls its own JSON API (jobs.uber.com/api/jobs/search/) — but that
    # endpoint is Cloudflare-bot-walled to a plain request (confirmed live
    # 2026-09-28: bare httpx gets a 403 "Just a moment..." challenge page)
    # even though it's completely public through a real browser, no
    # login/session needed — same shape as Indeed's block in
    # aggregator_clients.py, so this uses the same real-headless-browser
    # workaround. IMPORTANT: this superseded an earlier "oraclecloud"
    # Uber entry — iaziqy.fa.ocs.oraclecloud.com/UberCareers is a REAL,
    # live Oracle board (verified live 2026-09-27) but has ZERO Data
    # Engineer-shaped titles despite 538 postings (confirmed by manual
    # inspection: mostly ops/sales/mechanical-engineering roles) — it's
    # apparently a different or non-primary hiring pipeline, not where
    # Uber's real software engineering roles are posted. `slug` is the
    # free-text keyword search Uber's own site uses (e.g. "data
    # engineer") — no reliable location/country query param was found
    # (a `location=Canada` param is accepted but silently returns 0
    # results, confirmed live), so this returns results across all
    # countries and relies on filters.location_is_allowed downstream to
    # narrow to US/Canada, same approach used for fetch_ibm/fetch_amazon
    # when no explicit country scoping was available or reliable.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] Uber requires the optional `playwright` dependency "
              "(pip install playwright && playwright install chromium) — skipping.")
        return []

    base_query = slug
    page_size = 100
    pages_bodies = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                captured = {}

                def _on_response(resp):
                    if "/api/jobs/search/" in resp.url:
                        try:
                            captured["body"] = resp.json()
                        except Exception:
                            pass

                page.on("response", _on_response)

                page_num = 1
                while True:
                    captured.clear()
                    url = (
                        f"https://jobs.uber.com/en/jobs/?search={urllib.parse.quote(base_query)}"
                        f"&page={page_num}&pagesize={page_size}"
                    )
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(5000 if page_num == 1 else 3000)
                    body = captured.get("body")
                    if not body or not body.get("jobs"):
                        break
                    pages_bodies.append(body)
                    total_jobs = body.get("totalJobs") or 0
                    if page_num * page_size >= total_jobs:
                        break
                    page_num += 1
                    if page_num > 50:  # sanity guard
                        break
            finally:
                browser.close()
    except Exception:
        return pages_bodies and _uber_bodies_to_jobs(pages_bodies, company_display_name) or []

    return _uber_bodies_to_jobs(pages_bodies, company_display_name)


def _uber_bodies_to_jobs(bodies: list, company_display_name: str) -> list[dict]:
    jobs = []
    seen_ids = set()
    for body in bodies:
        for j in body.get("jobs") or []:
            job_id = j.get("Id")
            title = j.get("Title", "")
            urls = j.get("Urls") or []
            path = next((u.get("Url") for u in urls if u.get("IsDefault")), urls[0].get("Url") if urls else None)
            if not job_id or not title or not path or job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            locs = j.get("Locations") or []
            loc = locs[0] if locs else {}
            location = ", ".join(p for p in (loc.get("City"), loc.get("Region"), loc.get("Country")) if p)
            description = j.get("Description", "") or ""
            salary_info = j.get("Salary") or {}
            salary = (
                _format_money_range(
                    salary_info.get("MinValue"), salary_info.get("MaxValue"),
                    salary_info.get("Currency"), (salary_info.get("Period") or "").lower() or None,
                ) if (salary_info.get("MinValue") or salary_info.get("MaxValue")) else None
            ) or _extract_salary_from_text(salary_info.get("Description") or description)
            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": location,
                "url": f"https://jobs.uber.com{path}",
                "posted_at": j.get("DisplayDate"),
                "description": description,
                "salary": salary,
            })
    return jobs


def fetch_atlassian(company_display_name: str, slug: str) -> list[dict]:
    # Atlassian's careers site has no visible third-party ATS in its own
    # UI (fronted by Beamery, a recruitment CRM, not an ATS) but its own
    # frontend calls a first-party proxy endpoint that returns the WHOLE
    # board in one response, no pagination needed — confirmed live
    # 2026-09-28, ~296 postings, zero auth. `slug` is unused (single
    # fixed global endpoint, not per-tenant) — kept for signature
    # consistency with every other fetcher here. Each posting's
    # `portalJobPost.portalUrl` points at a real iCIMS tenant
    # (globalcareers-atlassian.icims.com / careers-americas.icims.com,
    # selected per portalId) confirming Atlassian runs iCIMS underneath —
    # but this proxy is simpler to call directly than reverse-engineering
    # per-region iCIMS tenants. No structured salary field: `payRanges`
    # is always null and `compensation` is boilerplate text with no
    # actual numbers on every posting sampled — only the free-text
    # fallback applies, same as Ashby/Workday.
    resp = httpx.get(
        "https://www.atlassian.com/endpoint/careers/listings",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    postings = resp.json()
    if not isinstance(postings, list):
        postings = []

    jobs = []
    seen_ids = set()
    for p in postings:
        job_id = p.get("id")
        title = p.get("title", "")
        apply_url = p.get("applyUrl") or (p.get("portalJobPost") or {}).get("portalUrl")
        if not job_id or not title or not apply_url or job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        location = "; ".join(p.get("locations") or [])
        description = filters.strip_html(
            "\n".join(p.get(k, "") or "" for k in ("overview", "responsibilities", "qualifications"))
        )
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": apply_url,
            "posted_at": (p.get("portalJobPost") or {}).get("updatedDate"),
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


_KULA_PAGE_SIZE = 99


def fetch_kula(company_display_name: str, slug: str) -> list[dict]:
    # Kula.ai is a generic hosted-careers-page product — confirmed live
    # 2026-09-28 against Vidyard (careers.kula.ai/vidyard), found via
    # network capture on vidyard.com/careers/. `slug` is the Kula
    # accountName (the vanity path segment, e.g. "vidyard" for
    # careers.kula.ai/vidyard) — this same endpoint pattern is reusable
    # for any other Kula-hosted company, just swap accountName. No
    # separate detail fetch needed: ats_job.job_description is the full
    # HTML description, included in the same list response. No
    # structured salary field anywhere in the payload — only the
    # free-text fallback applies. Apply URL is NOT in the API response;
    # constructed as careers.kula.ai/{accountName}/{id}-{slug(title)},
    # verified against the real site's own rendered anchor hrefs.
    def _fetch_page(page: int) -> tuple[list, int]:
        resp = httpx.get(
            "https://careers.kula.ai/api/internal/ats_job_posts",
            params={"accountName": slug, "page": page, "type": "ats_job_post.index", "items": _KULA_PAGE_SIZE},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        meta = data.get("meta") or {}
        return data.get("data") or [], meta.get("pages") or 1

    first_page, total_pages = _fetch_page(1)
    all_postings = list(first_page)
    if total_pages > 1:
        with ThreadPoolExecutor(max_workers=6) as pool:
            for page_postings, _ in pool.map(_fetch_page, range(2, total_pages + 1)):
                all_postings.extend(page_postings)

    jobs = []
    seen_ids = set()
    for p in all_postings:
        job_id = p.get("id")
        title = p.get("title", "")
        if not job_id or not title or job_id in seen_ids:
            continue
        seen_ids.add(job_id)
        ats_job = p.get("ats_job") or {}
        offices = ats_job.get("offices") or []
        location = "; ".join(o.get("location") or o.get("name") or "" for o in offices if o)
        description = filters.strip_html(ats_job.get("job_description") or "")
        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": location,
            "url": f"https://careers.kula.ai/{slug}/{job_id}-{_shopify_slugify(title)}",
            "posted_at": p.get("launch_at"),
            "description": description,
            "salary": _extract_salary_from_text(description),
        })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "ashby": fetch_ashby,
    "workable": fetch_workable,
    "lever": fetch_lever,
    "smartrecruiters": fetch_smartrecruiters,
    "workday": fetch_workday,
    "bamboohr": fetch_bamboohr,
    "rippling": fetch_rippling,
    "teamtailor": fetch_teamtailor,
    "gem": fetch_gem,
    "oraclecloud": fetch_oraclecloud,
    "successfactors": fetch_successfactors,
    "icims": fetch_icims,
    "eightfold": fetch_eightfold,
    "cornerstone": fetch_cornerstone,
    "avature": fetch_avature,
    "hrdepartment": fetch_hrdepartment,
    "gr8people": fetch_gr8people,
    "google": fetch_google,
    "apple": fetch_apple,
    "shopify": fetch_shopify,
    "meta": fetch_meta,
    "amazon": fetch_amazon,
    "ibm": fetch_ibm,
    "uber": fetch_uber,
    "atlassian": fetch_atlassian,
    "kula": fetch_kula,
}


def fetch_company(company: dict) -> list[dict]:
    fetcher = FETCHERS.get(company["ats"])
    if fetcher is None:
        raise ValueError(f"No fetcher for ATS type: {company['ats']}")
    jobs = fetcher(company["name"], company["slug"])
    for j in jobs:
        j["source"] = company["ats"]
    return jobs