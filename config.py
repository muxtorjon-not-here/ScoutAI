import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "scout.db"
# default_profile.txt holds personal details and is not committed; the example is the fallback
DEFAULT_PROFILE_PATH = (
    BASE_DIR / "default_profile.txt"
    if (BASE_DIR / "default_profile.txt").exists()
    else BASE_DIR / "default_profile.example.txt"
)
USER_SESSION = str(BASE_DIR / "user_session")
BOT_SESSION = str(BASE_DIR / "bot_session")


def _get(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or value.startswith("your_"):
        return ""
    return value


def _load():
    values = {name: _get(name) for name in (
        "TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN", "ADMIN_USER_ID", "AI_API_KEY",
    )}
    missing = [name for name, value in values.items() if not value]
    if missing:
        sys.exit(
            f"Missing or placeholder values in .env: {', '.join(missing)}\n"
            "Run setup.py (or just run.bat) to fill them in step by step."
        )
    for name in ("TG_API_ID", "ADMIN_USER_ID"):
        if not values[name].lstrip("-").isdigit():
            sys.exit(f"{name} in .env must be a number")
    return values


_v = _load()

API_ID = int(_v["TG_API_ID"])
API_HASH = _v["TG_API_HASH"]
BOT_TOKEN = _v["TG_BOT_TOKEN"]
ADMIN_USER_ID = int(_v["ADMIN_USER_ID"])
AI_API_KEY = _v["AI_API_KEY"]

# Optional: with a password the dashboard also opens to other devices on your network (your phone);
# without one it only listens on this computer.
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "").strip()
WEB_HOST = os.getenv("WEB_HOST", "").strip() or ("0.0.0.0" if WEB_PASSWORD else "127.0.0.1")

# Optional Google Calendar sync for Planned/Working opportunities (see README)
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "").strip()
GOOGLE_CREDENTIALS_FILE = Path(
    os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip() or BASE_DIR / "google_credentials.json"
)
TIMEZONE = os.getenv("TIMEZONE", "").strip() or "Asia/Tashkent"
# Days before the deadline on which you get a calendar notification, e.g. "10" or "10,3,1"
CALENDAR_REMIND_DAYS = sorted(
    {int(d) for d in os.getenv("CALENDAR_REMIND_DAYS", "10").replace(" ", "").split(",") if d.isdigit()},
    reverse=True,
) or [10]

INITIAL_CHANNELS = [
    c.strip().lstrip("@").lower()
    for c in os.getenv("INITIAL_CHANNELS", "").split(",")
    if c.strip()
]
