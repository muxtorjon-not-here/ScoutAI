from datetime import datetime, timezone
from typing import Optional

import aiosqlite

import uuid

from config import DB_PATH, DEFAULT_PROFILE_PATH, INITIAL_CHANNELS
from utils import parse_deadline_date

NOT_SPECIFIED = "⚠️ Not specified (Tap to set)"
VALID_STATUSES = ("UNTRACKED", "PLANNED", "WORKING", "FINISHED", "DISMISSED")
OPEN_STATUSES = ("UNTRACKED", "PLANNED", "WORKING")

SCHEMA = """
CREATE TABLE IF NOT EXISTS channels (
    username TEXT PRIMARY KEY,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1
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


async def add_channel(username: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO channels (username, is_active) VALUES (?, 1) "
            "ON CONFLICT(username) DO UPDATE SET is_active = 1",
            (username,),
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
) -> int:
    """Inserts an opportunity and returns its id (0 if the post link was already saved)."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO seen_opportunities (title, category, channel_source, "
            "deadline, deadline_date, score, urgency, post_link, summary, why_it_matches, "
            "status, notified_in_digest, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (title, category, channel_source, normalize_deadline_text(deadline),
             deadline_date or None, score, urgency, post_link, summary, why_it_matches,
             status, 1 if notified else 0, _now()),
        )
        await db.commit()
        return cur.lastrowid if cur.rowcount else 0


async def get_opportunity_by_id(opp_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM seen_opportunities WHERE id = ?", (opp_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_opportunity_status(opp_id: int, status: str) -> None:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid status {status!r}")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE seen_opportunities SET status = ? WHERE id = ?", (status, opp_id))
        await db.commit()


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
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM seen_opportunities WHERE id = ?", (opp_id,))
        await db.commit()
        return cur.rowcount > 0


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
