"""Puts Planned and Working opportunities on Google Calendar.

For each one it creates an all-day bar from N days before the deadline up to the deadline, plus a
timed 09:00 event on the first notification day (default 10 days before) whose popup and email
reminders fire right away, so the phone buzzes. Moving an opportunity to Finished/Dismissed/Inbox,
changing its deadline, or deleting it updates or removes those events.

It talks to Google with a service account (no browser sign-in, no tokens that expire): the user
shares their calendar with the service account's e-mail address once. See README.md."""
import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

import database as db
from config import CALENDAR_REMIND_DAYS, GOOGLE_CALENDAR_ID, GOOGLE_CREDENTIALS_FILE, TIMEZONE

log = logging.getLogger("scoutai.calendar")

SCOPE = "https://www.googleapis.com/auth/calendar.events"
API = "https://www.googleapis.com/calendar/v3/calendars/{cal}/events"
TRACKED = ("PLANNED", "WORKING")
NOTIFY_AT = time(9, 0)

_lock = asyncio.Lock()
_credentials = None
_last_error = {"text": "", "at": 0.0}
on_error = None  # optional async callable(text) set by main.py to tell the user in Telegram


def enabled() -> bool:
    return bool(GOOGLE_CALENDAR_ID) and GOOGLE_CREDENTIALS_FILE.exists()


def setup_hint() -> str:
    if not GOOGLE_CALENDAR_ID:
        return "GOOGLE_CALENDAR_ID is not set in .env"
    if not GOOGLE_CREDENTIALS_FILE.exists():
        return f"the Google key file is missing: {GOOGLE_CREDENTIALS_FILE.name}"
    return ""


def _token() -> str:
    """Blocking: returns a valid access token, refreshing it when needed."""
    global _credentials
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    if _credentials is None:
        _credentials = service_account.Credentials.from_service_account_file(
            str(GOOGLE_CREDENTIALS_FILE), scopes=[SCOPE]
        )
    if not _credentials.valid:
        _credentials.refresh(Request())
    return _credentials.token


async def _request(client: httpx.AsyncClient, method: str, event_id: str = "", body=None) -> dict:
    token = await asyncio.to_thread(_token)
    url = API.format(cal=quote(GOOGLE_CALENDAR_ID, safe="")) + (f"/{event_id}" if event_id else "")
    resp = await client.request(method, url, headers={"Authorization": f"Bearer {token}"}, json=body)
    if method == "DELETE" and resp.status_code in (404, 410):
        return {}
    resp.raise_for_status()
    return resp.json() if resp.content else {}


def _now() -> datetime:
    return datetime.now(ZoneInfo(TIMEZONE))


def build_events(item: dict, today: date | None = None, now: datetime | None = None) -> list[dict]:
    """The Google Calendar events for one opportunity (empty if it has no usable future deadline)."""
    now = now or _now()
    today = today or now.date()
    try:
        deadline = date.fromisoformat(item.get("deadline_date") or "")
    except ValueError:
        return []
    if deadline < today:
        return []

    remind = sorted({deadline - timedelta(days=d) for d in CALENDAR_REMIND_DAYS})
    dropped = [d for d in remind if d < today]
    remind = [d for d in remind if d >= today]
    if dropped and today not in remind:
        remind.insert(0, today)  # the early reminder date has passed: notify now instead
    start = remind[0] if remind else today

    title = item["title"]
    lines = [f"Deadline: {deadline:%A, %d %B %Y}"]
    for label, key in (("What to do", "summary"), ("Why it matches", "why_it_matches")):
        if item.get(key):
            lines.append(f"{label}: {item[key]}")
    if item.get("post_link") and item["post_link"].startswith("http"):
        lines.append(f"Post: {item['post_link']}")
    lines.append(f"Status in ScoutAI: {item.get('status', '').title()}")
    description = "\n".join(lines)

    events = [{
        "summary": f"📌 {title} (deadline {deadline:%d %b})",
        "description": description,
        "start": {"date": start.isoformat()},
        "end": {"date": (deadline + timedelta(days=1)).isoformat()},
        "transparency": "transparent",
        "reminders": {"useDefault": False, "overrides": []},
        "source": {"title": "ScoutAI", "url": item["post_link"]} if str(item.get("post_link", "")).startswith("http") else None,
    }]
    zone = ZoneInfo(TIMEZONE)
    for day in remind:
        begin = datetime.combine(day, NOTIFY_AT, zone)
        if begin <= now:
            begin = now + timedelta(minutes=5)
        left = (deadline - day).days
        when = "is due today" if left == 0 else f"{left} day{'s' if left != 1 else ''} to deadline"
        events.append({
            "summary": f"🔔 {title}: {when}",
            "description": description,
            "start": {"dateTime": begin.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": (begin + timedelta(minutes=30)).isoformat(), "timeZone": TIMEZONE},
            "reminders": {"useDefault": False, "overrides": [
                {"method": "popup", "minutes": 0}, {"method": "email", "minutes": 0}]},
        })
    for event in events:
        if event.get("source") is None:
            event.pop("source", None)
    return events


def _signature(item: dict) -> str:
    return "|".join(str(item.get(k)) for k in ("deadline_date", "title", "status")) + f"|{CALENDAR_REMIND_DAYS}"


async def _report(text: str) -> None:
    log.error("Google Calendar: %s", text)
    now = asyncio.get_running_loop().time()
    if on_error and (text != _last_error["text"] or now - _last_error["at"] > 3600):
        _last_error.update(text=text, at=now)
        try:
            await on_error(f"📅 Google Calendar sync failed: {text}")
        except Exception:
            log.exception("could not report the calendar error")


async def sync(opp_id: int, deleted_item: dict | None = None) -> str:
    """Brings the calendar in line with one opportunity. Returns 'added', 'removed', 'unchanged',
    'no_deadline' or 'off'."""
    if not enabled():
        return "off"
    async with _lock:
        item = deleted_item or await db.get_opportunity_by_id(opp_id)
        if item is None:
            return "unchanged"
        try:
            state = json.loads(item.get("calendar_events") or "{}")
        except ValueError:
            state = {}
        old_ids = state.get("ids", [])
        events = [] if deleted_item or item.get("status") not in TRACKED else build_events(item)
        if not events and not old_ids:
            return "no_deadline" if item.get("status") in TRACKED and not deleted_item else "unchanged"
        if events and old_ids and state.get("sig") == _signature(item):
            return "unchanged"
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                for event_id in old_ids:
                    await _request(client, "DELETE", event_id)
                new_ids = [(await _request(client, "POST", body=event))["id"] for event in events]
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:300]
            hint = " Share the calendar with the service account (README)." if exc.response.status_code in (403, 404) else ""
            await _report(f"HTTP {exc.response.status_code}: {detail}{hint}")
            return "off"
        except Exception as exc:
            await _report(repr(exc))
            return "off"
        if not deleted_item:
            await db.set_calendar_events(
                opp_id, json.dumps({"sig": _signature(item), "ids": new_ids}) if new_ids else ""
            )
        return "added" if new_ids else "removed"


async def sync_all() -> int:
    """Syncs every Planned/Working opportunity (used at startup and by /calendar). Returns how many
    now have calendar events."""
    count = 0
    for status in TRACKED:
        for item in await db.get_all_opportunities(status):
            if await sync(item["id"]) == "added":
                count += 1
    return count
