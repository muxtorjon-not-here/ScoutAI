"""Tiny stand-in for the ScoutAI dashboard API, to try the mobile app without Telegram credentials.

    python mobile/tools/stub_server.py            # listens on 0.0.0.0:8000, password "test"

Standard library only. Mirrors the JSON shapes of web_api.py; data lives in memory.
"""
import json
import re
import sys
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

PASSWORD = "test"
TOKEN = "stub-token-123"
today = date.today()


def opp(i, title, cat, score, days, status="UNTRACKED", src="@opportunities_uz", link="https://example.com/p/"):
    return {
        "id": i, "title": title, "category": cat, "channel_source": src, "source_label": src.lstrip("@"),
        "source_url": f"https://t.me/{src.lstrip('@')}",
        "deadline": (today + timedelta(days=days)).strftime("%b %d, %Y") if days is not None else "",
        "deadline_date": (today + timedelta(days=days)).isoformat() if days is not None else None,
        "score": score, "urgency": "IMMEDIATE" if score >= 8 else "DIGEST", "post_link": f"{link}{i}",
        "summary": f"Summary of {title}. A longer description so the card has two lines of text to truncate and expand.",
        "why_it_matches": "Matches your CS background and interest in ML research." if score >= 5 else "",
        "status": status, "created_at": f"2026-11-{10 - i % 9:02d} 10:00:00",
    }


OPPS = [
    opp(1, "Google STEP Internship 2027", "Internship", 9, 2),
    opp(2, "Fully funded ML summer school in Zurich", "Summer School", 8, 20),
    opp(3, "Hackathon: Build with AI Tashkent", "Hackathon", 6, -1),
    opp(4, "Erasmus Mundus scholarship (Data Science)", "Scholarship", 7, 45),
    opp(5, "Open source mentoring programme", "Program", 4, None),
    opp(6, "Research assistant, Westminster Univ.", "Research", 8, 6, "PLANNED"),
    opp(7, "Kaggle playground competition", "Competition", 5, 12, "WORKING"),
    opp(8, "MIT Beaver Works Summer Institute", "Summer School", 9, -10, "FINISHED"),
    opp(9, "Irrelevant crypto giveaway", "Other", 1, 3, "DISMISSED"),
]
CHANNELS = [
    {"key": "opportunities_uz", "label": "Opportunities UZ", "url": "https://t.me/opportunities_uz"},
    {"key": "-100123/45", "label": "Study Abroad / Scholarships topic", "url": "https://t.me/c/123/45"},
]
WEB = [
    {"id": "devpost", "name": "Devpost", "kind": "rss", "urls": ["https://devpost.com/feed"], "enabled": True, "custom": False, "found": 12},
    {"id": "scholarships_com", "name": "Scholarships.com", "kind": "rss", "urls": ["https://x"], "enabled": False, "custom": False, "found": 0},
]
PROFILE = {"profile": "CS applicant in Tashkent. Interested in ML, summer schools, scholarships."}
scan_calls = 0


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def authed(self):
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def route(self, method):
        global scan_calls
        path = self.path.split("?")[0]
        if path == "/api/login" and method == "POST":
            if self.body().get("password") == PASSWORD:
                return self.send(200, {"token": TOKEN})
            return self.send(401, {"detail": "Wrong password"})
        if not self.authed():
            return self.send(401, {"detail": "Not signed in"})
        if path == "/api/ping":
            return self.send(200, {"ok": True})
        if path == "/api/opportunities" and method == "GET":
            return self.send(200, {"opportunities": OPPS, "stats": {}})
        if path == "/api/opportunities" and method == "POST":
            b = self.body()
            o = opp(max(x["id"] for x in OPPS) + 1, b["title"], b.get("category") or "Manual", b.get("score", 0), None,
                    b.get("status", "UNTRACKED"), "manual")
            o["post_link"] = b.get("post_link", ""); o["deadline"] = b.get("deadline", ""); o["summary"] = b.get("summary", "")
            OPPS.append(o)
            return self.send(201, o)
        m = re.fullmatch(r"/api/opportunities/(\d+)(/status|/deadline)?", path)
        if m:
            o = next((x for x in OPPS if x["id"] == int(m[1])), None)
            if not o:
                return self.send(404, {"detail": "Opportunity not found"})
            if method == "DELETE":
                OPPS.remove(o); return self.send(200, {"deleted": o["id"]})
            if m[2] == "/status":
                o["status"] = self.body()["status"]
            elif m[2] == "/deadline":
                o["deadline"] = self.body()["deadline"]; o["deadline_date"] = None
            return self.send(200, o)
        if path == "/api/channels" and method == "GET":
            return self.send(200, {"channels": CHANNELS})
        if path == "/api/channels" and method == "POST":
            names = re.findall(r"@?([A-Za-z0-9_]{4,})", self.body()["channels"])
            added = []
            for n in names:
                if not any(c["key"] == n for c in CHANNELS):
                    CHANNELS.append({"key": n, "label": n, "url": f"https://t.me/{n}"}); added.append(n)
            return self.send(200, {"added": added, "already": [], "failed": []})
        if path.startswith("/api/channels/") and method == "DELETE":
            key = unquote(path[len("/api/channels/"):])
            for c in CHANNELS:
                if c["key"] == key:
                    CHANNELS.remove(c); return self.send(200, {"removed": key})
            return self.send(404, {"detail": "Channel is not monitored"})
        if path == "/api/web_sources" and method == "GET":
            return self.send(200, {"sources": WEB})
        if path == "/api/web_sources" and method == "POST":
            url = self.body()["url"]
            if not url.startswith("http"):
                return self.send(400, {"detail": "The address must start with http:// or https://"})
            WEB.append({"id": "c_" + str(len(WEB)), "name": url.split("//")[1].split("/")[0], "kind": "rss", "urls": [url], "enabled": True, "custom": True, "found": 0})
            return self.send(201, {"id": WEB[-1]["id"], "name": WEB[-1]["name"]})
        m = re.fullmatch(r"/api/web_sources/(.+)", path)
        if m:
            s = next((x for x in WEB if x["id"] == m[1]), None)
            if not s:
                return self.send(404, {"detail": "Unknown website"})
            if method == "PATCH":
                s["enabled"] = self.body()["enabled"]; return self.send(200, {"id": s["id"], "enabled": s["enabled"]})
            if not s["custom"]:
                return self.send(404, {"detail": "Only websites you added yourself can be removed"})
            WEB.remove(s); return self.send(200, {"removed": s["id"]})
        if path == "/api/profile":
            if method == "PUT":
                PROFILE["profile"] = self.body()["profile"]
            return self.send(200, PROFILE)
        if path == "/api/scan" and method == "POST":
            import time; time.sleep(2); scan_calls += 1
            return self.send(200, {"running": False, "evaluated": 3})
        return self.send(404, {"detail": "Not found"})

    do_GET = lambda self: self.route("GET")
    do_POST = lambda self: self.route("POST")
    do_PATCH = lambda self: self.route("PATCH")
    do_PUT = lambda self: self.route("PUT")
    do_DELETE = lambda self: self.route("DELETE")


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Stub ScoutAI API on 0.0.0.0:{port}  password={PASSWORD!r}")
    ThreadingHTTPServer(("0.0.0.0", port), H).serve_forever()
