"""Clients for broad job aggregators (keyword+location search across many
employers), as opposed to ats_clients.py which is one board per company.

Same output shape as ats_clients.py:
    {"company": str, "title": str, "location": str, "url": str,
     "posted_at": str|None, "salary": str|None}

Same sandbox caveat as ats_clients.py: outbound calls to these domains were
not testable live from this cloud dev environment (network allowlist), only
via WebFetch during research. Run for real on a machine with normal internet.
"""
import html
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET

import httpx

from app import filters
from app import playwright_lock

USER_AGENT = "job-search-pipeline/0.1 (personal use)"
TIMEOUT = 20.0
FULL_JD_TIMEOUT = 10.0  # shorter: this is a best-effort extra request per job, don't let one slow host stall the run

# Companies discovered mid-run: fetch_full_description resolved an Adzuna
# redirect to a Greenhouse/Lever slug we don't already track in
# companies.yaml. Module-level rather than threaded through every return
# value because fetch_adzuna's return shape (list[dict] of jobs) is a
# shared contract with fetch_remotive/FETCHERS/fetch_aggregator — see
# discover_companies.py for what reads this after a run. Each process run
# starts with an empty list, so there's no cross-run leakage to worry about.
DISCOVERED_COMPANIES: list[dict] = []


def _format_salary_range(min_val, max_val, currency=None, period=None, note=None) -> str | None:
    """Shared "X–Y CUR/period" formatter for every aggregator here that
    gives raw min/max numbers (Adzuna, RemoteOK, Jobicy). None if there's
    no usable number at all — including 0, which RemoteOK uses as "no
    salary given" rather than omitting the field (confirmed live
    2026-09-27: 83 of 99 real postings had salary_min == salary_max == 0;
    treating that as a real $0 salary was a real bug caught testing this
    against live data, not a hypothetical)."""
    def _num(v):
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return f"{n:,.0f}" if n else None

    lo, hi = _num(min_val), _num(max_val)
    if not lo and not hi:
        return None
    amount = f"{lo}–{hi}" if lo and hi and lo != hi else (lo or hi)
    text = " ".join(p for p in (amount, currency) if p)
    if period:
        text = f"{text}/{period}"
    return f"{text} ({note})" if note else text


def _format_adzuna_salary(salary_min, salary_max, is_predicted) -> str | None:
    # Adzuna gives raw numbers, no currency field — implied by the country
    # in the endpoint path, which fetch_adzuna hardcodes to /ca/ below, so
    # this hardcodes "CAD" to match (confirmed live 2026-09-26: real
    # salary_min/salary_max/salary_is_predicted values). `is_predicted`
    # means Adzuna's own ML estimate, not an employer-disclosed figure —
    # flagged with "(est.)" so it doesn't read as a real posted salary.
    return _format_salary_range(salary_min, salary_max, "CAD", "year", "est." if is_predicted else None)

_GREENHOUSE_URL_RE = re.compile(r"(?:job-boards|boards)\.greenhouse\.io/([^/]+)/jobs/(\d+)")
_LEVER_URL_RE = re.compile(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})")
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)[^>]*>.*?</\1>")

# NOTE: redirect_url is ALWAYS an adzuna.* URL (their own tracking/details
# link) whether or not it goes on to redirect somewhere else — so there's
# no way to tell "self-hosted, terminal" apart from "tracking link that
# 302s to a real ATS" by looking at redirect_url's domain alone. An
# earlier version of this file tried exactly that (skip anything on
# adzuna.*) and it was wrong: it would've skipped the Greenhouse/Lever
# redirects too, since those also start as adzuna.* links before
# following through. The only way to tell them apart is to actually
# attempt the request and see what comes back — see fetch_full_description
# below (and its KNOWN LIMITATION note for the case where it stays on
# adzuna.* and 403s).


def _collapse_whitespace(text: str) -> str:
    """Scraped page text turns every stripped block-level tag into its own
    line, so a page with a search widget/sidebar/nav (see fetch_adzuna's
    Adzuna-details-page fallback) comes back as mostly blank lines around a
    handful of real content lines. Strip each line and collapse RUNS of
    blank lines down to one — not zero, since a single blank line is often
    a legitimate paragraph break (e.g. Ashby's descriptionPlain) that's
    worth keeping, not layout noise to remove entirely."""
    lines = [ln.strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _html_page_to_text(html: str) -> str:
    """Rough main-content extraction for a page we don't have a structured
    API for: drop script/style blocks (JS/CSS text isn't content), strip
    remaining tags, collapse whitespace. Will include some nav/footer
    boilerplate on sites without a recognized ATS — still far more useful
    to the AI evaluation step than a one-sentence aggregator snippet."""
    html = _SCRIPT_STYLE_RE.sub(" ", html or "")
    return _collapse_whitespace(filters.strip_html(html).strip())


def fetch_via_browser(url: str, timeout_ms: int = 20000) -> str | None:
    """Last-resort fallback for pages a plain httpx GET can't get real
    content from. UPDATED DIAGNOSIS (2026-08-12): the earlier theory here
    was a Cloudflare bot-challenge (based on this sandbox's WebFetch
    getting 403 on adzuna.ca) — but a direct httpx print from
    fetch_full_description showed a plain 200 with just an empty PAGE
    SKELETON, not a 403 or a challenge page. adzuna.ca/details/ pages are
    a client-rendered SPA: the initial HTML response is a near-empty shell
    and the actual job content gets injected by JavaScript after load —
    httpx never executes JS, so it can NEVER see that content no matter
    what headers/User-Agent it sends. This is why wait_until="networkidle"
    matters below (waits for the page's own JS/API calls to finish
    populating content) rather than "domcontentloaded" (fires as soon as
    the empty shell HTML is parsed, before any of that has happened) — an
    earlier version of this function used domcontentloaded and would have
    captured the same empty skeleton a plain httpx GET already sees,
    making the whole fallback pointless. (WebFetch's 403 might still be a
    separate, genuine bot-block layered on top for that specific tool/UA —
    unconfirmed either way; the SPA-skeleton issue is the one a real
    browser actually needs to solve, and does, by executing the JS.)

    Only called from fetch_full_description as a fallback when the plain
    request path found nothing useful — and that's only ever reached from
    fetch_adzuna's already-gated call site (title/location passed, snippet
    looks truncated), so this doesn't fire for the bulk of raw results,
    only the smallish set that already look like real candidates. Still
    meaningfully slower than an httpx call (browser launch + full page
    render), so keep that gating in place upstream — don't call this
    unguarded.

    Requires the optional `playwright` dependency (`pip install playwright
    && playwright install chromium` — see requirements.txt). Returns None
    if playwright isn't installed, the page errors, or the page loads but
    still has nothing to show for it (e.g. network never goes idle within
    the timeout, or there really is a bot-challenge underneath the SPA
    shell) — same "fail quietly, caller falls back to the short snippet"
    contract as the rest of this module. NOT verified against the real
    adzuna.ca from this sandbox (no network access here to test with) —
    try it for real and report back what you see."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None

    try:
        # Playwright's sync API isn't safe for concurrent use across
        # threads (confirmed live 2026-09-28 — see playwright_lock.py).
        # This fallback can fire many times within one fetch_adzuna call
        # (once per gated job), across up to 4 concurrent Adzuna entries
        # in aggregators.yaml — the lock confines all of that, plus
        # fetch_uber/fetch_indeed's own Playwright usage, to one browser
        # session at a time, globally.
        with playwright_lock.PLAYWRIGHT_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                # networkidle, not domcontentloaded: the empty SPA shell
                # parses (and "DOM content loaded") almost instantly — the
                # real job content only shows up after the page's own JS
                # finishes fetching and rendering it, which is what
                # networkidle actually waits for.
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
                body_text = page.inner_text("body")
            finally:
                browser.close()
    except Exception:
        return None

    return _collapse_whitespace(body_text) or None


def fetch_full_description(redirect_url: str) -> dict:
    """Best-effort: follow an aggregator's redirect_url to the real posting
    and pull the full JD text. Adzuna's search API only ever returns a
    short teaser snippet — there's no "give me the full text" parameter —
    so this is the only way to get the real description.

    KNOWN LIMITATION, PARTIALLY MITIGATED (2026-08-12): when Adzuna is the
    terminal host for a job (no external ATS — the request lands on their
    own adzuna.ca/details/... page and stays there), a plain httpx request
    gets 403'd — confirmed with two real listings, and their API doesn't
    expose a full-description field anywhere either (checked their docs).
    As of 2026-08-12 this now falls back to a real headless browser (see
    fetch_via_browser) for that case, which has a real shot at clearing
    Cloudflare's bot-challenge where httpx has none — but isn't guaranteed
    (Cloudflare fingerprints headless browsers too) and hasn't been
    verified live from this sandbox (no network access here to test with).
    If it still comes back empty, jobs fall through to the short snippet
    unchanged, and ai_evaluate.py's looks_truncated() note is what keeps
    that from tanking match_score. This DOES still recover the full JD via
    the fast path for listings whose redirect_url is a tracking link that
    goes on to a recognized ATS (Greenhouse/Lever confirmed working; see
    below) — there's no reliable way to tell the two cases apart in
    advance, so every job still gets a real attempt at the fast path first.

    Returns {"description": str|None, "ats": str|None, "slug": str|None}.
    `description` is None only if EVERY path failed (plain request, known-
    ATS API, generic scrape, AND the browser fallback); callers must still
    fall back to the short snippet rather than let one bad job kill the
    run. `ats`/`slug` are set ONLY when the redirect resolved to a
    recognized ATS's own single-job API call that actually succeeded —
    that's the signal discover_companies.py uses to suggest adding the
    company to companies.yaml, since the slug is verified by a real
    successful request, not guessed from a URL (see companies.yaml's
    Coveo/Treewalk history for why that distinction matters — a slug
    parsed off a search-result URL burned real API calls on 404s twice
    before being caught).

    Prefers the source ATS's own JSON API (single-job endpoint — cheap,
    structured, matches what ats_clients.py already parses) when the
    redirect lands on a recognized board; falls back to scraping whatever
    HTML the final page returns; falls back further to a real browser if
    that HTML was empty/useless or the initial request failed outright."""
    empty = {"description": None, "ats": None, "slug": None}
    if not redirect_url:
        return empty

    resp = None
    try:
        resp = httpx.get(
            redirect_url, headers={"User-Agent": USER_AGENT},
            timeout=FULL_JD_TIMEOUT, follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception:
        resp = None  # plain request failed outright (403/timeout/DNS/etc) -> try the browser fallback below

    if resp is not None:
        final_url = str(resp.url)

        m = _GREENHOUSE_URL_RE.search(final_url)
        if m:
            slug, job_id = m.groups()
            try:
                job_resp = httpx.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}",
                    params={"content": "true"}, headers={"User-Agent": USER_AGENT}, timeout=FULL_JD_TIMEOUT,
                )
                job_resp.raise_for_status()
                content = job_resp.json().get("content")
                if content:
                    return {"description": content, "ats": "greenhouse", "slug": slug}
            except Exception:
                pass  # fall through to generic HTML scrape / browser fallback below

        m = _LEVER_URL_RE.search(final_url)
        if m:
            slug, posting_id = m.groups()
            try:
                job_resp = httpx.get(
                    f"https://api.lever.co/v0/postings/{slug}/{posting_id}",
                    params={"mode": "json"}, headers={"User-Agent": USER_AGENT}, timeout=FULL_JD_TIMEOUT,
                )
                job_resp.raise_for_status()
                data = job_resp.json()
                content = data.get("descriptionPlain") or data.get("description")
                if content:
                    return {"description": content, "ats": "lever", "slug": slug}
            except Exception:
                pass

        text = _html_page_to_text(resp.text)
        if text:
            return {"description": text, "ats": None, "slug": None}

    # Either the plain request failed outright, or it succeeded but there
    # was nothing usable in the response (e.g. a bot-challenge shell page
    # with no real content) — last resort, try a real browser.
    browser_text = fetch_via_browser(redirect_url)
    return {"description": browser_text, "ats": None, "slug": None}


def fetch_adzuna(params: dict) -> list[dict]:
    """Adzuna paginates — one page is only `results_per_page` (default 50)
    results, and the free tier's ranking means good matches can be a few
    pages deep. Fetches up to `max_pages` (default 5, set per-aggregator in
    aggregators.yaml), stopping early if a page comes back short (signals
    we've hit the end of Adzuna's result set for that query)."""
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError(
            "ADZUNA_APP_ID / ADZUNA_APP_KEY env vars not set — "
            "register for free at https://developer.adzuna.com"
        )

    params = dict(params)  # don't mutate the caller's dict (reused across runs)
    max_pages = params.pop("max_pages", 5)
    results_per_page = params.get("results_per_page", 50)

    jobs = []
    for page in range(1, max_pages + 1):
        url = f"https://api.adzuna.com/v1/api/jobs/ca/search/{page}"
        query = {
            "app_id": app_id,
            "app_key": app_key,
            "content-type": "application/json",
            **params,
        }
        resp = httpx.get(url, params=query, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        if not results:
            break

        for j in results:
            loc = (j.get("location") or {}).get("display_name", "")
            title = j.get("title", "")
            redirect_url = j.get("redirect_url", "")
            snippet = j.get("description", "")  # Adzuna gives a short snippet, not the full JD

            # Adzuna's structured `location` is a geographic field (e.g.
            # "Toronto, Ontario") — it doesn't reflect remote status even
            # when the posting clearly is remote (that only shows up in the
            # title/snippet text). Confirmed live 2026-09-27: `where:
            # "remote"` in aggregators.yaml itself returns 0 results,
            # because Adzuna's geocoder doesn't recognize "remote" as a
            # place — so "remote" has to be searched via `what`/
            # `what_phrase` instead, and this backfills the location string
            # so filters.location_is_allowed() (which only ever looks at
            # this field) doesn't drop a genuinely remote posting just
            # because Adzuna's own location field omits it.
            if "remote" not in loc.lower() and "remote" in (title + " " + snippet).lower():
                loc = f"Remote - {loc}" if loc else "Remote"

            description = snippet
            company_name = (j.get("company") or {}).get("display_name", "Unknown")
            # Only worth the extra request for jobs that already look like
            # real candidates (title+location pass) AND whose snippet is
            # actually thin — most of Adzuna's ~2000 raw results get
            # dropped by title/location alone, so gating on that first
            # keeps this from turning into ~2000 extra HTTP requests a run.
            if (
                filters.title_is_relevant(title)
                and filters.location_is_allowed(loc)
                and filters.looks_truncated(snippet)
            ):
                full = fetch_full_description(redirect_url)
                if full["description"] and len(full["description"]) > len(snippet):
                    description = full["description"]
                if full["ats"] and full["slug"]:
                    DISCOVERED_COMPANIES.append({
                        "name": company_name, "ats": full["ats"], "slug": full["slug"],
                    })

            jobs.append({
                "company": company_name,
                "title": title,
                "location": loc,
                "url": redirect_url,
                "posted_at": j.get("created"),
                "description": description,
                "salary": _format_adzuna_salary(j.get("salary_min"), j.get("salary_max"), j.get("salary_is_predicted")),
            })

        if len(results) < results_per_page:
            break  # short page -> no more results, stop paginating early
    return jobs


def fetch_remotive(params: dict) -> list[dict]:
    url = "https://remotive.com/api/remote-jobs"
    resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": j.get("company_name", "Unknown"),
            "title": j.get("title", ""),
            "location": j.get("candidate_required_location", ""),
            "url": j.get("url", ""),
            "posted_at": j.get("publication_date"),
            "description": j.get("description", ""),  # full HTML per Remotive's docs
            # A plain human-readable string when given at all (e.g. "$90k -
            # $105k", "$90 - $150 /hour") — confirmed live 2026-09-26, most
            # postings leave it "".
            "salary": j.get("salary") or None,
        })
    return jobs


def fetch_remoteok(params: dict) -> list[dict]:
    # https://remoteok.com/api — no auth. Confirmed live 2026-09-27: `tag`/
    # `location` query params do NOT filter server-side (a request with
    # `tag=data` returns the exact same 100 postings as no params at all),
    # so `params` is accepted for interface consistency with the other
    # fetchers but unused — this always returns the latest ~100 postings
    # across every category, and filters.yaml's title/stack rules do the
    # real filtering, same idea as a company with a huge board.
    url = "https://remoteok.com/api"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data:
        if "position" not in j:
            continue  # first element is RemoteOK's API-terms legal notice, not a job
        # `location` is very often "" (confirmed live: blank on most
        # postings) even though every RemoteOK listing is remote by
        # construction — always prefix "Remote" so
        # filters.location_is_allowed() has something to match rather than
        # dropping a real remote posting over an empty location string.
        raw_location = (j.get("location") or "").strip()
        jobs.append({
            "company": j.get("company", "Unknown"),
            "title": j.get("position", ""),
            "location": f"Remote - {raw_location}" if raw_location else "Remote",
            "url": j.get("url") or j.get("apply_url", ""),
            "posted_at": j.get("date"),
            "description": j.get("description", ""),
            # No currency field on RemoteOK's own API — assumed USD (the
            # site's own convention; confirmed live several real
            # salary_min/salary_max pairs, e.g. 70000-80000, no currency
            # marker anywhere in the response to contradict this).
            "salary": _format_salary_range(j.get("salary_min"), j.get("salary_max"), "USD", "year"),
        })
    return jobs


def fetch_jobicy(params: dict) -> list[dict]:
    # https://jobicy.com/api/v2/remote-jobs — no auth. `geo` (e.g. "europe",
    # "usa") DOES filter server-side (confirmed live), and `tag` accepts a
    # multi-word phrase (e.g. "data engineer", URL-encoded) that matches
    # job titles well — confirmed live: tag="data engineer" surfaced
    # "Senior Data Engineer", "AWS Data Engineer (Senior)", etc., not just
    # generic "dev" noise. Every Jobicy listing is remote by construction.
    url = "https://jobicy.com/api/v2/remote-jobs"
    resp = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for j in data.get("jobs", []):
        # `jobGeo` is a hiring-eligibility region (e.g. "USA", "Europe"),
        # not itself "remote" text, so it's folded into the location string
        # the same way as fetch_remoteok/fetch_adzuna.
        geo = (j.get("jobGeo") or "").strip()
        jobs.append({
            "company": j.get("companyName", "Unknown"),
            "title": j.get("jobTitle", ""),
            "location": f"Remote - {geo}" if geo else "Remote",
            "url": j.get("url", ""),
            "posted_at": j.get("pubDate"),
            "description": j.get("jobDescription", ""),  # full HTML per Jobicy's docs
            # Real structured currency+period fields, unlike RemoteOK —
            # confirmed live real values (e.g. 160000-195000 USD yearly).
            # Jobicy's "yearly"/"hourly" -> this module's "year"/"hour".
            "salary": _format_salary_range(
                j.get("salaryMin"), j.get("salaryMax"), j.get("salaryCurrency"),
                (j.get("salaryPeriod") or "").removesuffix("ly") or None,
            ),
        })
    return jobs


def fetch_weworkremotely(params: dict) -> list[dict]:
    # https://weworkremotely.com/categories/<category>.rss — no auth, RSS
    # (not JSON). Confirmed live 2026-09-27: the general
    # "remote-programming-jobs" category (the default) mixes titles across
    # sub-disciplines; category-specific feeds exist for some sub-areas
    # (e.g. "remote-full-stack-programming-jobs", 40 items;
    # "remote-back-end-programming-jobs", 6 items) but there's no
    # data-engineering-specific one, and an old "remote-data-jobs" slug
    # 301-redirects to nothing parseable — so this leans on filters.yaml's
    # title/stack rules for precision, same as fetch_remoteok. Every
    # listing is remote by construction. No salary field anywhere in the
    # feed. Title is "Company: Job Title" — split on the first ": ".
    category = params.get("category", "remote-programming-jobs")
    url = f"https://weworkremotely.com/categories/{category}.rss"
    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)

    jobs = []
    for item in root.findall(".//item"):
        raw_title = (item.findtext("title") or "").strip()
        company, sep, title = raw_title.partition(": ")
        if not sep:
            company, title = "Unknown", raw_title
        # `region` (e.g. "USA Only", "Anywhere in the World") is a hiring-
        # eligibility hint, not itself "remote" text, so it's folded into
        # the location string the same way as the other fetchers here.
        region = (item.findtext("region") or "").strip()
        jobs.append({
            "company": company,
            "title": title,
            "location": f"Remote - {region}" if region else "Remote",
            "url": item.findtext("link") or item.findtext("guid") or "",
            "posted_at": item.findtext("pubDate"),
            "description": item.findtext("description") or "",
            "salary": None,
        })
    return jobs


_LINKEDIN_CARD_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.DOTALL)
_LINKEDIN_TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL)
_LINKEDIN_COMPANY_RE = re.compile(r"<h4[^>]*>(.*?)</h4>", re.DOTALL)
_LINKEDIN_LOCATION_RE = re.compile(r'class="job-search-card__location"[^>]*>(.*?)</span>', re.DOTALL)
_LINKEDIN_URL_RE = re.compile(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"]+)"')
_LINKEDIN_DATE_RE = re.compile(r'datetime="([^"]+)"')
# Stops at the "description__job-criteria-list" that immediately follows
# the description on every real job page sampled — a landmark chosen over
# trying to match a specific closing-tag sequence (</div></div></section>,
# what an earlier version of this regex did) because the description's own
# internal nesting depth isn't fixed: a naive closing-tag match found
# nearby matched a real position but the WRONG one, silently truncating
# the captured text to only the first paragraph, and only real HTML
# (checked live 2026-09-27) caught it — a hand-built fixture using a
# plausible-looking but not-quite-real nesting depth passed clean.
_LINKEDIN_DESC_RE = re.compile(
    r'class="description__text description__text--rich">(.*?)<ul class="description__job-criteria-list"',
    re.DOTALL,
)


def _clean_html_text(raw: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", raw or "")).strip()


def _parse_linkedin_cards(page_html: str) -> list[dict]:
    """Extract job cards from a LinkedIn Guest API search-results HTML
    response (see fetch_linkedin). Regex, not an HTML parser, because
    there's no structured API here to prefer — same tradeoff as
    _html_page_to_text elsewhere in this module, just targeted at a known
    card layout instead of an arbitrary page. Confirmed live 2026-09-27
    against real "data engineer" / Canada results: title/company/location/
    date/url all extracted correctly for every one of 20 real postings
    sampled across 2 pages (Snowflake Data Engineer @ Propel, Lead Data
    Engineer @ Nasdaq, Senior Data Engineer @ HelloFresh, etc.)."""
    jobs = []
    for card in _LINKEDIN_CARD_RE.findall(page_html):
        title_m = _LINKEDIN_TITLE_RE.search(card)
        title = _clean_html_text(title_m.group(1)) if title_m else ""
        if not title:
            continue
        company_m = _LINKEDIN_COMPANY_RE.search(card)
        location_m = _LINKEDIN_LOCATION_RE.search(card)
        url_m = _LINKEDIN_URL_RE.search(card)
        date_m = _LINKEDIN_DATE_RE.search(card)
        jobs.append({
            "title": title,
            "company": _clean_html_text(company_m.group(1)) if company_m else "Unknown",
            "location": _clean_html_text(location_m.group(1)) if location_m else "",
            "url": url_m.group(1).split("?")[0] if url_m else "",
            "posted_at": date_m.group(1) if date_m else None,
        })
    return jobs


def _fetch_linkedin_description(job_url: str) -> str:
    """The search-results HTML has no description at all — only the job's
    own page does, one extra request per job. Confirmed live 2026-09-27:
    unlike LinkedIn's main site, a public job/view page is served to a
    plain unauthenticated GET (no login wall for viewing, only for
    applying), so this is a normal httpx call, no browser needed. Best-
    effort: any failure just means no description, not a crashed run."""
    try:
        resp = httpx.get(job_url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
        m = _LINKEDIN_DESC_RE.search(resp.text)
        return _collapse_whitespace(_clean_html_text(m.group(1))) if m else ""
    except Exception:
        return ""


def fetch_linkedin(params: dict) -> list[dict]:
    # LinkedIn's public "Guest API" — the same unauthenticated endpoint
    # linkedin.com/jobs' own search page calls for non-logged-in visitors,
    # confirmed live 2026-09-27 (no auth, no Playwright needed, real
    # results for "data engineer" / Canada). This is unofficial/
    # undocumented — like Workday's CXS or Gem's GraphQL API elsewhere in
    # this codebase, it could change or start rate-limiting without
    # notice; a short delay between pages here is deliberate, not just
    # politeness.
    keywords = params.get("keywords", "")
    location = params.get("location", "")
    time_range = params.get("time_range", "r604800")  # r604800 = last 7 days
    max_pages = params.get("max_pages", 2)

    jobs = []
    seen_urls: set[str] = set()
    for page_num in range(max_pages):
        if page_num > 0:
            time.sleep(1.5)
        query = urllib.parse.urlencode({
            "keywords": keywords, "location": location, "f_TPR": time_range, "start": page_num * 10,
        })
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{query}"
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            cards = _parse_linkedin_cards(resp.text)
        except Exception:
            break
        if not cards:
            break  # short/empty page -> no more results

        for c in cards:
            if not c["url"] or c["url"] in seen_urls:
                continue
            seen_urls.add(c["url"])

            description = ""
            # Only worth the extra per-job request for postings that
            # already look like real candidates — same gating idea used
            # throughout this module.
            if filters.title_is_relevant(c["title"]) and filters.location_is_allowed(c["location"]):
                description = _fetch_linkedin_description(c["url"])

            jobs.append({
                "company": c["company"],
                "title": c["title"],
                "location": c["location"],
                "url": c["url"],
                "posted_at": c["posted_at"],
                "description": description,
                # No salary anywhere in either the search card or the job
                # page HTML (confirmed live) — LinkedIn just doesn't
                # surface it on public/guest views.
                "salary": None,
            })
    return jobs


def fetch_indeed(params: dict) -> list[dict]:
    # Indeed blocks plain HTTP entirely (confirmed live 2026-09-27: a bare
    # httpx GET against a search page gets a 403, and even a Playwright
    # browser navigating DIRECTLY to a /viewjob?jk=... detail URL gets
    # redirected to an explicit "from=bot-detection-anonymous" wall) — a
    # real headless browser clicking through FROM a search results page,
    # the way an actual visitor would, is the only path that works.
    # Optional dependency, same contract as fetch_via_browser above: no
    # playwright installed -> warn and return [], don't crash the run.
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[WARN] Indeed requires the optional `playwright` dependency "
              "(pip install playwright && playwright install chromium) — skipping.")
        return []

    query = params.get("query", "")
    location = params.get("location", "")
    sort = params.get("sort", "date")
    base = params.get("base_url", "https://ca.indeed.com")  # swap for another country's Indeed site if needed

    jobs = []
    try:
        # Playwright's sync API isn't safe for concurrent use across
        # threads (confirmed live 2026-09-28 — see playwright_lock.py) —
        # held for this whole fetch so it never overlaps with another
        # Indeed entry's own fetch, or ats_clients.fetch_uber's.
        with playwright_lock.PLAYWRIGHT_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page(user_agent=USER_AGENT)
                search_url = f"{base}/jobs?{urllib.parse.urlencode({'q': query, 'l': location, 'sort': sort})}"
                page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(3000)

                cards = page.locator(".job_seen_beacon").all()
                for card in cards:
                    title_el = card.locator("a.jcs-JobTitle, h2.jobTitle a").first
                    if title_el.count() == 0:
                        continue
                    title = (title_el.text_content() or "").strip()
                    # The canonical detail URL is built from data-jk, NOT
                    # the card's href — confirmed live: sponsored ("pagead")
                    # cards' href is a one-time ad-click-tracking redirect,
                    # not a stable URL, which would break URL-based dedup
                    # across runs even though data-jk (present on every
                    # card, sponsored or not) is stable.
                    jk = title_el.get_attribute("data-jk")
                    if not title or not jk:
                        continue
                    job_url = f"{base}/viewjob?jk={jk}"

                    company_el = card.locator("[data-testid='company-name']").first
                    company = (company_el.text_content() or "").strip() if company_el.count() > 0 else "Unknown"
                    loc_el = card.locator("[data-testid='text-location']").first
                    location_text = (loc_el.text_content() or "").strip() if loc_el.count() > 0 else location
                    salary_el = card.locator('[data-testid*="salary-snippet-container"]').first
                    salary = (salary_el.text_content() or "").strip() if salary_el.count() > 0 else None

                    description = ""
                    # No description/snippet at all on the search card
                    # (confirmed live — only badges like salary/employment
                    # type) — the ONLY way to get it is the in-page click-
                    # through below, so gate that on title/location first,
                    # same idea as everywhere else in this module, to keep
                    # a page of noisy results from turning into 15+ clicks.
                    if (
                        filters.title_is_relevant(title)
                        and filters.location_is_allowed(location_text)
                        and title_el.is_visible()
                    ):
                        try:
                            title_el.click(timeout=5000)
                            page.wait_for_timeout(2000)
                            pane = page.locator(
                                ".simple-job-description-html, #jobDescriptionText, "
                                ".jobsearch-JobComponent-description"
                            ).first
                            if pane.count() > 0:
                                description = _collapse_whitespace(pane.inner_text())
                        except Exception:
                            pass  # best-effort — keep the empty description, don't fail the run

                    jobs.append({
                        "company": company,
                        "title": title,
                        "location": location_text,
                        "url": job_url,
                        "posted_at": None,  # not exposed on the search card; not worth another click just for this
                        "description": description,
                        "salary": salary,
                    })
            finally:
                browser.close()
    except Exception as exc:
        print(f"[WARN] Indeed scrape failed: {exc}")

    return jobs


FETCHERS = {
    "adzuna": fetch_adzuna,
    "remotive": fetch_remotive,
    "remoteok": fetch_remoteok,
    "jobicy": fetch_jobicy,
    "weworkremotely": fetch_weworkremotely,
    "linkedin": fetch_linkedin,
    "indeed": fetch_indeed,
}


def fetch_aggregator(aggregator: dict) -> list[dict]:
    fetcher = FETCHERS.get(aggregator["type"])
    if fetcher is None:
        raise ValueError(f"No fetcher for aggregator type: {aggregator['type']}")
    jobs = fetcher(aggregator.get("params", {}))
    for j in jobs:
        j["source"] = aggregator["type"]
    return jobs