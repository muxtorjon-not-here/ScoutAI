"""Detects the same opportunity posted by several sources with different wording.

Three signals, cheapest first: a shared external link (registration/application URL), a very
similar title, or a similar title plus the same deadline."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_SKIP_HOSTS = ("t.me", "telegram.me", "telegram.org", "instagram.com", "facebook.com", "fb.me",
               "twitter.com", "x.com", "youtube.com", "youtu.be", "linkedin.com", "tiktok.com",
               "wa.me", "whatsapp.com", "google.com", "wp.com", "wordpress.com", "wordpress.org",
               "gravatar.com", "w.org", "feedburner.com", "apple.com", "pinterest.com", "reddit.com")
_TRACKING = re.compile(r"^(utm_|fbclid|gclid|igshid|ref|source|mc_)", re.I)
_STOPWORDS = frozenset(
    "the and for with from your you our are will apply applications application now open opens "
    "call calls year years program programme programs fully funded funding fund scholarship "
    "scholarships opportunity opportunities announcement announcing announces new in of to a an "
    "competition challenge conference event session week season registration at on by is be as 2024 2025 2026 2027 2028 online free global international".split()
)
_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def normalize_url(url: str) -> str | None:
    """Lower-cased host + path without tracking parameters, or None for social/Telegram links."""
    try:
        parts = urlsplit(url.strip().rstrip(".,;:!?"))
    except ValueError:
        return None
    host = parts.netloc.lower().removeprefix("www.")
    if not host or any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS):
        return None
    path = parts.path.rstrip("/")
    if len(path) < 10:  # homepages and generic pages like /apply say nothing about one opportunity
        return None
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not _TRACKING.match(k)))
    return f"{host}{path}" + (f"?{query}" if query else "")


def extract_urls(text: str) -> list[str]:
    seen: list[str] = []
    for raw in _URL_RE.findall(text or ""):
        norm = normalize_url(raw)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def title_tokens(title: str) -> frozenset[str]:
    return frozenset(w for w in _WORD_RE.findall((title or "").lower()) if w not in _STOPWORDS)


def title_similarity(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    jaccard = inter / len(a | b)
    overlap = inter / min(len(a), len(b)) if min(len(a), len(b)) >= 3 else 0.0
    return max(jaccard, overlap * 0.85)


def find_duplicate(
    title: str, deadline_iso: str | None, urls: list[str], existing: list[dict]
) -> dict | None:
    """Returns the stored opportunity this one repeats, or None. `existing` rows need the keys
    id, title, deadline_date and source_urls (space separated)."""
    tokens = title_tokens(title)
    new_urls = set(urls)
    for row in existing:
        if new_urls and new_urls & set((row.get("source_urls") or "").split()):
            return row
        sim = title_similarity(tokens, title_tokens(row.get("title") or ""))
        if sim >= 0.7:
            return row
        if sim >= 0.45 and deadline_iso and deadline_iso == row.get("deadline_date"):
            return row
    return None
