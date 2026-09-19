"""Deterministic pre-filters: title allowlist + exclusion keywords +
location allowlist + JD stack dealbreakers.

These run BEFORE any AI call — the whole point is to keep the AI-scored
volume small and cheap. Keep this file boring and easy to edit by hand.

Design note (2026-08-11 rewrite): an EXCLUSION-only approach doesn't scale
to a company like Affirm that posts hundreds of non-engineering roles
(Analyst, Compliance, Marketing, Customer Success, Talent Brand, Sales
Development...) — you can't blacklist your way out of that. So title
filtering is now allowlist-first: the title must look like an engineering
role AT ALL before we even consider it, then a smaller exclusion list
catches engineering-adjacent titles that aren't a fit (sales engineer,
hardware/mechanical engineer, etc).
"""
import re

# Strong, unambiguous engineering-role signals. If the title matches one of
# these, it's kept regardless of EXCLUSION_KEYWORDS below — added
# 2026-08-11 after "Software Engineer, Automated Marketing" got dropped for
# containing "marketing", even though "Software Engineer" alone makes the
# role unambiguous. Keep this list short and genuinely unambiguous (don't
# add anything from TITLE_ALLOW_KEYWORDS that's also ambiguous, like bare
# "engineer" or "platform" — those still need the exclusion list's help).
PRIORITY_TITLE_KEYWORDS = [
    "software engineer", "software developer",
    "developer",
    "full stack", "fullstack", "full-stack",
]

# Title must match at least one of these to be considered at all.
TITLE_ALLOW_KEYWORDS = [
    "engineer", "engineering",  # covers Engineer / Engineering Manager / Software Engineering
    "developer", "development",
    "programmer",
    "architect",
    "sre", "site reliability",
    "devops",
    "full stack", "fullstack", "full-stack",
    "backend", "back-end", "back end",
    "platform",
    "tech lead", "technical lead",
]

# Even if the title matched the allowlist above, drop it if it also
# matches one of these — engineering-adjacent titles that aren't a fit.
EXCLUSION_KEYWORDS = [
    "accountant", "accounting",
    "nurse", "nursing",
    "lawyer", "attorney", "paralegal",
    "chemist", "chemistry",
    "electrician", "plumber", "welder",
    "sales representative", "account executive", "sales engineer",
    "sales development", "business development representative",
    "recruiter", "talent acquisition", "talent brand",
    "warehouse", "forklift",
    "driver", "cdl",
    "teacher", "professor",
    "dentist", "physician", "pharmacist",
    "mechanical engineer", "electrical engineer", "civil engineer",
    "chemical engineer", "industrial engineer", "manufacturing engineer",
    "hardware engineer", "process engineer", "field engineer",
    "customer success", "account manager", "marketing", "copywriting",
    "compliance", "product marketing",
]

# Locations we want to keep. Matched against the free-text location string
# each ATS returns. Deliberately does NOT allow bare "Remote" with no
# country qualifier — several companies post "Remote Poland"/"Remote
# Spain"/"Remote Australia" etc, and a naive "remote-but-not-US" pattern
# let all of those through (found 2026-08-11 in a real run: 64/320 rows
# were exactly this leak). Require an explicit Canada/Alberta signal.
LOCATION_ALLOW_PATTERNS = [
    r"\bcanada\b",
    r"\balberta\b",
    r",\s*ab\b",       # "Calgary, AB" / "Edmonton, AB"
    r"\bedmonton\b",
    r"\bcalgary\b",
    r"\bontario\b",
    r"\bvancouver\b",

]

# JD stack dealbreakers: if the description clearly requires one of these
# languages/frameworks and does NOT also mention anything from your own
# core stack, treat it as a mismatch. This only runs when a description is
# available — see ats_clients.py / aggregator_clients.py for which sources
# provide one. Word-boundary matched to avoid "java" matching "javascript".
STACK_DEALBREAKERS = [
    r"\bjava\b", r"\bc#\b", r"\.net\b", r"\bgolang\b",
    r"\bruby\b", r"\brails\b", r"\bphp\b", r"\bkotlin\b", r"\bswift\b",
    r"\bscala\b", r"\bc\+\+\b",
    # deliberately NOT including bare "go" — too ambiguous ("go-to-market",
    # "go live", etc.); Golang is covered by "golang" above, and most Go
    # job posts also say "Golang" or "Go (Golang)" somewhere in the JD.
]
STACK_CORE = [
    r"\bpython\b", r"\bfastapi\b", r"\bdjango\b", r"\bflask\b",
    r"\bjavascript\b", r"\btypescript\b", r"\breact\b", r"\bnode(?:\.?js)?\b",
]

_priority_re = re.compile(
    "(" + "|".join(re.escape(k) for k in PRIORITY_TITLE_KEYWORDS) + ")",
    re.IGNORECASE,
)
_title_allow_re = re.compile(
    "(" + "|".join(re.escape(k) for k in TITLE_ALLOW_KEYWORDS) + ")",
    re.IGNORECASE,
)
_exclusion_re = re.compile(
    "(" + "|".join(re.escape(k) for k in EXCLUSION_KEYWORDS) + ")",
    re.IGNORECASE,
)
_location_res = [re.compile(p, re.IGNORECASE) for p in LOCATION_ALLOW_PATTERNS]
_dealbreaker_res = [re.compile(p, re.IGNORECASE) for p in STACK_DEALBREAKERS]
_core_res = [re.compile(p, re.IGNORECASE) for p in STACK_CORE]

_html_tag_re = re.compile(r"<[^>]+>")


def strip_html(html_or_text: str) -> str:
    """Cheap HTML-to-text: good enough for keyword matching, not for display."""
    if not html_or_text:
        return ""
    return _html_tag_re.sub(" ", html_or_text)


# Shared by aggregator_clients.py (decide whether it's worth the extra
# request to fetch a full JD) and ai_evaluate.py (fall back to telling the
# model the description is partial, for the cases a full-JD fetch still
# couldn't recover — blocked site, dead link, genuinely short posting).
TRUNCATED_DESCRIPTION_MIN_CHARS = 400


def looks_truncated(description: str) -> bool:
    """True if `description` looks like a short aggregator teaser rather
    than a full job posting — either it visibly cuts off mid-sentence, or
    it's just too short to contain real requirements/responsibilities."""
    text = strip_html(description or "").strip()
    if not text:
        return False
    if text.endswith("…") or text.endswith("...") or text.rstrip().endswith(".."):
        return True
    return len(text) < TRUNCATED_DESCRIPTION_MIN_CHARS


def title_is_relevant(title: str) -> bool:
    title = title or ""
    if _priority_re.search(title):
        # Unambiguous engineering title — e.g. "Software Engineer, Automated
        # Marketing" — skip the exclusion list entirely so a word like
        # "marketing" elsewhere in the title can't veto it.
        return True
    if not _title_allow_re.search(title):
        return False
    if _exclusion_re.search(title):
        return False
    return True


def location_is_allowed(location: str) -> bool:
    loc = location or ""
    return any(p.search(loc) for p in _location_res)


def jd_stack_mismatch(description: str) -> bool:
    """True if the JD looks like a dealbreaker-language role with no
    mention of your own core stack. Only meaningful when `description` is
    non-empty — callers should treat an empty description as "unknown,
    don't reject on stack alone"."""
    text = strip_html(description)
    if not text:
        return False
    has_dealbreaker = any(p.search(text) for p in _dealbreaker_res)
    has_core = any(p.search(text) for p in _core_res)
    return has_dealbreaker and not has_core


def passes_filters(job: dict) -> bool:
    """job must have 'title' and 'location' keys (plain strings); may
    optionally have a 'description' key (HTML or plain text)."""
    if not title_is_relevant(job.get("title", "")):
        return False
    if not location_is_allowed(job.get("location", "")):
        return False
    if jd_stack_mismatch(job.get("description", "")):
        return False
    return True