"""Pure helpers shared by the Telegram bot, the database layer and the web API."""
import re
from datetime import date, datetime, timedelta

USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")


def normalize_channel(raw: str) -> str | None:
    s = raw.strip()
    s = re.sub(r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?", "", s, flags=re.I)
    s = s.lstrip("@").split("/")[0].split("?")[0]
    return s.lower() if USERNAME_RE.match(s) else None


def parse_channels(arg: str) -> tuple[list[str], list[str]]:
    """Splits on spaces, commas, semicolons and newlines. Returns (unique valid, invalid tokens)."""
    valid: list[str] = []
    invalid: list[str] = []
    for token in re.split(r"[\s,;]+", arg.strip()):
        if not token:
            continue
        name = normalize_channel(token)
        if name is None:
            invalid.append(token)
        elif name not in valid:
            valid.append(name)
    return valid, invalid


def days_until(iso: str | None) -> int | None:
    """Whole days from today to an ISO date; None when there is no usable date."""
    if not iso:
        return None
    try:
        return (date.fromisoformat(iso) - datetime.now().date()).days
    except ValueError:
        return None


_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
_NUMERIC_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_DOTTED_RE = re.compile(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b")
_DAY_MONTH_RE = re.compile(rf"\b(\d{{1,2}})\s*{_MONTH}\b(?:\s+(\d{{4}}))?")
_MONTH_DAY_RE = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})\b(?!\s*:)(?:\s+(\d{{4}}))?")
_MONTH_YEAR_RE = re.compile(rf"\b{_MONTH}\s+(\d{{4}})\b")


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _roll_forward(month: int, day: int, today: date) -> date | None:
    candidate = _safe_date(today.year, month, day)
    if candidate and candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def parse_deadline_date(text: str) -> str | None:
    """Best-effort conversion of free-text deadlines to ISO. Finds the date anywhere in the text:
    '15 November 2026', '30.10.2026', '2026-10-26 08:59 GMT+5', 'Nov 15, 2026', '14-15 Nov 2026',
    'Dec 1, 23:59', 'October 2026' (month only -> last day of that month)."""
    s = text.lower()
    s = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\bsept\b", "sep", s)
    today = datetime.now().date()
    if re.fullmatch(r"\W*today\W*", s):
        return today.isoformat()
    if re.fullmatch(r"\W*tomorrow\W*", s):
        return (today + timedelta(days=1)).isoformat()

    if m := _NUMERIC_RE.search(s):
        found = _safe_date(int(m[1]), int(m[2]), int(m[3]))
        if found:
            return found.isoformat()
    if m := _DOTTED_RE.search(s):
        found = _safe_date(int(m[3]), int(m[2]), int(m[1]))
        if found:
            return found.isoformat()
    if m := _DAY_MONTH_RE.search(s):
        month, day, year = _MONTHS[m[2]], int(m[1]), m[3]
        found = _safe_date(int(year), month, day) if year else _roll_forward(month, day, today)
        if found:
            return found.isoformat()
    if m := _MONTH_DAY_RE.search(s):
        month, day, year = _MONTHS[m[1]], int(m[2]), m[3]
        found = _safe_date(int(year), month, day) if year else _roll_forward(month, day, today)
        if found:
            return found.isoformat()
    if m := _MONTH_YEAR_RE.search(s):
        month, year = _MONTHS[m[1]], int(m[2])
        first_of_next = date(year + (month == 12), month % 12 + 1, 1)
        return (first_of_next - timedelta(days=1)).isoformat()
    if re.search(r"\btoday\b", s):
        return today.isoformat()
    if re.search(r"\btomorrow\b", s):
        return (today + timedelta(days=1)).isoformat()
    return None
