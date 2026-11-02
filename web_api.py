"""FastAPI dashboard backend. It shares scout.db with the Telegram bot; the bot-side actions
(scanning, joining channels) are injected by main.py through `hooks` to avoid a circular import."""
import asyncio
import hashlib
import hmac
import logging
import time
from typing import Awaitable, Callable, Optional
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

import database as db
from config import BASE_DIR, WEB_PASSWORD
from utils import parse_channels

log = logging.getLogger("scoutai.web")

INDEX_HTML = BASE_DIR / "templates" / "index.html"
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
SCAN_WAIT_SECONDS = 40


class Hooks:
    """Filled in by main.py once the Telegram clients exist."""
    scan: Optional[Callable[[], Awaitable[int]]] = None
    add_channels: Optional[Callable[[str], Awaitable[dict]]] = None


hooks = Hooks()
app = FastAPI(title="ScoutAI Dashboard", docs_url=None, redoc_url=None, openapi_url=None)


AUTH_COOKIE = "scoutai_auth"
_AUTH_TOKEN = hmac.new(WEB_PASSWORD.encode(), b"scoutai-dashboard", hashlib.sha256).hexdigest()
_failed_logins = {"count": 0, "until": 0.0}
LOGIN_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ScoutAI</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0d12;color:#e6e8ee;
font:16px system-ui,sans-serif}form{width:min(320px,86vw);display:grid;gap:12px}h1{font-size:20px;margin:0 0 4px}
input,button{padding:12px;border-radius:10px;border:1px solid #2a2f3a;background:#151922;color:inherit;font:inherit}
button{background:#6366f1;border:0;font-weight:600}p{color:#f87171;margin:0;min-height:1em}</style></head>
<body><form method="post" action="/login"><h1>ScoutAI</h1>
<input type="password" name="password" placeholder="Dashboard password" autofocus required>
<button>Sign in</button><p>__ERROR__</p></form></body></html>"""


def _is_authed(request: Request) -> bool:
    return hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), _AUTH_TOKEN)


@app.middleware("http")
async def guard(request: Request, call_next):
    if WEB_PASSWORD:
        # Network mode: any host name is fine, but every page and API call needs the login cookie.
        if request.url.path == "/login" or _is_authed(request):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not signed in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)
    # Local-only mode blocks DNS-rebinding: a hostile page can't reach the dashboard under another name.
    raw = request.headers.get("host", "")
    host = raw.split("]")[0] + "]" if raw.startswith("[") else raw.split(":")[0]
    if host not in ALLOWED_HOSTS:
        return JSONResponse({"detail": "Forbidden host"}, status_code=403)
    return await call_next(request)


@app.get("/login", include_in_schema=False)
async def login_page():
    return HTMLResponse(LOGIN_HTML.replace("__ERROR__", ""))


@app.post("/login", include_in_schema=False)
async def login(request: Request):
    if time.time() < _failed_logins["until"]:
        return HTMLResponse(LOGIN_HTML.replace("__ERROR__", "Too many attempts. Wait a minute."), 429)
    form = parse_qs((await request.body()).decode("utf-8", "ignore"))
    attempt = form.get("password", [""])[0]
    if WEB_PASSWORD and hmac.compare_digest(attempt.encode(), WEB_PASSWORD.encode()):
        _failed_logins["count"] = 0
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(AUTH_COOKIE, _AUTH_TOKEN, max_age=60 * 60 * 24 * 90,
                            httponly=True, samesite="strict")
        return response
    _failed_logins["count"] += 1
    if _failed_logins["count"] >= 5:
        _failed_logins.update(count=0, until=time.time() + 60)
    await asyncio.sleep(1)
    return HTMLResponse(LOGIN_HTML.replace("__ERROR__", "Wrong password."), 401)


# ---------- models ----------

class OpportunityIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: str = Field(default="Manual", max_length=80)
    deadline: str = Field(default="", max_length=200)
    score: int = Field(default=0, ge=0, le=10)
    post_link: str = Field(default="", max_length=500)
    summary: str = Field(default="", max_length=2000)
    status: str = "UNTRACKED"


class StatusIn(BaseModel):
    status: str


class DeadlineIn(BaseModel):
    deadline: str = Field(max_length=200)


class ChannelsIn(BaseModel):
    channels: str = Field(min_length=1, max_length=4000)


class ProfileIn(BaseModel):
    profile: str = Field(min_length=1, max_length=8000)


# ---------- pages ----------

@app.get("/")
async def index():
    if not INDEX_HTML.exists():
        raise HTTPException(500, "templates/index.html is missing")
    return FileResponse(INDEX_HTML, media_type="text/html")


# ---------- opportunities ----------

async def _require(opp_id: int) -> dict:
    item = await db.get_opportunity_by_id(opp_id)
    if not item:
        raise HTTPException(404, "Opportunity not found")
    return item


@app.get("/api/opportunities")
async def list_opportunities(status: Optional[str] = None, min_score: int = 1):
    if status and status not in db.VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(db.VALID_STATUSES)}")
    return {
        "opportunities": await db.get_all_opportunities(status, min_score),
        "stats": await db.get_stats(),
    }


@app.post("/api/opportunities", status_code=201)
async def create_opportunity(body: OpportunityIn):
    if body.status not in db.VALID_STATUSES:
        raise HTTPException(400, "Invalid status")
    if body.post_link and not body.post_link.startswith(("http://", "https://")):
        raise HTTPException(400, "Link must start with http:// or https://")
    opp_id = await db.create_manual_opportunity(**body.model_dump())
    if not opp_id:
        raise HTTPException(409, "An opportunity with that link already exists")
    return await db.get_opportunity_by_id(opp_id)


@app.patch("/api/opportunities/{opp_id}/status")
async def set_status(opp_id: int, body: StatusIn):
    if body.status not in db.VALID_STATUSES:
        raise HTTPException(400, f"status must be one of {', '.join(db.VALID_STATUSES)}")
    await _require(opp_id)
    await db.update_opportunity_status(opp_id, body.status)
    return await db.get_opportunity_by_id(opp_id)


@app.patch("/api/opportunities/{opp_id}/deadline")
async def set_deadline(opp_id: int, body: DeadlineIn):
    await _require(opp_id)
    await db.update_opportunity_deadline(opp_id, body.deadline.strip())
    return await db.get_opportunity_by_id(opp_id)


@app.delete("/api/opportunities/{opp_id}")
async def delete_opportunity(opp_id: int):
    if not await db.delete_opportunity(opp_id):
        raise HTTPException(404, "Opportunity not found")
    return {"deleted": opp_id}


# ---------- channels ----------

@app.get("/api/channels")
async def list_channels():
    return {"channels": await db.get_active_channels()}


@app.post("/api/channels")
async def add_channels(body: ChannelsIn):
    if hooks.add_channels is None:
        raise HTTPException(503, "Telegram client is not ready yet")
    names, invalid = parse_channels(body.channels)
    if not names and not invalid:
        raise HTTPException(400, "No channels given")
    result = await hooks.add_channels(body.channels)
    return result


@app.delete("/api/channels/{username}")
async def remove_channel(username: str):
    if not await db.remove_channel(username.lstrip("@").lower()):
        raise HTTPException(404, "Channel is not monitored")
    return {"removed": username}


# ---------- scan ----------

_scan_task: Optional[asyncio.Task] = None


@app.post("/api/scan")
async def scan_now():
    """Runs the same catch-up the bot does at startup. Long scans keep going in the background."""
    global _scan_task
    if hooks.scan is None:
        raise HTTPException(503, "Telegram client is not ready yet")
    if _scan_task and not _scan_task.done():
        return {"running": True, "evaluated": None}
    _scan_task = asyncio.create_task(hooks.scan())
    done, _ = await asyncio.wait({_scan_task}, timeout=SCAN_WAIT_SECONDS)
    if _scan_task not in done:
        return {"running": True, "evaluated": None}
    try:
        return {"running": False, "evaluated": _scan_task.result()}
    except Exception as exc:
        log.exception("manual scan failed")
        raise HTTPException(500, f"Scan failed: {exc}")


# ---------- AI profile ----------

@app.get("/api/profile")
async def get_profile():
    return {"profile": await db.get_profile()}


@app.put("/api/profile")
async def put_profile(body: ProfileIn):
    await db.set_profile(body.profile.strip())
    return {"profile": await db.get_profile()}
