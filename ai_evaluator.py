import asyncio
import json
import logging
import re
from datetime import date
from typing import Optional

import httpx
from pydantic import BaseModel, Field, field_validator

from config import AI_API_KEY

log = logging.getLogger("scoutai.ai")

TIMEOUT = 35.0
MIN_TEXT_LENGTH = 40
MAX_TEXT_LENGTH = 6000
RETRY_DELAYS = (3, 10, 25, 45)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "openai/gpt-oss-20b"]
_groq_state = {"good": None}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_MODELS = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-flash-lite-latest"]
_gemini_state = {"good": None}

SYSTEM_PROMPT = """You are ScoutAI, a strict opportunity-screening assistant for one specific person.
You receive a post from a Telegram channel or a listing from an opportunity website, the person's profile, today's date, and titles of opportunities \
already sent to them. Decide whether the post is a genuinely valuable, still-actionable opportunity for THIS person.

Rules:
- is_opportunity: true only if the post announces a concrete opportunity (admissions fair, scholarship, \
research position, internship, hackathon, competition, etc.) that matches the profile's targets.
- is_past_event_recap: true if the post reports on, recaps or thanks participants of an event that already happened, \
or the deadline/date is before today's date.
- is_duplicate: true if the post describes the same opportunity/event as one of the recent titles, even if worded differently.
- Apply the profile's location and "strictly ignore" rules rigidly (e.g. offline events in other cities, \
age/grade restrictions the person does not meet, paid ads, greetings).
- relevance_score (1-10): 9-10 = exceptional match with a deadline or date still open; 8 = strong match; \
5-7 = possible or partial match worth a digest (when unsure between 4 and 5, choose 5); \
1-4 = weak or irrelevant.
- urgency: "IMMEDIATE" if score >= 8, "DIGEST" if score is 5, 6 or 7, otherwise "IGNORE".
- title: short descriptive title in English. category: e.g. Admissions Fair, Scholarship, Internship, Hackathon, \
Competition, Research. deadline_or_date: the deadline or event date as written, or "Not specified". \
deadline_iso: the single date that matters most (application/registration deadline if there is one, otherwise the \
event date) as YYYY-MM-DD, resolving relative words like "tomorrow" or "this Friday" from today's date and assuming \
the nearest upcoming year when none is given; "" if there is no clear date.
- action_required: one short sentence on what the person must do (register, apply, etc.).
- why_it_matches: one or two sentences tying the post to the profile.

Respond with ONLY a JSON object with exactly these keys:
is_opportunity (bool), is_duplicate (bool), is_past_event_recap (bool), relevance_score (int 1-10), \
urgency (string), title (string), category (string), deadline_or_date (string), deadline_iso (string), \
action_required (string), \
why_it_matches (string)."""


class Evaluation(BaseModel):
    is_opportunity: bool = False
    is_duplicate: bool = False
    is_past_event_recap: bool = False
    relevance_score: int = Field(default=1, ge=1, le=10)
    urgency: str = "IGNORE"
    title: str = ""
    category: str = "Other"
    deadline_or_date: str = "Not specified"
    deadline_iso: str = ""
    action_required: str = ""
    why_it_matches: str = ""

    @field_validator("deadline_iso", mode="before")
    @classmethod
    def _valid_iso_date(cls, v):
        try:
            return date.fromisoformat(str(v or "").strip()[:10]).isoformat()
        except ValueError:
            return ""

    @field_validator("relevance_score", mode="before")
    @classmethod
    def _clamp_score(cls, v):
        try:
            return max(1, min(10, int(round(float(v)))))
        except (TypeError, ValueError):
            return 1

    @field_validator("urgency", mode="before")
    @classmethod
    def _norm_urgency(cls, v):
        v = str(v or "").strip().upper()
        return v if v in ("IMMEDIATE", "DIGEST", "IGNORE") else "IGNORE"

    @field_validator("title", "category", "deadline_or_date", "action_required",
                     "why_it_matches", mode="before")
    @classmethod
    def _to_str(cls, v):
        return "" if v is None else str(v).strip()


def passes_prefilter(text: Optional[str]) -> bool:
    return bool(text) and len(text.strip()) >= MIN_TEXT_LENGTH


def _build_user_prompt(text: str, profile: str, today: str, recent_titles: list[str]) -> str:
    titles = "\n".join(f"- {t}" for t in recent_titles) or "(none yet)"
    return (
        f"TODAY'S DATE: {today}\n\n"
        f"PERSON'S PROFILE:\n{profile}\n\n"
        f"RECENTLY SENT OPPORTUNITY TITLES (for duplicate detection):\n{titles}\n\n"
        f"CHANNEL POST:\n\"\"\"\n{text[:MAX_TEXT_LENGTH]}\n\"\"\""
    )


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            raise
        return json.loads(match.group(0))


async def _call_groq(client: httpx.AsyncClient, system: str, user_prompt: str) -> str:
    # Models get retired on Groq; walk the list when one returns 404.
    for model in sorted(GROQ_MODELS, key=lambda m: m != _groq_state["good"]):
        resp = await client.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {AI_API_KEY}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_prompt},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0.1,
            },
        )
        if resp.status_code == 404:
            log.warning("Groq model %s returned 404, trying next model", model)
            continue
        break
    resp.raise_for_status()
    _groq_state["good"] = model
    return resp.json()["choices"][0]["message"]["content"]


async def _call_gemini(client: httpx.AsyncClient, system: str, user_prompt: str) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
    }
    headers = {"x-goog-api-key": AI_API_KEY}
    # Try the last model that worked first; move on when a model is missing (404) or overloaded (503).
    models = sorted(GEMINI_MODELS, key=lambda m: m != _gemini_state["good"])
    for model in models:
        resp = await client.post(GEMINI_URL.format(model=model), headers=headers, json=body)
        if resp.status_code in (404, 503):
            log.warning("%s returned %s, trying next model", model, resp.status_code)
            continue
        break
    resp.raise_for_status()
    _gemini_state["good"] = model
    parts = resp.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


async def _ask(system: str, prompt: str) -> Optional[str]:
    """Sends one prompt to the configured provider with retries. Returns the raw answer, "" when
    the provider answered with nothing usable, or None when it was unreachable (retry later)."""
    call = _call_groq if AI_API_KEY.startswith("gsk_") else _call_gemini
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        for attempt in range(len(RETRY_DELAYS) + 1):
            try:
                return await call(client, system, prompt)
            except (KeyError, IndexError) as exc:
                log.warning("AI returned no usable content: %r", exc)
                return ""
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == len(RETRY_DELAYS):
                    log.error("AI request failed (%s): %r", status, exc)
                    return None
                delay = RETRY_DELAYS[attempt]
                if status == 429:
                    try:
                        delay = max(delay, min(float(exc.response.headers["retry-after"]) + 1, 60))
                    except (KeyError, ValueError):
                        pass
                log.warning("AI rate-limited/unavailable (%s); retrying in %.0fs", status, delay)
                await asyncio.sleep(delay)
    return None


async def evaluate_message(
    text: str, profile: str, today: str, recent_titles: list[str]
) -> Optional[Evaluation]:
    """Returns an Evaluation, or None if the AI provider was unreachable (retry later).
    Unparseable/blocked answers yield a non-opportunity so the post is skipped."""
    if not passes_prefilter(text):
        return Evaluation()

    raw = await _ask(SYSTEM_PROMPT, _build_user_prompt(text, profile, today, recent_titles))
    if raw is None:
        return None
    try:
        return Evaluation.model_validate(_extract_json(raw))
    except Exception as exc:  # malformed JSON or schema mismatch
        log.warning("Could not parse AI output (%r): %.200s", exc, raw)
        return Evaluation()


TRIAGE_PROMPT = """You screen listings from opportunity websites for ONE person, using only each listing's title and first lines. Keep a listing if it could plausibly be a real match for the profile: eligible location or open worldwide, fitting level/age and field. Drop clear mismatches: restricted to other countries or regions, wrong level or age, paid jobs for experienced people, ads, news and general advice articles. When unsure, keep it.
Respond with ONLY a JSON object: {"keep": [numbers of the listings to keep]}."""


async def triage_listings(profile: str, listings: list[tuple[str, str]]) -> Optional[set[int]]:
    """Cheap first pass over many (title, snippet) pairs in one AI call. Returns the zero-based
    indexes worth a full evaluation, or None if the provider was unreachable (retry later)."""
    lines = "\n".join(f"{i + 1}. {title} — {snippet[:200]}" for i, (title, snippet) in enumerate(listings))
    raw = await _ask(TRIAGE_PROMPT, f"PERSON'S PROFILE:\n{profile}\n\nLISTINGS:\n{lines}")
    if raw is None:
        return None
    try:
        keep = _extract_json(raw).get("keep", [])
        return {int(n) - 1 for n in keep if 0 < int(n) <= len(listings)}
    except Exception as exc:
        log.warning("Could not parse triage output (%r): %.200s", exc, raw)
        return set(range(len(listings)))  # unreadable answer: evaluate everything rather than drop it
