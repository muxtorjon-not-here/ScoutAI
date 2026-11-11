"""Opportunity websites as a second kind of source next to Telegram channels.

Each website is read through its public RSS feed (or a small public JSON API). New listings go
through the same pipeline as Telegram posts: a cheap batch triage by title, then the full AI
evaluation, the duplicate checks and the alerts. Every feed below was fetched successfully while
this list was compiled; sites that block bots or had no feed were left out.

Per-site opportunity feeds have different editorial focus (many lean towards Africa or the US), so
the triage step drops listings that clearly don't fit the user's profile."""
import asyncio
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import zip_longest
from time import mktime
from urllib.parse import urlsplit

import feedparser
import httpx
from bs4 import BeautifulSoup

import database as db
from dedupe import extract_urls, find_duplicate

log = logging.getLogger("scoutai.web")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 ScoutAI/1.0"
)
FETCH_TIMEOUT = 30
MAX_FEED_ITEMS = 15        # newest entries taken from each feed
MAX_AGE_DAYS = 45          # older RSS entries are ignored
MAX_NEW_PER_CYCLE = 80     # new listings considered per scan; the rest wait for the next one
TRIAGE_CHUNK = 25
TRIAGE_PAUSE = 8
MAX_EVAL_PER_CYCLE = 40    # full AI evaluations per scan (free AI tiers have daily limits)
MAX_TEXT = 3500

WP = "https://{d}/feed"
CAT = "https://{d}/category/{c}/feed"


def _wp(domain: str, *categories: str) -> list[str]:
    return [WP.format(d=domain)] + [CAT.format(d=domain, c=c) for c in categories]


# id is the site's domain; it becomes the source key "web:<id>"
CATALOGUE: list[dict] = [
    {"id": "opportunitydesk.org", "name": "Opportunity Desk", "kind": "rss",
     "urls": _wp("opportunitydesk.org", "fellowships", "internships", "undergraduate")},
    {"id": "opportunitiesforafricans.com", "name": "Opportunities For Africans", "kind": "rss",
     "urls": _wp("opportunitiesforafricans.com", "scholarships", "fellowships", "internships")},
    {"id": "scholars4dev.com", "name": "Scholarships for Development", "kind": "rss",
     "urls": _wp("scholars4dev.com")},
    {"id": "opportunitiesfeed.com", "name": "Opportunity Feed", "kind": "rss",
     "urls": _wp("opportunitiesfeed.com", "scholarships", "internships")},
    {"id": "opportunitiescircle.com", "name": "Opportunities Circle", "kind": "rss",
     "urls": _wp("opportunitiescircle.com", "scholarships", "internships")},
    {"id": "opportunitiescorners.info", "name": "Opportunities Corners", "kind": "rss",
     "urls": _wp("opportunitiescorners.info")},
    {"id": "oyaop.com", "name": "OYA Opportunities", "kind": "rss", "urls": _wp("oyaop.com")},
    {"id": "scholarshipscorner.website", "name": "Scholarships Corner", "kind": "rss",
     "urls": _wp("scholarshipscorner.website", "fellowships", "internships", "summer-programs", "competitions")},
    {"id": "scholarshipsfuture.com", "name": "Scholarships Future", "kind": "rss",
     "urls": _wp("scholarshipsfuture.com", "fellowships", "internships")},
    {"id": "scholarshipregion.com", "name": "Scholarship Region", "kind": "rss",
     "urls": _wp("scholarshipregion.com")},
    {"id": "scholarshiproar.com", "name": "Scholarship Roar", "kind": "rss",
     "urls": _wp("scholarshiproar.com")},
    {"id": "profellow.com", "name": "ProFellow (fellowships)", "kind": "rss",
     "urls": ["https://www.profellow.com/feed"]},
    {"id": "devpost.com", "name": "Devpost hackathons", "kind": "devpost",
     "urls": ["https://devpost.com/api/hackathons"]},
    {"id": "ctftime.org", "name": "CTFtime (CTF competitions)", "kind": "rss",
     "urls": ["https://ctftime.org/event/list/upcoming/rss/"]},
    {"id": "codeforces.com", "name": "Codeforces contests", "kind": "codeforces",
     "urls": ["https://codeforces.com/api/contest.list?gym=false"]},
]


@dataclass
class Listing:
    source_id: str
    title: str
    link: str
    text: str
    published: datetime | None = None


def _clean(html_or_text: str) -> str:
    text = BeautifulSoup(html_or_text or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)


def _strip_own_links(text: str, host: str) -> str:
    """Removes links back to the site itself (categories, share buttons): they say nothing about
    the opportunity and would make unrelated posts look like duplicates."""
    return re.sub(rf"https?://(?:[\w-]+\.)*{re.escape(host)}\S*", " ", text)


def _entry_time(entry) -> datetime | None:
    stamp = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(mktime(stamp), timezone.utc) if stamp else None


def _from_feed(source_id: str, content: bytes) -> list[Listing]:
    parsed = feedparser.parse(content)
    out = []
    for entry in parsed.entries[:MAX_FEED_ITEMS]:
        title, link = _clean(entry.get("title", "")), entry.get("link", "")
        if not title or not link.startswith("http"):
            continue
        body = ""
        if entry.get("content"):
            body = entry["content"][0].get("value", "")
        body = _clean(body or entry.get("summary", ""))
        out.append(Listing(source_id, title, link, body, _entry_time(entry)))
    return out


async def _get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    resp = await client.get(url, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                            timeout=FETCH_TIMEOUT, **kwargs)
    resp.raise_for_status()
    return resp


async def _fetch_devpost(client: httpx.AsyncClient, source_id: str, url: str) -> list[Listing]:
    out = []
    for page in (1, 2, 3):
        resp = await _get(client, url, params=[
            ("status[]", "open"), ("status[]", "upcoming"), ("order_by", "deadline"),
            ("per_page", "20"), ("page", str(page))])
        items = resp.json().get("hackathons", [])
        for h in items:
            themes = ", ".join(t["name"] for t in h.get("themes", []))
            prize = _clean(h.get("prize_amount") or "")
            out.append(Listing(source_id, h["title"], h["url"], (
                f"{h['title']}. Hackathon on Devpost, {h.get('displayed_location', {}).get('location', '')}. "
                f"Submission period: {h.get('submission_period_dates', '')} ({h.get('time_left_to_submission', '')}). "
                f"Themes: {themes}. Prize: {prize or 'n/a'}. Organizer: {h.get('organization_name', '')}.")))
        if len(items) < 20:
            break
    return out


async def _fetch_codeforces(client: httpx.AsyncClient, source_id: str, url: str) -> list[Listing]:
    contests = (await _get(client, url)).json().get("result", [])
    out = []
    for c in contests:
        if c.get("phase") != "BEFORE":
            continue
        start = datetime.fromtimestamp(c["startTimeSeconds"], timezone.utc)
        hours = c.get("durationSeconds", 0) / 3600
        out.append(Listing(source_id, c["name"], f"https://codeforces.com/contest/{c['id']}", (
            f"{c['name']}. Online programming contest on Codeforces ({c.get('type', '')} rules), "
            f"starts {start:%Y-%m-%d %H:%M} UTC, lasts {hours:g} hours. Free to join.")))
    return out


async def fetch_source(client: httpx.AsyncClient, src: dict) -> list[Listing]:
    out: list[Listing] = []
    for url in src["urls"]:
        try:
            if src["kind"] == "devpost":
                out += await _fetch_devpost(client, src["id"], url)
            elif src["kind"] == "codeforces":
                out += await _fetch_codeforces(client, src["id"], url)
            else:
                resp = await _get(client, url)
                out += await asyncio.to_thread(_from_feed, src["id"], resp.content)
        except Exception as exc:
            log.warning("web source %s (%s) failed: %r", src["id"], url, exc)
    seen: set[str] = set()
    return [l for l in out if not (l.link in seen or seen.add(l.link))]


def source_id_for(url: str) -> str:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return host or url


async def validate_feed(url: str) -> str:
    """For user-added feeds: returns the feed's title, or raises ValueError if it is not a feed."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await _get(client, url)
        except Exception as exc:
            raise ValueError(f"could not open it ({exc.__class__.__name__})") from exc
    parsed = feedparser.parse(resp.content)
    if not parsed.entries:
        raise ValueError("it has no RSS/Atom entries")
    return _clean(parsed.feed.get("title", "")) or source_id_for(url)


async def scan_web(process, triage, profile: str) -> dict:
    """One pass over every enabled website.

    process(source_key, text, link, own_url) -> bool   full evaluation, False if the AI is down
    triage(profile, [(title, snippet), ...]) -> set[int] | None   indexes worth evaluating
    Returns counters for logging."""
    stats = {"fetched": 0, "new": 0, "triaged_out": 0, "evaluated": 0}
    sources = await db.get_web_sources(enabled_only=True)
    if not sources:
        return stats

    async with httpx.AsyncClient() as client:
        sem = asyncio.Semaphore(5)

        async def one(src):
            async with sem:
                return await fetch_source(client, src)

        batches = await asyncio.gather(*(one(s) for s in sources))
    listings = [l for batch in batches for l in batch]
    stats["fetched"] = len(listings)

    unseen = await db.unseen_web_links([l.link for l in listings])
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    fresh, skip = [], []
    for l in listings:
        if l.link not in unseen:
            continue
        (skip if l.published and l.published < cutoff else fresh).append(l)
    await db.mark_web_seen([l.link for l in skip])

    # drop repeats of stored opportunities and of each other before spending any AI calls
    existing = list(await db.get_dedupe_candidates())
    candidates, repeats = [], []
    # newest first within each site, then take turns between sites so one big feed can't crowd out the rest
    per_site: dict[str, list[Listing]] = defaultdict(list)
    for l in sorted(fresh, key=lambda x: x.published or datetime.now(timezone.utc), reverse=True):
        per_site[l.source_id].append(l)
    interleaved = [l for round_ in zip_longest(*per_site.values()) for l in round_ if l]
    for l in interleaved:
        text = _strip_own_links(f"{l.title}. {l.text}", source_id_for(l.link))[:MAX_TEXT]
        urls = extract_urls(l.link)
        if find_duplicate(l.title, None, urls, existing):
            repeats.append(l)
            continue
        existing.append({"id": 0, "title": l.title, "deadline_date": None, "source_urls": " ".join(urls)})
        l.text = text
        candidates.append(l)
    await db.mark_web_seen([l.link for l in repeats])
    stats["new"] = len(candidates)
    candidates = candidates[:MAX_NEW_PER_CYCLE]
    if not candidates:
        return stats

    # cheap batch triage by title, then full evaluation of the survivors
    kept: list[Listing] = []
    rejected: list[str] = []
    for i in range(0, len(candidates), TRIAGE_CHUNK):
        chunk = candidates[i:i + TRIAGE_CHUNK]
        keep = await triage(profile, [(l.title, l.text) for l in chunk])
        if keep is None:
            log.warning("AI unavailable during triage; the rest waits for the next scan")
            break
        kept += [l for j, l in enumerate(chunk) if j in keep]
        rejected += [l.link for j, l in enumerate(chunk) if j not in keep]
        await asyncio.sleep(TRIAGE_PAUSE)  # stay under the AI provider's tokens-per-minute limit
    await db.mark_web_seen(rejected)
    stats["triaged_out"] = len(rejected)

    for l in kept[:MAX_EVAL_PER_CYCLE]:
        if not await process(f"web:{l.source_id}", l.text, l.link, l.link):
            log.warning("AI unavailable during evaluation; stopping this scan")
            break
        await db.mark_web_seen([l.link])
        stats["evaluated"] += 1
    return stats
