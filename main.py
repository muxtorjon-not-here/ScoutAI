import asyncio
import html
import logging
import re
import socket
import sys
from datetime import date, datetime, timedelta, timezone

from telethon import Button, TelegramClient, events
from telethon.errors import MessageNotModifiedError
from telethon.tl.functions.channels import JoinChannelRequest

import uvicorn

import database as db
import web_api
from ai_evaluator import evaluate_message, passes_prefilter
from utils import days_until, parse_channels, parse_deadline_date
from config import (ADMIN_USER_ID, API_HASH, API_ID, BASE_DIR, BOT_SESSION,
                    BOT_TOKEN, USER_SESSION)

for _stream in (sys.stdout, sys.stderr):  # emoji in the banner/logs must not crash cp1252 consoles
    if _stream is not None and hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

_handlers = [logging.FileHandler(BASE_DIR / "scout.log", encoding="utf-8")]
if sys.stderr is not None:  # pythonw (hidden mode) has no console
    _handlers.append(logging.StreamHandler())
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=_handlers,
)
log = logging.getLogger("scoutai")

user_client = TelegramClient(USER_SESSION, API_ID, API_HASH)
bot_client = TelegramClient(BOT_SESSION, API_ID, API_HASH)
bot_client.parse_mode = None  # formatting is requested explicitly per message

process_lock = asyncio.Lock()  # serialises AI calls so duplicate detection stays accurate

WEB_HOST = "127.0.0.1"
WEB_PORT = 8000
AI_DELAY_SECONDS = 3.0
DIGEST_MIN_SCORE = 5
IMMEDIATE_MIN_SCORE = 8
URGENT_DEADLINE_DAYS = 1  # deadline today or tomorrow is sent immediately
UNDATED_LIST_DAYS = 14  # undated posts stay in the Active view this long
CATCHUP_LIMIT = 15
FORCED_CATCHUP_LIMIT = 10
NEW_CHANNEL_SCAN_LIMIT = 5
DIGEST_INTERVAL = timedelta(hours=24)
TG_MESSAGE_LIMIT = 3900
BOARD_TEXT_LIMIT = 3600
BOARD_MAX_ITEMS = 20
BUTTONS_PER_ROW = 5

# Seconds before bot chatter is deleted (cards, digests and boards are never auto-deleted)
COMMAND_TTL = 5
REPLY_TTL = 8
INFO_TTL = 45
ERROR_TTL = 15
PROMPT_TTL = 300

STATUS_LABELS = {
    "UNTRACKED": "⚪ Untracked",
    "PLANNED": "📌 Planned",
    "WORKING": "🛠️ Working on",
    "FINISHED": "✅ Finished",
    "DISMISSED": "🚫 Dismissed",
}
STATUS_EMOJI = {k: v.split()[0] for k, v in STATUS_LABELS.items()}
STATUS_TOASTS = {
    "UNTRACKED": "Status cleared ⚪",
    "PLANNED": "Moved to Planned 📌",
    "WORKING": "Moved to Working On 🛠️",
    "FINISHED": "Marked as Done ✅",
    "DISMISSED": "Dismissed 🚫",
}
VIEW_TITLES = {
    "ACTIVE": "📋 Active",
    "PLANNED": "📌 Planned",
    "WORKING": "🛠️ Working on",
    "FINISHED": "✅ Done",
}
VIEW_COMMANDS = {
    "board": "ACTIVE", "tracked": "ACTIVE", "list": "ACTIVE",
    "planned": "PLANNED", "working": "WORKING", "done": "FINISHED",
}

# admin id -> (opportunity id, id of the card message to refresh)
pending_deadline_edits: dict[int, tuple[int, int]] = {}
pending_manual_add: dict[int, int] = {}  # admin id -> prompt message id
pending_prompt_ids: dict[int, int] = {}  # admin id -> prompt message id (deadline edits)
_last_board_id: int | None = None

HELP_TEXT = (
    "ScoutAI commands:\n"
    "/board (or /tracked, /list) - dashboard of active opportunities\n"
    "/planned, /working, /done - opportunities by status\n"
    "/add_opp <Title> | <Deadline> - track an opportunity you found elsewhere\n"
    "/digest - send saved digest opportunities (score 5-7)\n"
    "/add ch1 @ch2 https://t.me/ch3 - monitor channels (spaces, commas or lines)\n"
    "/remove ch1 @ch2 - stop monitoring channels\n"
    "/channels - list monitored channels\n"
    "/profile - show your filtering profile\n"
    "/set_profile <new text> - replace your filtering profile\n"
    "/catchup - rescan the last 10 posts of every channel\n\n"
    "Every opportunity card has buttons: set its status, edit its deadline, open the post.\n"
    "Command messages and short replies delete themselves after a few seconds."
)


# ---------- helpers ----------

def esc(value) -> str:
    return html.escape(str(value or ""), quote=False)


def short(text: str, limit: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def deadline_badge(iso: str | None) -> str:
    n = days_until(iso)
    if n is None:
        return "⚪ No exact date"
    when = date.fromisoformat(iso).strftime("%b %d")
    if n < 0:
        label = "⚫ Expired"
    elif n == 0:
        label = "🔴 TODAY"
    elif n == 1:
        label = "🟠 Tomorrow"
    elif n <= 3:
        label = f"🟠 {n} days left"
    elif n <= 7:
        label = f"🟡 {n} days left"
    else:
        label = f"🟢 {n} days left"
    return f"{label} ({when})"


def is_web_link(link: str | None) -> bool:
    return bool(link) and link.startswith("http")


# ---------- cards ----------

def render_card(item: dict, header: str | None = None) -> str:
    status = item.get("status") or "UNTRACKED"
    manual = item["channel_source"] == "manual"
    deadline_line = f"📅 <b>Deadline:</b> {esc(item['deadline'])}"
    if days_until(item.get("deadline_date")) is not None:
        deadline_line += f" · {deadline_badge(item['deadline_date'])}"
    lines = [header] if header else []
    lines.append(f"<b>#{item['id']} · {esc(item['title'])}</b>")
    lines.append(
        "📂 Added manually" if manual
        else f"⭐ {item['score']}/10 · 📂 {esc(item['category'])} · 📣 @{esc(item['channel_source'])}"
    )
    lines.append(deadline_line)
    lines.append(f"📊 <b>Status:</b> {STATUS_LABELS.get(status, status)}")
    if item.get("why_it_matches"):
        lines.append(f"🎯 {esc(item['why_it_matches'])}")
    if item.get("summary"):
        lines.append(f"✅ {esc(item['summary'])}")
    return "\n".join(lines)


def card_buttons(item: dict) -> list[list]:
    oid, status = item["id"], item.get("status") or "UNTRACKED"

    def toggle(label: str, target: str):
        return Button.inline(("✔️ " if status == target else "") + label, f"st:{oid}:{target}")

    row2 = [Button.inline("🗓️ Set/Edit Deadline", f"dl:{oid}")]
    if is_web_link(item.get("post_link")):
        row2.append(Button.url("🔗 Post", item["post_link"]))
    row2.append(
        Button.inline("↩️ Restore", f"st:{oid}:UNTRACKED") if status == "DISMISSED"
        else Button.inline("🚫 Dismiss", f"st:{oid}:DISMISSED")
    )
    return [
        [toggle("📌 Plan", "PLANNED"), toggle("🛠️ Working", "WORKING"), toggle("✅ Done", "FINISHED")],
        row2,
    ]


def alert_header(item: dict) -> str:
    n = days_until(item.get("deadline_date"))
    return "⏰ <b>DEADLINE SOON</b>" if n is not None and n <= URGENT_DEADLINE_DAYS else "🚨 <b>New opportunity</b>"


async def send_card(item: dict, header: str | None = None):
    return await bot_client.send_message(
        ADMIN_USER_ID, render_card(item, header), buttons=card_buttons(item),
        parse_mode="html", link_preview=False,
    )


async def refresh_card(opp_id: int, message_id: int) -> None:
    item = await db.get_opportunity_by_id(opp_id)
    if not item:
        return
    try:
        await bot_client.edit_message(
            ADMIN_USER_ID, message_id, render_card(item), buttons=card_buttons(item),
            parse_mode="html", link_preview=False,
        )
    except MessageNotModifiedError:
        pass
    except Exception as exc:  # card may have been deleted by the user
        log.debug("could not refresh card %s: %r", message_id, exc)


# ---------- lists / board ----------

def open_buttons(ids: list[int]) -> list[list]:
    buttons = [Button.inline(f"#{i}", f"open:{i}") for i in ids]
    return [buttons[i:i + BUTTONS_PER_ROW] for i in range(0, len(buttons), BUTTONS_PER_ROW)]


def list_line(item: dict, with_status: bool) -> str:
    prefix = f"{STATUS_EMOJI.get(item.get('status'), '⚪')} " if with_status else ""
    deadline = esc(short(item["deadline"]))
    if days_until(item.get("deadline_date")) is not None:
        deadline += f" · {deadline_badge(item['deadline_date'])}"
    link = f" · 🔗 <a href=\"{html.escape(item['post_link'])}\">Post</a>" if is_web_link(item.get("post_link")) else ""
    return f"{prefix}<b>#{item['id']}</b> · {esc(short(item['title'], 70))}\n   📅 {deadline}{link}"


async def render_board(view: str) -> tuple[str, list[list]]:
    today = datetime.now().date().isoformat()
    since = (datetime.now(timezone.utc) - timedelta(days=UNDATED_LIST_DAYS)).isoformat()
    active = await db.get_active_opportunities(today, since)
    counts = await db.count_by_status()
    if view == "ACTIVE":
        items = active
    else:
        items = await db.get_opportunities_by_status(view)

    shown, text = [], f"<b>{VIEW_TITLES[view]}</b> ({len(items)})\n\n"
    for item in items[:BOARD_MAX_ITEMS]:
        line = list_line(item, with_status=(view == "ACTIVE")) + "\n\n"
        if len(text) + len(line) > BOARD_TEXT_LIMIT:
            break
        text += line
        shown.append(item["id"])
    if not items:
        text += "Nothing here yet."
    elif len(shown) < len(items):
        text += f"…and {len(items) - len(shown)} more."

    def tab(label: str, target: str, n: int):
        return Button.inline(("● " if view == target else "") + f"{label} ({n})", f"list:{target}")

    buttons = [[
        tab("📌 Planned", "PLANNED", counts.get("PLANNED", 0)),
        tab("🛠️ Working", "WORKING", counts.get("WORKING", 0)),
        tab("✅ Done", "FINISHED", counts.get("FINISHED", 0)),
    ]]
    buttons += open_buttons(shown)
    buttons.append([tab("📋 Active", "ACTIVE", len(active)), Button.inline("➕ Add Manual Opportunity", "addopp")])
    return text.rstrip(), buttons


async def send_board(view: str) -> None:
    global _last_board_id
    if _last_board_id:  # keep a single live board instead of stacking copies
        _spawn(_delete_after([_last_board_id], 0))
    text, buttons = await render_board(view)
    msg = await bot_client.send_message(
        ADMIN_USER_ID, text, buttons=buttons, parse_mode="html", link_preview=False
    )
    _last_board_id = msg.id


# ---------- messaging ----------

async def send_html(text: str, buttons=None):
    return await bot_client.send_message(
        ADMIN_USER_ID, text, parse_mode="html", link_preview=False, buttons=buttons
    )


_background: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _delete_after(message_ids: list[int], ttl: float) -> None:
    await asyncio.sleep(ttl)
    try:
        await bot_client.delete_messages(ADMIN_USER_ID, message_ids)
    except Exception as exc:
        log.debug("could not delete %s: %r", message_ids, exc)


async def say(text: str, ttl: float = REPLY_TTL, as_html: bool = False, buttons=None):
    """Sends a short-lived message that deletes itself after `ttl` seconds."""
    msg = await bot_client.send_message(
        ADMIN_USER_ID, text, parse_mode="html" if as_html else None,
        link_preview=False, buttons=buttons,
    )
    _spawn(_delete_after([msg.id], ttl))
    return msg


# ---------- digest ----------

def format_digest_item(item: dict) -> str:
    link = f"\n  🔗 <a href=\"{html.escape(item['post_link'])}\">Open post</a>" if is_web_link(item["post_link"]) else ""
    return (
        f"• <b>#{item['id']}</b> {deadline_badge(item.get('deadline_date'))}\n"
        f"  <b>{esc(item['title'])}</b> ({item['score']}/10 · {esc(item['category'])})\n"
        f"  📅 {esc(item['deadline'])}\n"
        f"  🎯 {esc(item['why_it_matches'])}{link}"
    )


async def send_digest() -> int:
    items = await db.get_unsent_digest()
    await db.set_meta("last_digest_at", datetime.now(timezone.utc).isoformat())
    expired = [i["id"] for i in items if (days_until(i["deadline_date"]) or 0) < 0]
    await db.mark_digest_sent(expired)  # deadline passed while waiting: no use any more
    live = [i for i in items if i["id"] not in expired]
    if not live:
        return 0
    live.sort(key=lambda i: (
        days_until(i["deadline_date"]) if days_until(i["deadline_date"]) is not None else 999,
        -i["score"],
    ))

    header = f"📰 <b>ScoutAI Digest</b> ({len(live)} saved)\n\n"
    chunks: list[tuple[str, list[int]]] = []
    current, ids = header, []
    for item in live:
        block = format_digest_item(item) + "\n\n"
        if len(current) + len(block) > TG_MESSAGE_LIMIT:
            chunks.append((current, ids))
            current, ids = "", []
        current += block
        ids.append(item["id"])
    chunks.append((current, ids))
    for text, chunk_ids in chunks:
        await send_html(text.rstrip(), buttons=open_buttons(chunk_ids))
    await db.mark_digest_sent([i["id"] for i in live])
    return len(live)


# ---------- evaluation pipeline ----------

async def process_message(channel: str, msg) -> bool:
    """Evaluates one post. Returns False only when the AI provider failed (retry later)."""
    text = msg.message or ""
    if not passes_prefilter(text):
        return True

    link = f"https://t.me/{channel}/{msg.id}"
    if await db.opportunity_exists(link):
        return True

    profile = await db.get_profile()
    titles = await db.get_recent_titles(15)
    today = datetime.now().strftime("%Y-%m-%d (%A)")
    ev = await evaluate_message(text, profile, today, titles)
    await asyncio.sleep(AI_DELAY_SECONDS)
    if ev is None:
        return False

    if not ev.is_opportunity or ev.is_duplicate or ev.is_past_event_recap:
        log.info("skip %s (opp=%s dup=%s past=%s score=%s)", link, ev.is_opportunity,
                 ev.is_duplicate, ev.is_past_event_recap, ev.relevance_score)
        return True

    score = ev.relevance_score
    if score < DIGEST_MIN_SCORE:
        return True

    days_left = days_until(ev.deadline_iso)
    if days_left is not None and days_left < 0:
        log.info("skip %s (deadline %s already passed)", link, ev.deadline_iso)
        return True

    immediate = score >= IMMEDIATE_MIN_SCORE or (
        days_left is not None and days_left <= URGENT_DEADLINE_DAYS
    )
    opp_id = await db.save_opportunity(
        title=ev.title or "Untitled opportunity",
        category=ev.category,
        channel_source=channel,
        deadline=ev.deadline_or_date,
        deadline_date=ev.deadline_iso,
        score=score,
        urgency="IMMEDIATE" if immediate else "DIGEST",
        post_link=link,
        summary=ev.action_required,
        why_it_matches=ev.why_it_matches,
        notified=immediate,
    )
    if not opp_id:
        return True
    if immediate:
        try:
            item = await db.get_opportunity_by_id(opp_id)
            await send_card(item, alert_header(item))
        except Exception:
            log.exception("failed to send alert for %s", link)
    else:
        log.info("saved for digest: %s (%s)", ev.title, score)
    return True


async def scan_channel(username: str, limit: int, forced: bool = False) -> int:
    """Scans a channel. Normal mode fetches posts newer than last_message_id; forced mode
    fetches the latest `limit` posts regardless. Returns the number of posts examined."""
    entity = await user_client.get_entity(username)
    last_id = await db.get_last_message_id(username)
    kwargs = {"limit": limit}
    if not forced and last_id:
        kwargs["min_id"] = last_id
    msgs = sorted(
        [m async for m in user_client.iter_messages(entity, **kwargs)], key=lambda m: m.id
    )

    examined = 0
    async with process_lock:
        for m in msgs:
            if not forced and m.id <= await db.get_last_message_id(username):
                continue
            if not await process_message(username, m):
                log.warning("AI unavailable; stopping scan of @%s at post %s", username, m.id)
                break
            await db.set_last_message_id(username, m.id)
            examined += 1
    return examined


async def ensure_joined(username: str) -> None:
    try:
        await user_client(JoinChannelRequest(await user_client.get_entity(username)))
    except Exception as exc:
        log.debug("join @%s: %r", username, exc)


async def catch_up_channels() -> int:
    """Scans every active channel for posts newer than the last one seen. Returns the total
    number of posts evaluated."""
    total = 0
    for username in await db.get_active_channels():
        try:
            await ensure_joined(username)
            first_run = await db.get_last_message_id(username) == 0
            limit = NEW_CHANNEL_SCAN_LIMIT if first_run else CATCHUP_LIMIT
            n = await scan_channel(username, limit)
            total += n
            log.info("catch-up @%s: %d posts examined", username, n)
        except ValueError:
            log.warning("catch-up: @%s does not exist; fix it with /remove and /add", username)
        except Exception:
            log.exception("catch-up failed for @%s", username)
    return total


# ---------- live channel listener ----------

@user_client.on(events.NewMessage(incoming=True))
async def on_channel_post(event):
    if not event.is_channel or event.is_group:
        return
    chat = await event.get_chat()
    username = (getattr(chat, "username", None) or "").lower()
    if not username or username not in await db.get_active_channels():
        return
    async with process_lock:
        if event.message.id <= await db.get_last_message_id(username):
            return
        try:
            if await process_message(username, event.message):
                await db.set_last_message_id(username, event.message.id)
        except Exception:
            log.exception("live processing failed for @%s/%s", username, event.message.id)


# ---------- bot: text messages and commands ----------

@bot_client.on(events.NewMessage(incoming=True))
async def on_bot_message(event):
    if event.sender_id != ADMIN_USER_ID:
        return
    text = (event.raw_text or "").strip()
    m = re.match(r"^/(\w+)(?:@\w+)?(?:\s+(.*))?$", text, re.S)
    if not m:
        if text:
            await handle_plain_text(event, text)
        return
    cmd, arg = m.group(1).lower(), (m.group(2) or "").strip()
    _spawn(_delete_after([event.id], COMMAND_TTL))  # the command itself is clutter too
    try:
        await handle_command(cmd, arg)
    except Exception as exc:
        log.exception("command /%s failed", cmd)
        await say(f"⚠️ /{cmd} failed: {exc}", ERROR_TTL)


def split_title_deadline(text: str) -> tuple[str, str]:
    title, _, deadline = text.partition("|")
    return " ".join(title.split()), deadline.strip()


async def create_manual_opportunity(text: str) -> dict | None:
    title, deadline = split_title_deadline(text)
    if not title:
        return None
    opp_id = await db.create_manual_opportunity(title=title, deadline=deadline)
    return await db.get_opportunity_by_id(opp_id)


async def handle_plain_text(event, text: str) -> None:
    """Free text is only meaningful as the answer to a pending deadline / manual-add prompt."""
    admin = event.sender_id
    if admin in pending_deadline_edits:
        opp_id, card_id = pending_deadline_edits.pop(admin)
        prompt_id = pending_prompt_ids.pop(admin, None)
        await db.update_opportunity_deadline(opp_id, text, parse_deadline_date(text))
        _spawn(_delete_after([i for i in (event.id, prompt_id) if i], 2))
        shown = (await db.get_opportunity_by_id(opp_id) or {}).get("deadline", text)
        await say(f"✅ Deadline updated to: <b>{esc(shown)}</b>", REPLY_TTL, as_html=True)
        await refresh_card(opp_id, card_id)
    elif admin in pending_manual_add:
        prompt_id = pending_manual_add.pop(admin)
        item = await create_manual_opportunity(text)
        _spawn(_delete_after([event.id, prompt_id], 2))
        if item:
            await send_card(item, "➕ <b>Manual opportunity added</b>")
        else:
            await say("A title is required. Format: Title | Deadline", ERROR_TTL)


async def handle_command(cmd: str, arg: str) -> None:
    if cmd in ("start", "help"):
        await say(HELP_TEXT, INFO_TTL)

    elif cmd in VIEW_COMMANDS:
        await send_board(VIEW_COMMANDS[cmd])

    elif cmd == "add_opp":
        item = await create_manual_opportunity(arg) if arg else None
        if item:
            await send_card(item, "➕ <b>Manual opportunity added</b>")
        else:
            await say("Usage: /add_opp <Title> | <Deadline>   e.g. /add_opp MIT RSI | Feb 1, 2027", ERROR_TTL)

    elif cmd == "digest":
        if not await send_digest():
            await say("No unsent digest opportunities.")

    elif cmd == "channels":
        channels = await db.get_active_channels()
        await say(
            "Monitored channels:\n" + "\n".join(f"• @{c}" for c in channels)
            if channels else "No channels yet. Use /add @channel",
            INFO_TTL,
        )

    elif cmd == "add":
        await add_channels(arg)

    elif cmd == "remove":
        await remove_channels(arg)

    elif cmd == "profile":
        await say("Current profile:\n\n" + await db.get_profile(), INFO_TTL)

    elif cmd == "set_profile":
        if not arg:
            await say("Usage: /set_profile <new text>", ERROR_TTL)
            return
        await db.set_profile(arg)
        await say("✅ Profile updated. It applies to all new evaluations.")

    elif cmd == "catchup":
        channels = await db.get_active_channels()
        await say(f"Scanning the last {FORCED_CATCHUP_LIMIT} posts of {len(channels)} channel(s)…", 10)
        total = 0
        for username in channels:
            try:
                total += await scan_channel(username, FORCED_CATCHUP_LIMIT, forced=True)
            except Exception as exc:
                await say(f"⚠️ @{username}: {exc}", ERROR_TTL)
        await say(f"Catch-up done ({total} posts examined).")

    else:
        await say("Unknown command. Send /help for the list.")


def at_list(names: list[str]) -> str:
    return ", ".join(f"@{n}" for n in names)


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


async def add_channels_core(names: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Validates, joins and stores channels. Returns (added, already monitored, failed)."""
    monitored = set(await db.get_active_channels())
    added, already, failed = [], [], []
    for name in names:
        if name in monitored:
            already.append(name)
            continue
        try:
            await user_client.get_entity(name)
        except Exception:
            failed.append(f"@{name} (not found)")
            continue
        await ensure_joined(name)
        await db.add_channel(name)
        added.append(name)
        await asyncio.sleep(0.5)  # be gentle with Telegram's flood limits
    return added, already, failed


async def scan_new_channels(names: list[str]) -> int:
    total = 0
    for name in names:
        try:
            total += await scan_channel(name, NEW_CHANNEL_SCAN_LIMIT, forced=True)
        except Exception as exc:
            log.warning("initial scan of @%s failed: %r", name, exc)
    return total


async def web_add_channels(arg: str) -> dict:
    """Called by the web dashboard: same as /add, with the quick scan running in the background."""
    names, invalid = parse_channels(arg)
    added, already, failed = await add_channels_core(names)
    if added:
        _spawn(scan_new_channels(added))
    return {
        "added": added,
        "already": already,
        "failed": [f"{t} (invalid name)" for t in invalid] + failed,
    }


async def add_channels(arg: str) -> None:
    names, invalid = parse_channels(arg)
    if not names:
        await say("Usage: /add ch1 @ch2 https://t.me/ch3", ERROR_TTL)
        return
    added, already, failed = await add_channels_core(names)
    failed = [f"{t} (invalid name)" for t in invalid] + failed

    lines = []
    if added:
        lines.append(f"✅ Added {plural(len(added), 'channel')}: {at_list(added)}")
    if already:
        lines.append(f"ℹ️ Already monitored: {at_list(already)}")
    if failed:
        lines.append(f"❌ Couldn't add: {', '.join(failed)}")
    await say("\n".join(lines), ERROR_TTL if failed else 12)

    if added:
        total = await scan_new_channels(added)
        await say(f"🔎 Quick scan done ({plural(total, 'post')} examined).")


async def remove_channels(arg: str) -> None:
    names, invalid = parse_channels(arg)
    if not names:
        await say("Usage: /remove ch1 @ch2", ERROR_TTL)
        return
    removed, missing = [], []
    for name in names:
        (removed if await db.remove_channel(name) else missing).append(name)
    lines = []
    if removed:
        lines.append(f"🗑 Removed {plural(len(removed), 'channel')}: {at_list(removed)}")
    if missing:
        lines.append(f"ℹ️ Not monitored: {at_list(missing)}")
    if invalid:
        lines.append(f"❌ Invalid: {', '.join(invalid)}")
    await say("\n".join(lines), 12)


# ---------- bot: inline buttons ----------

@bot_client.on(events.CallbackQuery)
async def on_callback(event):
    if event.sender_id != ADMIN_USER_ID:
        await event.answer()
        return
    try:
        await handle_callback(event, event.data.decode())
    except MessageNotModifiedError:
        await event.answer()
    except Exception:
        log.exception("callback %r failed", event.data)
        await event.answer("Something went wrong, see scout.log", alert=True)


async def clear_pending(admin: int) -> None:
    pending_deadline_edits.pop(admin, None)
    pending_manual_add.pop(admin, None)
    stale = [pending_prompt_ids.pop(admin, None)]
    stale = [i for i in stale if i]
    if stale:
        _spawn(_delete_after(stale, 0))


async def handle_callback(event, data: str) -> None:
    admin = event.sender_id
    kind, _, rest = data.partition(":")

    if kind == "st":
        opp_id_text, _, target = rest.partition(":")
        opp_id = int(opp_id_text)
        item = await db.get_opportunity_by_id(opp_id)
        if not item:
            await event.answer("That opportunity no longer exists.", alert=True)
            return
        if target == item["status"] and target in ("PLANNED", "WORKING", "FINISHED"):
            target = "UNTRACKED"  # tapping the active state again clears it
        await db.update_opportunity_status(opp_id, target)
        item["status"] = target
        await event.answer(STATUS_TOASTS[target])
        await event.edit(
            render_card(item), buttons=card_buttons(item), parse_mode="html", link_preview=False
        )

    elif kind == "dl":
        opp_id = int(rest)
        item = await db.get_opportunity_by_id(opp_id)
        if not item:
            await event.answer("That opportunity no longer exists.", alert=True)
            return
        await clear_pending(admin)
        pending_deadline_edits[admin] = (opp_id, event.message_id)
        await event.answer()
        prompt = await say(
            f"🗓️ <b>Editing deadline for:</b> <i>{esc(item['title'])}</i>\n"
            f"Current deadline: <code>{esc(item['deadline'])}</code>\n\n"
            "Reply or send the new deadline now (e.g., <code>Nov 15, 2026</code> or <code>Dec 1, 23:59</code>):",
            PROMPT_TTL, as_html=True, buttons=[[Button.inline("❌ Cancel", "dl_cancel")]],
        )
        pending_prompt_ids[admin] = prompt.id

    elif kind == "addopp":
        await clear_pending(admin)
        await event.answer()
        prompt = await say(
            "➕ <b>New manual opportunity</b>\nSend it as <code>Title | Deadline</code>, "
            "e.g. <code>MIT RSI | Feb 1, 2027</code> (the deadline is optional):",
            PROMPT_TTL, as_html=True, buttons=[[Button.inline("❌ Cancel", "dl_cancel")]],
        )
        pending_manual_add[admin] = prompt.id

    elif data == "dl_cancel":
        await clear_pending(admin)
        await event.answer("Cancelled")
        try:
            await event.delete()
        except Exception:
            pass

    elif kind == "open":
        item = await db.get_opportunity_by_id(int(rest))
        if not item:
            await event.answer("That opportunity no longer exists.", alert=True)
            return
        await event.answer()
        await send_card(item)

    elif kind == "list":
        text, buttons = await render_board(rest if rest in VIEW_TITLES else "ACTIVE")
        await event.answer()
        await event.edit(text, buttons=buttons, parse_mode="html", link_preview=False)

    else:
        await event.answer()


# ---------- daily digest ----------

async def digest_scheduler() -> None:
    while True:
        await asyncio.sleep(600)
        try:
            last = await db.get_meta("last_digest_at")
            now = datetime.now(timezone.utc)
            if last is None:
                await db.set_meta("last_digest_at", now.isoformat())
            elif now - datetime.fromisoformat(last) >= DIGEST_INTERVAL:
                await send_digest()
        except Exception:
            log.exception("daily digest failed")


# ---------- entry point ----------

async def main() -> None:
    await db.init_db()
    await bot_client.start(bot_token=BOT_TOKEN)
    await user_client.start()  # first run asks for phone number and login code in the console
    log.info("Both clients connected")

    try:
        await say("🟢 ScoutAI is online. Send /help for commands.", 20)
    except Exception as exc:
        log.warning("Could not message admin (open the bot and press Start first): %r", exc)

    web_api.hooks.scan = catch_up_channels
    web_api.hooks.add_channels = web_add_channels
    web_server = uvicorn.Server(uvicorn.Config(
        web_api.app, host=WEB_HOST, port=WEB_PORT, log_level="warning",
        log_config=None, access_log=False,  # log_config=None: uvicorn's console setup breaks under pythonw
    ))

    async def run_web() -> None:
        # A busy port must not take the Telegram bot down with it.
        probe = socket.socket()
        try:
            probe.bind((WEB_HOST, WEB_PORT))
        except OSError:
            log.error("Port %d is in use; the web dashboard is disabled this run", WEB_PORT)
            return
        finally:
            probe.close()
        banner = f"🌐 ScoutAI Web Dashboard is live at: http://localhost:{WEB_PORT}"
        log.info(banner)
        print(banner, flush=True)
        try:
            await web_server.serve()
        except SystemExit:
            log.error("Web dashboard failed to start")

    scheduler = asyncio.create_task(digest_scheduler())
    catchup = asyncio.create_task(catch_up_channels())
    try:
        await asyncio.gather(
            user_client.run_until_disconnected(), bot_client.run_until_disconnected(), run_web()
        )
    finally:
        web_server.should_exit = True
        scheduler.cancel()
        catchup.cancel()


if __name__ == "__main__":
    # Single-instance guard: two copies would fight over the Telegram session files.
    _lock = socket.socket()
    try:
        _lock.bind(("127.0.0.1", 47653))
    except OSError:
        log.error("ScoutAI is already running (run stop_scout.bat first)")
        sys.exit(1)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped")
