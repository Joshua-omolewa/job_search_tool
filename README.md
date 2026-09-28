# AI-Powered Job Search Automation


An automated pipeline for a targeted job search: fetch open roles directly
from companies' ATS APIs and job aggregators, filter out anything
irrelevant, deduplicate against everything already seen, optionally score
each candidate's fit against your own experience with Claude, and review
the results in a local web board.

Nothing here talks to any service other than the job sources you configure
and (for the optional AI step) the Anthropic API. All state — scraped
jobs, scores, your own notes — lives in a local SQLite database; nothing
is sent to a third party beyond fetching the postings themselves.

<img width="2551" height="1220" alt="image" src="https://github.com/user-attachments/assets/638470ad-ce73-4515-b03c-4ff28e89d874" />


## How it works

1. **Fetch** (`app/main.py`) — pulls open roles from:
   - `companies.yaml` — known companies queried directly via their ATS.
     31 ATS types supported, from mainstream ones with a clean public API
     (Greenhouse, Ashby, Workable, Lever, SmartRecruiters, Workday,
     BambooHR, Rippling, Teamtailor, Gem) to enterprise platforms
     (Oracle Cloud Recruiting, SAP SuccessFactors, iCIMS, Eightfold,
     Cornerstone OnDemand, UKG/UltiPro), HTML-only portals with no API at
     all (Avature, HRDepartment, the Humi hosted-careers-page product),
     multi-tenant career-site SaaS products (Ongig, Kula.ai), and a
     handful of large companies' own custom career sites reverse-engineered
     individually (Google, Apple, Meta, Shopify, Amazon, IBM, Uber,
     Atlassian, TikTok, gr8people) — precise, low noise either way.
     Company fetches run concurrently (a thread pool, default 10 workers —
     see `--workers` below), since with 400+ companies configured, fetching
     one at a time was itself the bottleneck on a full run.
   - `aggregators.yaml` — broad keyword+location search across many
     employers at once (Adzuna, Remotive, RemoteOK, Jobicy,
     WeWorkRemotely, LinkedIn, Indeed) — wider reach, more noise. Also
     fetched concurrently, same thread pool.
2. **Filter** (`app/filters.py`) — drops anything that isn't an
   engineering-shaped title, isn't in an allowed location, or whose JD
   requires a stack you've excluded.
3. **Dedup** (`app/dedup.py`) — everything fetched is stored in a local
   SQLite database (`data/seen_jobs.sqlite3`), keyed by URL and by
   normalized company+title, so re-runs only ever surface genuinely new
   postings.
4. **AI evaluation** (`app/ai_evaluate.py`, optional, costs money) — sends
   each filtered candidate plus your `profile.yaml` to Claude, which scores
   fit (0-100) and returns concrete gaps, transferable strengths, risk
   factors, and an apply/consider/skip recommendation.
5. **Review** (`web/`) — a local Next.js app reading/writing the same
   SQLite database directly, for browsing results and tracking your own
   `applied / interview / rejected / skipped / silence` status and notes.
   Filter by AI status, your status, location, source (which fetcher found
   it — Greenhouse, LinkedIn, Indeed, etc.), or free-text search; each row
   also shows a best-effort salary (when the ATS discloses one) and how
   long ago it was posted. See [`web/README.md`](web/README.md) for what
   each column means.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# fill in .env with the keys you need — see the comments in that file
```

Create your experience profile from a template (`profile.yaml` itself is
gitignored — it's never committed, since it holds your personal
experience):

```bash
cp profile.general_template.yaml profile.yaml
# then edit profile.yaml with your own background
```

[`profile.general_template.yaml`](profile.general_template.yaml) is the
general-purpose blank template — adaptable to any field, `<PLACEHOLDER>`
fields throughout, and it opens with a ready-to-copy prompt for having
Claude fill it in from your resume instead of doing it by hand. If you're
specifically a software engineer,
[`profile.senior_software_engineer_template_2.yaml`](profile.senior_software_engineer_template_2.yaml)
is a lighter starting point already shaped for that role (stack and
competency categories pre-set).

Either way, it's worth seeing what a properly filled-in profile looks
like before you write your own: for fully worked examples showing the
level of specificity/quantification each evidence bullet should actually
have — not something to copy verbatim, but useful as a target for
"good" — see
[`profile.senior_software_engineer_template.yaml`](profile.senior_software_engineer_template.yaml)
(fictional, general full-stack background) or
[`profile.data_engineer_template.yaml`](profile.data_engineer_template.yaml)
(data-engineering-shaped — lakehouse/streaming/platform work — pairs with
`filters.yaml`'s shipped Data Engineer defaults below).

`ANTHROPIC_API_KEY` is only needed for the AI evaluation step. Adzuna
(`ADZUNA_APP_ID`/`ADZUNA_APP_KEY`) is only needed if you keep an Adzuna
entry in `aggregators.yaml` — register a free key at
[developer.adzuna.com](https://developer.adzuna.com). Remotive needs no
auth but only covers remote roles.

### Customize for your own search

This repo ships pre-configured for the original author's search (Alberta/
Canada, targeting Data Engineer / Senior / Staff Data Engineer / Data
Platform Engineer titles, Python + data-stack dealbreakers). **Before
your first run, edit the following** (items 1-3 are in `filters.yaml`,
4-5 are the company/aggregator registries) or you'll get zero candidates,
or candidates that don't match your actual role/stack:

1. **`filters.yaml` → `priority_title_keywords` / `title_allow_keywords`**
   — ships deliberately narrow to Data Engineer-shaped titles (no bare
   "engineer"/"developer"/"platform") so generic software/frontend/
   hardware roles never reach the AI step. If you're targeting a
   different title — Software Engineer, for instance — either broaden
   these lists yourself, or just swap in
   [`filters_software_engineer_template.yaml`](filters_software_engineer_template.yaml),
   a ready-made alternative tuned for general software engineering roles
   (`cp filters_software_engineer_template.yaml filters.yaml`) — pairs
   with `profile.senior_software_engineer_template_2.yaml`/
   `profile.senior_software_engineer_template.yaml` above.
2. **`filters.yaml` → `location_allow_patterns`** — regex patterns for
   locations to keep. Ships as Canada/Alberta-only; replace with your own
   country/region/cities, or delete entries to broaden it. This is the
   #1 reason a first run returns nothing — if nothing you fetch ever
   matches these patterns, `location_is_allowed()` rejects every job.
3. **`filters.yaml` → `stack_dealbreakers` / `stack_core`** — a JD is
   rejected if it mentions a `stack_dealbreakers` language and none of
   `stack_core`. Ships assuming a Python/data stack (PySpark, SQL, dbt,
   Snowflake, Databricks, Airflow, ...) and rejects Java/C#/.NET/Ruby/
   Rails/PHP/Kotlin/Swift roles. If your own stack includes one of the
   "dealbreaker" languages, move it into `stack_core` (or the filter will
   reject roles in your own stack) — `filters_software_engineer_template.yaml`
   above ships a different, broader stack_core if you're not targeting
   data engineering specifically.
4. **`companies.yaml`** — the company registry. Ships with the original
   author's real target list; add/remove companies to match who you're
   actually applying to (see the file's header comment for the format).
5. **`aggregators.yaml`** — ships tuned for the same Data Engineer /
   Canada search as `filters.yaml` above, so every entry needs both its
   title keyword and its location edited for a different search:
   - **Title keyword** — each aggregator's own param name for it:
     Adzuna's `what_phrase`, Jobicy's `tag`, LinkedIn's `keywords`,
     Indeed's `query`. (Remotive/RemoteOK/WeWorkRemotely have no keyword
     param at all — they return their latest postings regardless, and
     `filters.yaml`'s title matching does the real narrowing for those
     three.)
   - **Location** — Adzuna's `where` and LinkedIn's/Indeed's `location`
     are all `"Canada"`; replace with your own country/region. Remotive/
     RemoteOK/Jobicy/WeWorkRemotely are remote-only by construction and
     have no location param to set.
   Whatever you change these to, make sure a real result would still match
   `filters.yaml`'s `title_allow_keywords`/`location_allow_patterns`, or
   it'll get filtered out downstream anyway — see the file's own header
   comment for per-aggregator quirks (e.g. Adzuna's `where: "remote"`
   silently returning 0 results).

## Usage

### 1. Fetch + filter (free)

```bash
python -m app.main                    # all companies + aggregators
python -m app.main --company affirm   # just one company, for debugging a fetcher
python -m app.main --skip-aggregators # companies.yaml only
python -m app.main --skip-companies   # aggregators.yaml only
python -m app.main --workers 20       # more/fewer concurrent fetches (default 10)

make run                              # same thing, interactive prompts instead of flags
```

Output: `data/candidates.csv`, appended to on every run.

### 2. AI evaluation (optional, costs money)

```bash
python -m app.ai_evaluate --dry-run   # see what's queued, no API calls, no cost
python -m app.ai_evaluate --limit 20  # score just 20, to sanity-check quality/cost first
python -m app.ai_evaluate             # score everything unscored

make evaluate                         # interactive prompts instead of flags
```

Results are stored in SQLite (so re-running never re-pays for a job
already scored) and written to `data/scored_candidates.csv`, sorted
best-match-first.

Evaluations run 5 at a time by default (each is an independent Claude
call, so this is mostly free speedup — ~4-5x faster on a real run). Tune
with `AI_EVALUATE_WORKERS` if your Anthropic usage tier comfortably
supports more, or want to dial it back:

```bash
AI_EVALUATE_WORKERS=10 python -m app.ai_evaluate
```

### 3. Review results

```bash
cd web && npm install    # first time only
cd ..
make web                              # starts the board at http://localhost:3000
```

Reads/writes `data/seen_jobs.sqlite3` directly — no export/import step.
See [`web/README.md`](web/README.md) for details (custom `DB_PATH`,
production build, etc).

## Makefile commands

Thin wrappers over the commands above — run from the repo root:

| Command        | Equivalent to                    | What it does |
|-----------------|----------------------------------|--------------|
| `make run`      | `python -m app.main`             | Fetch + filter (step 1). Interactively asks whether to skip companies/aggregators/discovery and whether to limit to one company slug, instead of you remembering the flags. |
| `make evaluate` | `python -m app.ai_evaluate`       | AI evaluation (step 2). Interactively asks for `--dry-run` and an optional `--limit`. |
| `make web`      | `npm --prefix web run dev`       | Starts the Next.js review board, with `DB_PATH` already pointed at `data/seen_jobs.sqlite3`. |
| `make test`     | `pytest app/` + the standalone sanity-check scripts | Runs the full test suite (see the Tests section below). |

`make` with no target runs `make run` (the default goal).

## Configuration

- **`companies.yaml`** — the company registry (name, ATS type, slug). Add
  a company here once you've identified its ATS — see the file's header
  comment for all 31 supported ATS types' slug formats (Greenhouse, Ashby,
  Workable, Lever, SmartRecruiters, Workday, BambooHR, Rippling,
  Teamtailor, Gem, Oracle Cloud Recruiting, SAP SuccessFactors, iCIMS,
  Eightfold, Cornerstone OnDemand, Avature, HRDepartment, gr8people,
  UKG/UltiPro, Kula.ai, Ongig, the Humi hosted-careers-page platform, and
  Google/Apple/Meta/Shopify/Amazon/IBM/Uber/Atlassian/TikTok's own custom
  career sites).
- **`aggregators.yaml`** — aggregator search config (keywords, location,
  pagination limits) across Adzuna, Remotive, RemoteOK, Jobicy,
  WeWorkRemotely, LinkedIn, and Indeed. Only Adzuna needs auth (a free
  app_id/app_key). Indeed additionally needs the optional `playwright`
  dependency (see requirements.txt) — it's the only one that can't be
  scraped without a real browser; the rest are plain HTTP/API calls. See
  the file's header comment for each one's query params and quirks.
- **`filters.yaml`** — title allowlist/exclusion keywords, location
  allowlist patterns, and JD stack-dealbreaker/core-stack keywords. Edit
  this directly as you refine what counts as in-scope for you — no code
  changes needed (matching logic lives in `app/filters.py`). Ships tuned
  for Data Engineer roles; see
  [`filters_software_engineer_template.yaml`](filters_software_engineer_template.yaml)
  for a ready-made alternative tuned for general software engineering
  roles instead.
- **`profile.yaml`** — your experience profile fed to the AI evaluation
  step (see `profile.general_template.yaml` for the general blank
  template, `profile.senior_software_engineer_template_2.yaml` for a
  software-engineer-shaped blank starting point,
  `profile.senior_software_engineer_template.yaml` for a fully worked
  software-engineer example, and `profile.data_engineer_template.yaml`
  for a fully worked data-engineer example).

## Project layout

```
app/
  main.py                 orchestrates fetch -> filter -> dedup -> candidates.csv
  ats_clients.py           one fetch function per ATS
  aggregator_clients.py    one fetch function per aggregator
  playwright_lock.py       shared lock serializing every Playwright-based fetcher (see Notes on scope)
  filters.py               loads and applies filters.yaml's rules
  dedup.py                 SQLite store (seen_jobs, job_details)
  discover_companies.py    auto-appends newly-resolved companies to companies.yaml
  ai_evaluate.py           stage 2: Claude-based fit scoring
  inspect_job.py           CLI to look up a stored job or list recent rejections
  refilter.py              re-runs current filters.py against already-fetched jobs
  scripts/                 one-off diagnostic/maintenance scripts, not part of the pipeline
  tests/                   pytest + standalone sanity-check scripts
web/                       Next.js review board (see web/README.md)
companies.yaml             company -> ATS registry
aggregators.yaml           aggregator search config
filters.yaml               title/location/stack filter rules (ships tuned for Data Engineer roles)
filters_software_engineer_template.yaml             alternative filters.yaml tuned for software engineering roles
profile.general_template.yaml                       general-purpose blank template for profile.yaml (your real profile, gitignored)
profile.senior_software_engineer_template_2.yaml     blank template pre-shaped for a software engineer
profile.senior_software_engineer_template.yaml       fully worked (fictional) software-engineer example
profile.data_engineer_template.yaml                 fully worked data-engineer example
```

## Tests

```bash
make test
```

Runs the pytest suite plus the standalone sanity-check scripts
(`test_pipeline.py`, `test_discover_companies.py`, `test_ai_evaluate.py`).
None of them call live external APIs.

## Notes on scope

- ATS fetchers for Greenhouse, Ashby, Workable, Lever, BambooHR, Rippling,
  and Teamtailor were each validated against real live responses.
  SmartRecruiters support is built from documentation and third-party
  corroboration only — verify a new SmartRecruiters company with
  `python -m app.main --company <slug>` before trusting it in a real run.
- Workday and Gem support call the same undocumented internal API each
  platform's own public careers page uses (not a published product for
  either) — verified live against several real companies, but could
  change without notice. If a newly-added Workday or Gem company comes
  back with 0 jobs, verify the slug against a live network request from
  that company's careers page before assuming the code is wrong — see the
  CONFIDENCE NOTEs in `app/ats_clients.py` for how each was reverse-engineered.
- 12 more ATS types were added 2026-09-27 (Oracle Cloud Recruiting, SAP
  SuccessFactors, iCIMS, Eightfold, Cornerstone OnDemand, Avature,
  HRDepartment, gr8people, and Google/Apple/Meta/Shopify's own custom
  career sites) to cover large companies previously checked and
  deliberately left out of `companies.yaml` for having no obvious public
  ATS API — each turned out to be reachable unauthenticated after all,
  just not through a clean documented REST endpoint. Where no real API
  exists at all (Avature, HRDepartment), the fetcher scrapes plain HTML
  instead, same approach already used for LinkedIn in
  `app/aggregator_clients.py`. Every one of these is either an
  undocumented internal API or an HTML scrape, so treat them with the same
  "verify a new company before trusting it" caution as Workday/Gem above —
  see the CONFIDENCE NOTES in `app/ats_clients.py`'s module docstring and
  `companies.yaml`'s header comment for the full detail per type.
- 9 more ATS types were added 2026-09-28 (Amazon, IBM, Uber, Atlassian,
  Kula.ai, UKG/UltiPro, the Humi hosted-careers-page platform, TikTok,
  Ongig) while re-investigating ~20 previously-excluded companies —
  IBM's own site (`careers.ibm.com`) is still hard AWS-WAF-blocked, but
  its real search backend (`www-api.ibm.com/search/api/v2`) isn't, and
  works unauthenticated; Electronic Arts (previously excluded — its old
  gr8people tenant, `ea.gr8people.com`, blocked requests from outside its
  network) has since migrated its whole careers site to Avature on a
  custom domain (`jobs.ea.com`), which `fetch_avature` now supports
  alongside the usual `{tenant}.avature.net` pattern. Kula.ai, UKG/UltiPro,
  Ongig, and the Humi platform are each a multi-tenant career-site
  product used by more than one company (same shape as Avature/
  HRDepartment above) — the fetcher is generic per platform, so adding
  another company already on one of these is just a new `companies.yaml`
  entry with that company's own tenant/account id, no new code needed.
- Every Playwright-based fetcher (Uber, Indeed, and Adzuna's headless-
  browser fallback for job pages it can't otherwise reach) shares one
  lock (`app/playwright_lock.py`) so only one browser session ever runs
  at a time, globally — Playwright's sync API isn't safe for concurrent
  use across threads, which only matters now that companies/aggregators
  fetch concurrently (see above); without it, multiple Playwright-based
  fetches running at once visibly stalled a real run.
- A 429 from an ATS's API is treated as a transient rate limit, not a
  real failure — `_request_with_retry` in `app/ats_clients.py` retries
  with capped exponential backoff (honoring a `Retry-After` header up to
  a hard ceiling, so a server can't force an unbounded wait), currently
  wired into Workday's three request call sites. This started mattering
  once companies fetch concurrently: several Workday tenants share the
  same backend CDN even though they're different companies' own boards,
  and a burst of concurrent requests spread across different companies
  was enough to trip a shared rate limiter that a one-at-a-time run never
  hit.
- Workday's fetcher parallelizes both list-page pagination and per-job
  detail requests, since a large board (thousands of postings) made of
  sequential one-at-a-time requests was slow enough to matter in practice.
- Salary is extracted per-posting, not guaranteed per company: a real
  structured field when the ATS has one (Lever, SmartRecruiters,
  Teamtailor, and Greenhouse's own pay-transparency HTML block, all
  confirmed live with real data), falling back to a keyword-anchored scan
  of the JD text everywhere else (the only path for Ashby/Workday, which
  have no structured field at all) — see the `salary` section of
  `app/ats_clients.py`'s module docstring for the full breakdown per ATS,
  and its sanity-check guards against real data-entry errors (an unfilled
  template placeholder, a mistyped range) caught live in source postings.
- AI evaluation runs several jobs concurrently (`AI_EVALUATE_WORKERS`,
  default 5) rather than one Claude call at a time — ~4-5x faster on a
  real run, see the AI evaluation section above.
- RemoteOK, Jobicy, and WeWorkRemotely were each validated against real
  live responses, including a full end-to-end pipeline run (fetch ->
  filter -> dedup) across all `aggregators.yaml` entries together. None
  of the three support real server-side title filtering (RemoteOK/
  WeWorkRemotely have no keyword param at all; Jobicy's `tag` is a loose
  word-level match, not an exact phrase) — they always return their
  latest ~25-100 postings, and `filters.yaml`'s title/stack rules do the
  real narrowing, same as a company with a huge board. RemoteOK uses
  `salary_min == salary_max == 0` to mean "no salary given," not an
  actual $0 — caught live and guarded against, not hypothetical (83 of 99
  real postings sampled had this).
- LinkedIn and Indeed were each validated against real live responses,
  including a full end-to-end pipeline run (fetch -> filter -> dedup)
  against real Canada/Data-Engineer results — 18 of 20 LinkedIn results
  and 3 of 16 Indeed results passed filters and were new, in one live
  test run. LinkedIn calls the same public, unauthenticated "Guest API"
  its own signed-out job search page uses (undocumented/unofficial, like
  Workday's CXS or Gem's GraphQL API in `ats_clients.py` — could change or
  start rate-limiting without notice). Indeed blocks plain HTTP entirely
  (confirmed live: a bare request gets 403, and even a real browser
  navigating directly to a job's detail URL gets redirected to an
  explicit bot-detection wall) — only a headless browser clicking through
  from a search page, like a real visitor, gets past it, which is why
  `fetch_indeed` needs the optional `playwright` dependency and is by far
  the slowest aggregator here (a real browser launch, page render, and an
  in-page click per job worth fetching the full description for).
- If a particular company's fetch fails, `main.py` logs a warning and
  continues with the rest rather than crashing the whole run.

## Possible next steps

- Company-name normalization for aggregator-sourced dedup (the same
  posting sometimes comes back under slightly different company name
  strings, e.g. "Acme Corp" vs. "Acme").
- A digest output (email/Sheet) beyond the current CSV/SQLite/web-board
  review flow.
- Scheduling: once you're happy with a full local run, a daily cron entry
  like `0 7 * * * cd /path/to/job_search_pipeline && python -m app.main && python -m app.ai_evaluate`.

## License

[MIT](LICENSE)
