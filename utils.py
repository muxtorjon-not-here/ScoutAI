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


_DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y")
_NO_YEAR_FORMATS = ("%b %d", "%B %d", "%d %b", "%d %B")


def parse_deadline_date(text: str) -> str | None:
    """Best-effort conversion of free-text deadlines ('Nov 15, 2026', 'Dec 1, 23:59') to ISO."""
    s = text.lower()
    s = re.sub(r"\d{1,2}:\d{2}\s*(?:am|pm)?", " ", s)
    s = re.sub(r"\b(\d{1,2})(?:st|nd|rd|th)\b", r"\1", s)
    s = re.sub(r"\bsept\b", "sep", s)
    s = re.sub(r"[,\s]+", " ", s).strip(" .-")
    today = datetime.now().date()
    if s == "today":
        return today.isoformat()
    if s == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    for fmt in _NO_YEAR_FORMATS:
        try:
            parsed = datetime.strptime(f"{s} 2000", f"{fmt} %Y").date()
        except ValueError:
            continue
        candidate = parsed.replace(year=today.year)
        if candidate < today:
            candidate = candidate.replace(year=today.year + 1)
        return candidate.isoformat()
    return None
