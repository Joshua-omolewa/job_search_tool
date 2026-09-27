"""Thin clients for each ATS's public job-board API.

Every function returns a list of plain dicts with a common shape:
    {"company": str, "title": str, "location": str, "url": str,
     "posted_at": str|None, "description": str}

`description` is HTML or plain text, whatever the ATS gives us, and is
consumed by filters.jd_stack_mismatch() for the JD-based stack dealbreaker
check. It's fetched from the SAME request as the listing wherever the ATS
supports that (Greenhouse ?content=true, Ashby/Lever include it by
default) — no extra per-job request needed, so this doesn't multiply your
call volume. Workable's widget API may or may not include it depending on
account; if absent, `description` is just "" and the stack check is
skipped for that job (title/location filters still apply).

NOTE ON THIS SANDBOX: outbound HTTP to arbitrary domains (e.g.
boards-api.greenhouse.io) is blocked by this cloud environment's network
allowlist — that's a property of THIS dev sandbox, not of the ATS APIs
themselves (verified working via WebFetch during development). Run this
module on a machine/server with normal internet access — your laptop, a
cron box, a small VM — and it will work as-is.

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
"""
import re
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


def fetch_greenhouse(company_display_name: str, slug: str) -> list[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    resp = httpx.get(
        url, params={"content": "true"}, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted_at": j.get("first_published") or j.get("updated_at"),
            "description": j.get("content", ""),  # HTML
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
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("jobUrl") or j.get("applyUrl", ""),
            "posted_at": j.get("publishedAt"),
            "description": j.get("descriptionPlain") or j.get("descriptionHtml") or "",
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
        jobs.append({
            "company": company_display_name,
            "title": j.get("title", ""),
            "location": loc_str,
            "url": j.get("url") or j.get("shortlink", ""),
            "posted_at": j.get("published_on") or j.get("created_at"),
            # Workable's widget API doesn't reliably include a description
            # field across all accounts — treat missing as unknown, not
            # as "no dealbreaker language", filters.py handles empty safely.
            "description": j.get("description", ""),
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
        jobs.append({
            "company": company_display_name,
            "title": j.get("text", ""),
            "location": loc,
            "url": j.get("hostedUrl", ""),
            "posted_at": _epoch_millis_to_iso(j.get("createdAt")),
            "description": j.get("descriptionPlain") or j.get("description", ""),
        })
    return jobs


def _fetch_smartrecruiters_description(slug: str, posting_id: str) -> str:
    """SmartRecruiters' list endpoint (fetch_smartrecruiters below) doesn't
    include the JD text — only the per-posting detail endpoint does, one
    extra request per job. Best-effort: any failure (private board, 404,
    timeout) just means no description, not a crashed run; filters.py
    treats an empty description as "unknown, don't reject on stack alone"."""
    try:
        resp = httpx.get(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{posting_id}",
            headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        sections = (resp.json().get("jobAd") or {}).get("sections") or {}
        parts = [
            (sections.get(key) or {}).get("text", "")
            for key in ("jobDescription", "qualifications", "additionalInformation")
        ]
        return "\n\n".join(p for p in parts if p)
    except Exception:
        return ""


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
            # Only worth the extra per-job request (see
            # _fetch_smartrecruiters_description) for postings that
            # already look like real candidates — same gating idea as
            # aggregator_clients.fetch_adzuna uses for its full-JD fetch,
            # so a company with hundreds of postings doesn't turn into
            # hundreds of extra requests for roles that'd get filtered
            # out on title/location alone anyway.
            if posting_id and filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
                description = _fetch_smartrecruiters_description(slug, posting_id)

            jobs.append({
                "company": company_display_name,
                "title": title,
                "location": loc_str,
                "url": job_url,
                "posted_at": p.get("releasedDate"),
                "description": description,
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
    not a crashed run, same as _fetch_smartrecruiters_description.

    Also resolves the real location(s): a multi-location posting's list-page
    `locationsText` is just "5 Locations", but the detail response has the
    primary `location` plus an `additionalLocations` list with the actual
    place names — needed for filters.location_is_allowed to mean anything."""
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
        return {
            "description": info.get("jobDescription", ""),
            "posted_at": info.get("startDate"),
            "location": ", ".join(locations) or None,
        }
    except Exception:
        return {"description": "", "posted_at": None, "location": None}


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
                if detail["location"]:
                    jobs[idx]["location"] = detail["location"]

    return jobs


def _fetch_bamboohr_detail(base_url: str, job_id: str) -> dict:
    """The list endpoint has no description and no real posted date — both
    live on the per-job detail endpoint, one extra request per job.
    Best-effort: any failure just means no description/date, not a crashed
    run, same as _fetch_smartrecruiters_description."""
    try:
        resp = httpx.get(
            f"{base_url}/{job_id}/detail",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        info = (resp.json().get("result") or {}).get("jobOpening") or {}
        return {"description": info.get("description", ""), "posted_at": info.get("datePosted")}
    except Exception:
        return {"description": "", "posted_at": None}


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
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea as
        # fetch_smartrecruiters/fetch_workday.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_bamboohr_detail(base, job_id)
            description = detail["description"]
            posted_at = detail["posted_at"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": f"{base}/{job_id}",
            "posted_at": posted_at,
            "description": description,
        })
    return jobs


def _fetch_rippling_detail(slug: str, job_uuid: str) -> dict:
    """The list endpoint has no description at all — it lives on the
    per-job detail endpoint, one extra request per job. Best-effort: any
    failure just means no description/date, not a crashed run."""
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
        html = "\n\n".join(v for v in (desc.get("company"), desc.get("role")) if v)
        return {"description": html, "posted_at": info.get("createdOn")}
    except Exception:
        return {"description": "", "posted_at": None}


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
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea used throughout this
        # module.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_rippling_detail(slug, job_uuid)
            description = detail["description"]
            posted_at = detail["posted_at"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": entry["url"],
            "posted_at": posted_at,
            "description": description,
        })
    return jobs


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
        jobs.append({
            "company": company_display_name,
            "title": it.get("title", ""),
            "location": "; ".join(filter(None, loc_parts)),
            "url": it.get("url", ""),
            "posted_at": it.get("date_published"),
            "description": it.get("content_html", ""),
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
    means no description/date, not a crashed run."""
    try:
        data = _gem_graphql("ExternalJobPosting", _GEM_DETAIL_QUERY, {"boardId": slug, "extId": ext_id})
        info = data.get("oatsExternalJobPosting") or {}
        return {
            "description": info.get("descriptionHtml", ""),
            "posted_at": _epoch_seconds_to_iso(info.get("firstPublishedTsSec")),
        }
    except Exception:
        return {"description": "", "posted_at": None}


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
        # Only worth the extra per-job request for postings that already
        # look like real candidates — same gating idea used throughout this
        # module.
        if filters.title_is_relevant(title) and filters.location_is_allowed(loc_str):
            detail = _fetch_gem_detail(slug, ext_id)
            description = detail["description"]
            posted_at = detail["posted_at"]

        jobs.append({
            "company": company_display_name,
            "title": title,
            "location": loc_str,
            "url": f"https://jobs.gem.com/{slug}/{ext_id}",
            "posted_at": posted_at,
            "description": description,
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
}


def fetch_company(company: dict) -> list[dict]:
    fetcher = FETCHERS.get(company["ats"])
    if fetcher is None:
        raise ValueError(f"No fetcher for ATS type: {company['ats']}")
    jobs = fetcher(company["name"], company["slug"])
    for j in jobs:
        j["source"] = company["ats"]
    return jobs