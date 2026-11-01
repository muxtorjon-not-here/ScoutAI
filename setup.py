"""Guided first-time setup for ScoutAI.

Asks for the few things the bot needs, checks each one, writes .env and
default_profile.txt, and logs in to your Telegram account. Run it through
run.bat (Windows) or `python setup.py` inside the virtual environment.
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
ENV_PATH = BASE / ".env"
PROFILE_PATH = BASE / "default_profile.txt"
SESSION_PATH = BASE / "user_session"
ENV_KEYS = ("TG_API_ID", "TG_API_HASH", "TG_BOT_TOKEN", "ADMIN_USER_ID", "AI_API_KEY")


def say(text: str = "") -> None:
    print(text, flush=True)


def step(number: int, total: int, title: str) -> None:
    say()
    say("=" * 60)
    say(f" Step {number} of {total}: {title}")
    say("=" * 60)


def ask(prompt: str, check=None, default: str = "") -> str:
    """Prompt until the answer passes `check` (a function returning an error message or None)."""
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{prompt}{suffix}: ").strip() or default
        error = check(value) if check else None
        if not error:
            return value
        say(f"  -> {error} Try again.")


def yes(prompt: str, default: bool = True) -> bool:
    answer = input(f"{prompt} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not answer else answer.startswith("y")


def http_json(url: str, headers: dict | None = None, timeout: int = 15):
    request = urllib.request.Request(url, headers={"User-Agent": "ScoutAI-setup", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def read_env() -> dict:
    values = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip()
    return values


def env_is_complete(values: dict) -> bool:
    return all(values.get(k) and not values[k].startswith("your_") for k in ENV_KEYS)


# ---------- individual questions ----------

def ask_api_credentials() -> tuple[str, str]:
    say("ScoutAI reads Telegram channels as YOU, so it needs your own free Telegram API keys.")
    say("  1. Open https://my.telegram.org and log in with your phone number.")
    say("  2. Click 'API development tools' and create an app (any name, e.g. ScoutAI).")
    say("  3. Copy 'App api_id' (a number) and 'App api_hash' (32 letters/digits).")
    say()
    api_id = ask("api_id", lambda v: None if v.isdigit() else "It must be a number, e.g. 1234567.")
    api_hash = ask("api_hash", lambda v: None if re.fullmatch(r"[0-9a-fA-F]{32}", v) else "It should be 32 letters/digits.")
    return api_id, api_hash


def check_bot_token(token: str):
    if not re.fullmatch(r"\d+:[\w-]{30,}", token):
        return "That doesn't look like a bot token (it looks like 123456789:AAxxxx...)."
    try:
        info = http_json(f"https://api.telegram.org/bot{token}/getMe")
        check_bot_token.username = info["result"]["username"]
    except urllib.error.HTTPError:
        return "Telegram rejected this token."
    except Exception:
        say("  (Could not reach Telegram to verify the token; continuing anyway.)")
        check_bot_token.username = ""
    return None


def ask_bot_token() -> tuple[str, str]:
    say("The bot is the chat that sends you opportunity alerts.")
    say("  1. In Telegram, open @BotFather and send /newbot.")
    say("  2. Pick a name and a username ending in 'bot'.")
    say("  3. BotFather replies with a token. Paste it here.")
    say()
    token = ask("Bot token", check_bot_token)
    username = getattr(check_bot_token, "username", "")
    if username:
        say(f"  OK, your bot is @{username}")
    return token, username


def detect_user_id(token: str, bot_username: str) -> str | None:
    """Wait for the user to press Start on their bot and read their numeric ID from that message."""
    say(f"Open https://t.me/{bot_username} and press START (or send any message).")
    say("Waiting up to 2 minutes...")
    deadline = time.time() + 120
    offset = None
    while time.time() < deadline:
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=10" + (f"&offset={offset}" if offset else "")
        try:
            updates = http_json(url, timeout=20)["result"]
        except Exception:
            time.sleep(2)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            sender = (update.get("message") or {}).get("from")
            if sender and not sender.get("is_bot"):
                http_json(f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}")  # mark as read
                say(f"  Found you: {sender.get('first_name', '')} (ID {sender['id']})")
                return str(sender["id"])
    return None


def ask_user_id(token: str, bot_username: str) -> str:
    if bot_username:
        found = detect_user_id(token, bot_username)
        if found:
            return found
        say("  Didn't see a message. Let's enter your ID by hand instead.")
    say("Message @userinfobot in Telegram; it replies with your numeric 'Id'.")
    return ask("Your Telegram user ID", lambda v: None if v.isdigit() else "It must be a number.")


def check_ai_key(key: str):
    try:
        if key.startswith("gsk_"):
            http_json("https://api.groq.com/openai/v1/models", {"Authorization": f"Bearer {key}"})
        else:
            http_json("https://generativelanguage.googleapis.com/v1beta/models", {"x-goog-api-key": key})
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 403):
            return "The provider rejected this key."
    except Exception:
        say("  (Could not reach the AI provider to verify the key; continuing anyway.)")
    return None


def ask_ai_key() -> str:
    say("An AI model reads each post and scores how well it fits you. Both options below are free.")
    say("  Groq (recommended, fastest): https://console.groq.com/keys -> Create API Key (starts with gsk_)")
    say("  Gemini: https://aistudio.google.com/apikey -> Create API key")
    say()
    return ask("AI API key", lambda v: check_ai_key(v) if len(v) > 15 else "That looks too short.")


def ask_profile() -> None:
    if PROFILE_PATH.exists() and not yes("A profile already exists. Replace it?", default=False):
        return
    say("Tell the AI who you are, so it keeps only posts that fit. Short answers are fine,")
    say("and you can change all of this later from the dashboard (Channels & AI Profile tab).")
    say()
    city = ask("Where do you live (city, country)?")
    level = ask("Your level (e.g. 'high-school senior', '1st-year CS student')")
    want = ask("What do you want to find (e.g. scholarships, internships, hackathons)")
    ignore = ask("What should be ignored", default="ads, paid courses, recaps of past events, events in other cities")
    PROFILE_PATH.write_text(
        f"Location: {city} (strictly ignore offline events in other cities/regions unless travel is funded or the event is online/in {city}).\n"
        f"Academic Profile: {level}.\n"
        f"Target Opportunities: {want}.\n"
        f"Strictly Ignore: {ignore}.\n",
        encoding="utf-8",
    )
    say("  Profile saved.")


def ask_channels() -> str:
    say("Which PUBLIC Telegram channels should ScoutAI watch? Type their @usernames separated")
    say("by commas, or press Enter to skip (you can add channels later with /add in the bot).")
    raw = input("Channels: ").strip()
    names = [c.strip().lstrip("@").removeprefix("https://t.me/").lower() for c in raw.split(",") if c.strip()]
    return ",".join(names)


def login_to_telegram(api_id: int, api_hash: str) -> bool:
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        say("Telethon is not installed. Run run.bat (or `pip install -r requirements.txt`) first.")
        return False
    say("Now log in to Telegram. Telegram sends a login code to your Telegram app.")
    say("(Enter your phone number with country code, e.g. +998901234567.)")
    say()
    try:
        with TelegramClient(str(SESSION_PATH), api_id, api_hash) as client:
            me = client.get_me()
            say(f"  Logged in as {me.first_name}.")
        return True
    except Exception as exc:
        say(f"  Login failed: {exc!r}")
        return False


# ---------- main flow ----------

def main() -> int:
    say("Welcome to ScoutAI setup! It takes about 5 minutes. Nothing leaves your computer")
    say("except the calls to Telegram and your AI provider.")

    existing = read_env()
    if env_is_complete(existing):
        if yes("Existing settings found. Keep them?", default=True):
            values = existing
        else:
            values = {}
    else:
        values = {}

    if not values:
        step(1, 5, "Telegram API keys")
        api_id, api_hash = ask_api_credentials()
        step(2, 5, "Your bot")
        token, bot_username = ask_bot_token()
        step(3, 5, "Your Telegram user ID")
        user_id = ask_user_id(token, bot_username)
        step(4, 5, "AI key")
        ai_key = ask_ai_key()
        step(5, 5, "Channels to watch")
        channels = ask_channels()
        ENV_PATH.write_text(
            f"TG_API_ID={api_id}\nTG_API_HASH={api_hash}\nTG_BOT_TOKEN={token}\n"
            f"ADMIN_USER_ID={user_id}\nAI_API_KEY={ai_key}\nINITIAL_CHANNELS={channels}\n",
            encoding="utf-8",
        )
        say("  Settings saved to .env (this file is private; never share it).")
        values = read_env()

    say()
    say("About you")
    say("-" * 60)
    ask_profile()

    if not (SESSION_PATH.with_suffix(".session")).exists():
        say()
        say("Telegram login")
        say("-" * 60)
        if not login_to_telegram(int(values["TG_API_ID"]), values["TG_API_HASH"]):
            say("Setup is not finished. Run it again to retry the login.")
            return 1

    say()
    say("All set! Starting ScoutAI now. Open your bot in Telegram and send /help.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        say("\nSetup cancelled. Run it again whenever you're ready.")
        sys.exit(1)
