import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

import aiosqlite

import uuid

from config import DB_PATH, DEFAULT_PROFILE_PATH, INITIAL_CHANNELS
from dedupe import find_duplicate
from utils import infer_tags, parse_deadline_date

NOT_SPECIFIED = "⚠️ Not specified (Tap to set)"
VALID_STATUSES = ("UNTRACKED", "PLANNED", "WORKING", "FINISHED", "DISMISSED")
OPEN_STATUSES = ("UNTRACKED", "PLANNED", "WORKING")

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    username TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1,
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS web_sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    urls TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    custom INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS web_seen (
    link TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    profile_text TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT,
    channel_source TEXT,
    deadline TEXT NOT NULL DEFAULT '⚠️ Not specified (Tap to set)',
    deadline_date TEXT,
    score INTEGER,
    urgency TEXT,
    post_link TEXT UNIQUE,
    summary TEXT,
    why_it_matches TEXT,
    status TEXT NOT NULL DEFAULT 'UNTRACKED',
    notified_in_digest INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_deadline_text(text: Optional[str]) -> str:
    """The deadline text is never empty: unknown deadlines become a tap-to-set placeholder."""
    text = (text or "").strip()
    if not text or text.lower().rstrip(".") in ("not specified", "n/a", "none", "unknown", "-"):
        return NOT_SPECIFIED
    return text


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)

        # Safe migrations for databases created by earlier versions
        async with db.execute("PRAGMA table_info(seen_opportunities)") as cur:
            columns = {row[1] for row in await cur.fetchall()}
        if "deadline_date" not in columns:
            await db.execute("ALTER TABLE seen_opportunities ADD COLUMN deadline_date TEXT")
        if "status" not in columns:
            await db.execute(
                "ALTER TABLE seen_opportunities ADD COLUMN status TEXT DEFAULT 'UNTRACKED'"
            )
        if "tags" not in columns:
            await db.execute("ALTER TABLE seen_opportunities ADD COLUMN tags TEXT DEFAULT ''")
        if "insights" not in columns:
            await db.execute("ALTER TABLE seen_opportunities ADD COLUMN insights TEXT DEFAULT ''")
        if "calendar_events" not in columns:
            await db.execute("ALTER TABLE seen_opportunities ADD COLUMN calendar_events TEXT DEFAULT ''")
        if "source_urls" not in columns:
            await db.execute("ALTER TABLE seen_opportunities ADD COLUMN source_urls TEXT DEFAULT ''")
        async with db.execute("PRAGMA table_info(channels)") as cur:
            channel_columns = {row[1] for row in await cur.fetchall()}
        if "title" not in channel_columns:
            await db.execute("ALTER TABLE channels ADD COLUMN title TEXT NOT NULL DEFAULT ''")
        await db.execute("UPDATE seen_opportunities SET status = 'UNTRACKED' WHERE status IS NULL")
        await db.execute(
            "UPDATE seen_opportunities SET deadline = ? WHERE deadline IS NULL "
            "OR TRIM(deadline) = '' OR LOWER(deadline) LIKE 'not specified%'",
            (NOT_SPECIFIED,),
        )
        # Backfill ISO dates that the parser could not read in earlier versions
        async with db.execute(
            "SELECT id, deadline FROM seen_opportunities "
            "WHERE (deadline_date IS NULL OR deadline_date = '') AND deadline != ?",
            (NOT_SPECIFIED,),
        ) as cur:
            undated = await cur.fetchall()
        for opp_id, text in undated:
            parsed = parse_deadline_date(text or "")
            if parsed:
                await db.execute(
                    "UPDATE seen_opportunities SET deadline_date = ? WHERE id = ?", (parsed, opp_id)
                )

        # Opportunities saved before tags existed get keyword-based tags (the AI refines them later)
        async with db.execute(
            "SELECT id, category, title, summary FROM seen_opportunities WHERE tags IS NULL OR tags = ''"
        ) as cur:
            untagged = await cur.fetchall()
        for opp_id, category, title, summary in untagged:
            await db.execute(
                "UPDATE seen_opportunities SET tags = ? WHERE id = ?",
                (",".join(infer_tags(category, title, summary)), opp_id),
            )

        async with db.execute("SELECT COUNT(*) FROM user_profile") as cur:
            (count,) = await cur.fetchone()
        if count == 0:
            text = DEFAULT_PROFILE_PATH.read_text(encoding="utf-8").strip()
            await db.execute(
                "INSERT INTO user_profile (id, profile_text, updated_at) VALUES (1, ?, ?)",
                (text, _now()),
            )
        async with db.execute("SELECT value FROM meta WHERE key = 'channels_seeded'") as cur:
            seeded = await cur.fetchone()
        if not seeded:
            for name in INITIAL_CHANNELS:
                await db.execute("INSERT OR IGNORE INTO channels (username) VALUES (?)", (name,))
            await db.execute("INSERT INTO meta (key, value) VALUES ('channels_seeded', '1')")
        await db.commit()


# ---------- channels ----------

async def get_active_channels() -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT username FROM channels WHERE is_active = 1 ORDER BY username"
        ) as cur:
            return [row[0] for row in await cur.fetchall()]


async def add_channel(username: str, title: str = "") -> None:
    """`username` is a source key: 'name', 'name/27' (topic), 'c/123' or 'c/123/27' (private)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (username, is_active, title) VALUES (?, 1, ?) "
            "ON CONFLICT(username) DO UPDATE SET is_active = 1, title = excluded.title",
            (username, title),
        )
        await db.commit()


async def get_channel_titles() -> dict[str, str]:
    """Display names for sources: chat/topic titles, plus 'web:<id>' keys for websites."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT username, title FROM channels WHERE title != ''") as cur:
            titles = {row[0]: row[1] for row in await cur.fetchall()}
        async with db.execute("SELECT id, name FROM web_sources") as cur:
            titles.update({f"web:{row[0]}": row[1] for row in await cur.fetchall()})
    return titles


# ---------- opportunity websites ----------

async def sync_web_catalogue(catalogue: list[dict]) -> None:
    """Adds new built-in sources and refreshes names/URLs of existing ones; a source the user
    switched off stays off, and custom sources are never touched."""
    async with aiosqlite.connect(DB_PATH) as db:
        for src in catalogue:
            await db.execute(
                "INSERT INTO web_sources (id, name, kind, urls, enabled, custom) VALUES (?, ?, ?, ?, 1, 0) "
                "ON CONFLICT(id) DO UPDATE SET name = excluded.name, kind = excluded.kind, "
                "urls = excluded.urls WHERE custom = 0",
                (src["id"], src["name"], src["kind"], json.dumps(src["urls"])),
            )
        await db.commit()


async def get_web_sources(enabled_only: bool = False) -> list[dict]:
    sql = "SELECT * FROM web_sources" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY name"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
        async with db.execute(
            "SELECT channel_source, COUNT(*) FROM seen_opportunities "
            "WHERE channel_source LIKE 'web:%' GROUP BY channel_source"
        ) as cur:
            found = {row[0][4:]: row[1] for row in await cur.fetchall()}
    for row in rows:
        row["urls"] = json.loads(row["urls"])
        row["enabled"] = bool(row["enabled"])
        row["custom"] = bool(row["custom"])
        row["found"] = found.get(row["id"], 0)
    return rows


async def set_web_source_enabled(source_id: str, enabled: bool) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "UPDATE web_sources SET enabled = ? WHERE id = ?", (1 if enabled else 0, source_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def add_custom_web_source(source_id: str, name: str, url: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO web_sources (id, name, kind, urls, enabled, custom) "
            "VALUES (?, ?, 'rss', ?, 1, 1)",
            (source_id, name, json.dumps([url])),
        )
        await db.commit()
        return cur.rowcount > 0


async def remove_custom_web_source(source_id: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM web_sources WHERE id = ? AND custom = 1", (source_id,))
        await db.commit()
        return cur.rowcount > 0


async def unseen_web_links(links: list[str]) -> set[str]:
    """The subset of `links` that has not been looked at before."""
    if not links:
        return set()
    seen: set[str] = set()
    async with aiosqlite.connect(DB_PATH) as db:
        for i in range(0, len(links), 500):
            chunk = links[i:i + 500]
            marks = ",".join("?" * len(chunk))
            async with db.execute(f"SELECT link FROM web_seen WHERE link IN ({marks})", chunk) as cur:
                seen.update(row[0] for row in await cur.fetchall())
    return set(links) - seen


async def mark_web_seen(links: list[str]) -> None:
    if not links:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executemany(
            "INSERT OR IGNORE INTO web_seen (link, seen_at) VALUES (?, ?)", [(l, _now()) for l in links]
        )
        await db.commit()


async def remove_channel(username: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM channels WHERE username = ?", (username,))
        await db.commit()
        return cur.rowcount > 0


async def get_last_message_id(username: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT last_message_id FROM channels WHERE username = ?", (username,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def set_last_message_id(username: str, message_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE channels SET last_message_id = MAX(last_message_id, ?) WHERE username = ?",
            (message_id, username),
        )
        await db.commit()


# ---------- profile ----------

async def get_profile() -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT profile_text FROM user_profile WHERE id = 1") as cur:
            row = await cur.fetchone()
            return row[0] if row else ""


async def set_profile(text: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE user_profile SET profile_text = ?, updated_at = ? WHERE id = 1",
            (text, _now()),
        )
        await db.commit()


# ---------- opportunities ----------

async def opportunity_exists(post_link: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM seen_opportunities WHERE post_link = ?", (post_link,)
        ) as cur:
            return await cur.fetchone() is not None


async def get_recent_titles(limit: int = 15) -> list[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT title FROM seen_opportunities ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            return [row[0] for row in await cur.fetchall()]


async def save_opportunity(
    *,
    title: str,
    category: str,
    channel_source: str,
    deadline: str,
    deadline_date: str,
    score: int,
    urgency: str,
    post_link: str,
    summary: str,
    why_it_matches: str,
    notified: bool,
    status: str = "UNTRACKED",
    source_urls: str = "",
    tags: Optional[list] = None,
    insights: Optional[dict] = None,
) -> int:
    """Inserts an opportunity and returns its id (0 if the post link was already saved).
    `source_urls` holds the post's normalized external links, space separated, for duplicate checks."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO seen_opportunities (title, category, channel_source, "
            "deadline, deadline_date, score, urgency, post_link, summary, why_it_matches, "
            "status, notified_in_digest, created_at, source_urls, tags, insights) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, category, channel_source, normalize_deadline_text(deadline),
             deadline_date or None, score, urgency, post_link, summary, why_it_matches,
             status, 1 if notified else 0, _now(), source_urls,
             ",".join(tags or infer_tags(category, title, summary)),
             json.dumps(insights) if insights and any(insights.values()) else ""),
        )
        await db.commit()
        return cur.lastrowid if cur.rowcount else 0


async def set_insights(opp_id: int, tags: list, insights: dict) -> None:
    """Stores the AI's tags and mentor-style benefits for one opportunity."""
    async with aiosqlite.connect(DB_PATH) as db:
        if tags:
            await db.execute("UPDATE seen_opportunities SET tags = ? WHERE id = ?", (",".join(tags), opp_id))
        await db.execute(
            "UPDATE seen_opportunities SET insights = ? WHERE id = ?",
            (json.dumps(insights) if insights and any(insights.values()) else "", opp_id),
        )
        await db.commit()


async def get_missing_insights(today: str, limit: int) -> list[dict]:
    """Open opportunities (not dismissed/finished, deadline not passed) that have no benefits yet."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM seen_opportunities WHERE (insights IS NULL OR insights = '') "
            "AND status IN ('UNTRACKED', 'PLANNED', 'WORKING') AND channel_source != 'manual' "
            "AND (deadline_date IS NULL OR deadline_date >= ?) ORDER BY id DESC LIMIT ?",
            (today, limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_dedupe_candidates() -> list[dict]:
    """Every stored opportunity, reduced to the fields duplicate detection compares."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, title, deadline_date, source_urls, status FROM seen_opportunities "
            "ORDER BY id"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def dismiss_existing_duplicates() -> int:
    """One-time cleanup: untouched (UNTRACKED) copies of an earlier opportunity are moved to
    Dismissed, never deleted, so they can still be restored from the dashboard."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM meta WHERE key = 'dedupe_v1'") as cur:
            if await cur.fetchone():
                return 0
    kept: list[dict] = []
    dismiss: list[int] = []
    for row in await get_dedupe_candidates():
        if find_duplicate(row["title"], row["deadline_date"], [], kept) and row["status"] == "UNTRACKED":
            dismiss.append(row["id"])
        else:
            kept.append(row)
    async with aiosqlite.connect(DB_PATH) as db:
        for opp_id in dismiss:
            await db.execute("UPDATE seen_opportunities SET status = 'DISMISSED' WHERE id = ?", (opp_id,))
        await db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('dedupe_v1', '1')")
        await db.commit()
    return len(dismiss)


async def get_opportunity_by_id(opp_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM seen_opportunities WHERE id = ?", (opp_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


change_hooks: list = []  # async callables (opp_id, deleted_item_or_None), e.g. the calendar sync
_background: set = set()


def _fire_change(opp_id: int, deleted_item: Optional[dict] = None) -> None:
    for hook in change_hooks:
        task = asyncio.get_running_loop().create_task(hook(opp_id, deleted_item))
        _background.add(task)
        task.add_done_callback(_background.discard)


async def set_calendar_events(opp_id: int, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE seen_opportunities SET calendar_events = ? WHERE id = ?", (value, opp_id))
        await db.commit()


async def update_opportunity_status(opp_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE seen_opportunities SET status = ? WHERE id = ?", (status, opp_id))
        await db.commit()
    _fire_change(opp_id)


_AUTO = object()


async def update_opportunity_deadline(
    opp_id: int, new_deadline: str, deadline_date=_AUTO
) -> None:
    """Stores the display text plus its ISO date. The date is parsed from the text unless given
    explicitly; when it can't be parsed it is cleared so a stale countdown never shows."""
    if deadline_date is _AUTO:
        deadline_date = parse_deadline_date(new_deadline or "")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE seen_opportunities SET deadline = ?, deadline_date = ? WHERE id = ?",
            (normalize_deadline_text(new_deadline), deadline_date or None, opp_id),
        )
        await db.commit()
    _fire_change(opp_id)


_ORDER = "ORDER BY deadline_date IS NULL, deadline_date ASC, score DESC, id DESC"


async def get_opportunities_by_status(status: str, limit: Optional[int] = None) -> list[dict]:
    sql = f"SELECT * FROM seen_opportunities WHERE status = ? AND notified_in_digest = 1 {_ORDER}"
    params: list = [status]
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_active_opportunities(today: str, undated_since: str) -> list[dict]:
    """Delivered opportunities still worth attention: not finished/dismissed and the deadline
    hasn't passed (or there is no known deadline and it arrived recently / was added manually)."""
    marks = ",".join("?" * len(OPEN_STATUSES))
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM seen_opportunities WHERE notified_in_digest = 1 "
            f"AND status IN ({marks}) AND ("
            f"deadline_date >= ? OR (deadline_date IS NULL AND (created_at >= ? "
            f"OR channel_source = 'manual'))) {_ORDER}",
            (*OPEN_STATUSES, today, undated_since),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def count_by_status() -> dict[str, int]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM seen_opportunities WHERE notified_in_digest = 1 "
            "GROUP BY status"
        ) as cur:
            return {row[0]: row[1] for row in await cur.fetchall()}


async def get_unsent_digest() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM seen_opportunities WHERE urgency = 'DIGEST' "
            "AND notified_in_digest = 0 ORDER BY score DESC, id ASC"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def mark_digest_sent(ids: list[int]) -> None:
    if not ids:
        return
    async with aiosqlite.connect(DB_PATH) as db:
        marks = ",".join("?" * len(ids))
        await db.execute(
            f"UPDATE seen_opportunities SET notified_in_digest = 1 WHERE id IN ({marks})", ids
        )
        await db.commit()


async def create_manual_opportunity(
    title: str,
    category: str = "Manual",
    deadline: str = "",
    score: int = 0,
    post_link: str = "",
    summary: str = "",
    status: str = "UNTRACKED",
) -> int:
    """Adds an opportunity found outside Telegram. Returns its id (0 if the link already exists)."""
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    return await save_opportunity(
        title=title.strip(),
        category=(category or "Manual").strip(),
        channel_source="manual",
        deadline=deadline,
        deadline_date=(parse_deadline_date(deadline) or "") if deadline else "",
        score=max(0, min(10, int(score or 0))),
        urgency="MANUAL",
        post_link=post_link.strip() or f"manual:{uuid.uuid4().hex[:12]}",
        summary=summary.strip(),
        why_it_matches="",
        notified=True,
        status=status,
    )


async def delete_opportunity(opp_id: int) -> bool:
    item = await get_opportunity_by_id(opp_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM seen_opportunities WHERE id = ?", (opp_id,))
        await db.commit()
        deleted = cur.rowcount > 0
    if deleted:
        _fire_change(opp_id, item)
    return deleted


async def get_all_opportunities(status: Optional[str] = None, min_score: int = 1) -> list[dict]:
    """Every saved opportunity (full rows), newest first. Manual items ignore the score filter."""
    sql = "SELECT * FROM seen_opportunities WHERE (score >= ? OR channel_source = 'manual')"
    params: list = [min_score]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC"
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_stats() -> dict[str, int]:
    marks = ",".join("?" * len(OPEN_STATUSES))
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT status, COUNT(*) FROM seen_opportunities GROUP BY status"
        ) as cur:
            by_status = {row[0]: row[1] for row in await cur.fetchall()}
        async with db.execute(
            f"SELECT COUNT(*) FROM seen_opportunities WHERE urgency = 'IMMEDIATE' "
            f"AND status IN ({marks})", OPEN_STATUSES
        ) as cur:
            (high,) = await cur.fetchone()
        async with db.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1") as cur:
            (channels,) = await cur.fetchone()
    return {
        "total_scanned": sum(by_status.values()),
        "untracked": by_status.get("UNTRACKED", 0),
        "planned": by_status.get("PLANNED", 0),
        "working": by_status.get("WORKING", 0),
        "finished": by_status.get("FINISHED", 0),
        "high_urgency": high,
        "active_channels": channels,
    }


# ---------- meta ----------

async def get_meta(key: str) -> Optional[str]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM meta WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None


async def set_meta(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()
