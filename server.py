#!/usr/bin/env python3
"""
OG Checker - local server.

The Windows app (app.py / OGChecker.exe) runs this server privately and shows the page in its own window.
Running this file directly opens the page in your browser instead (handy for development).

On your own computer:   python server.py   (or double-click start.bat)
    -> opens http://127.0.0.1:8765, no limits.

As a public website:    set the environment variable PUBLIC=1 (render.yaml already does).
    -> listens on 0.0.0.0:$PORT, every visitor gets their own check, and safety limits apply
       so one visitor can't get the server's IP blocked by Mojang, Discord, etc.

Settings for the public site (environment variables, all optional):
    MAX_NAMES   most names one check may look up              (default 3000)
    MAX_JOBS    most checks running at once across everyone   (default 20)
    PLATFORMS   comma list of platforms to offer, e.g. "Minecraft,Roblox,GitHub"  (default: all)

Needs: pip install requests
"""

import itertools
import json
import os
import queue
import re
import string
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import checker_core as core

PUBLIC = os.environ.get("PUBLIC", "").strip() in ("1", "true", "yes")
HOST = "0.0.0.0" if PUBLIC else "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
MAX_NAMES = int(os.environ.get("MAX_NAMES", "3000")) if PUBLIC else 10_000_000
MAX_JOBS = int(os.environ.get("MAX_JOBS", "20")) if PUBLIC else 1000
# How many visitors may check the same platform at the same time (public site only).
# The slow, strictly-limited platforms get one slot so visitors don't trip each other's limits.
PLATFORM_SLOTS = {"Minecraft": 4, "Roblox": 3, "TikTok": 1, "GitHub": 1, "Discord": 1, "Instagram": 1}
# Inside a PyInstaller .exe, bundled files live in a temp folder (sys._MEIPASS)
STATIC = os.path.join(getattr(sys, "_MEIPASS", core.app_dir()), "static")
FROZEN = getattr(sys, "frozen", False)
LAST_PAGE_SEEN = [0.0]   # when any open page last checked in (the .exe quits when none are left)

_wanted = [p.strip().lower() for p in os.environ.get("PLATFORMS", "").split(",") if p.strip()]
ENABLED = [p for p in core.PLATFORMS if not _wanted or p.lower() in _wanted] or list(core.PLATFORMS)

CID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


# --------------------------------------------------------------------------
# Minecraft lock history - shared by everyone, so the public site learns faster
# --------------------------------------------------------------------------
class History:
    def __init__(self):
        self.lock = threading.Lock()
        self.data = core.load_history()
        self.dirty = False
        self.last_save = time.time()

    def mark_taken(self, name):
        with self.lock:
            self.data[name] = [int(time.time()), 0]
            self.dirty = True

    def lock_info(self, name, now):
        with self.lock:
            rec = self.data.get(name)
            if not rec:
                return None
            taken_at, freed_at = rec
            if now - taken_at >= core.LOCK_DAYS * core.DAY:
                del self.data[name]
                self.dirty = True
                return None
            if not freed_at:
                rec[1] = freed_at = now
                self.dirty = True
        lo = max(0, core.math.ceil((taken_at + core.LOCK_DAYS * core.DAY - now) / core.DAY))
        hi = max(0, core.math.ceil((freed_at + core.LOCK_DAYS * core.DAY - now) / core.DAY))
        return lo, hi, int((now - taken_at) // core.DAY)

    def save(self, force=False):
        with self.lock:
            if self.dirty and (force or time.time() - self.last_save > 30):
                core.save_history(self.data)
                self.dirty = False
                self.last_save = time.time()

    def reset(self):
        with self.lock:
            n = len(self.data)
            self.data = {}
            core.save_history(self.data)
            self.dirty = False
            return n

    def __len__(self):
        return len(self.data)


HISTORY = History()


# --------------------------------------------------------------------------
# One Job per visitor
# --------------------------------------------------------------------------
class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.running = False
        self.dead = False
        self.platform = "Minecraft"
        self.last_seen = time.time()
        self.run_id = 0
        self.reset_results()
        threading.Thread(target=self.drain_loop, daemon=True).start()

    def reset_results(self):
        self.results = {"avail": [], "locked": [], "taken": []}
        self.stats = {"checked": 0, "avail": 0, "taken": 0, "rare": 0, "skipped": 0, "locked": 0}
        self.status = "Ready"
        self.notice = ""
        self.total = 0
        self.start_time = 0.0
        self.end_time = 0.0
        self.run_id += 1

    def start(self, s, plat, names, total, threads, delay, rare_max):
        self.reset_results()
        self.total = total
        self.platform = plat
        self.q = queue.Queue()
        self.stop_evt = threading.Event()
        self.running = True
        self.status = "Running"
        self.start_time = time.time()
        core.Checker(names, threads, self.q, self.stop_evt, rare_max, plat, delay).start()

    def stop(self):
        with self.lock:
            if self.running:
                self.stop_evt.set()
                self.status = "Stopping"

    def clear(self):
        with self.lock:
            if not self.running:
                self.reset_results()

    def drain_loop(self):
        while not self.dead:
            q = self.q
            try:
                kind, data = q.get(timeout=0.3)
            except queue.Empty:
                continue
            with self.lock:
                if q is self.q:
                    self.handle(kind, data)

    def handle(self, kind, data):
        if kind == "avail":
            name, cats = data
            self.stats["checked"] += 1
            info = HISTORY.lock_info(name, time.time()) if self.platform == "Minecraft" else None
            if info:
                lo, hi, ago = info
                when = f"~{lo}d" if lo == hi else f"{lo}-{hi}d"
                self.stats["locked"] += 1
                self.results["locked"].append({"name": name, "label": f"drops in {when} · seen {ago}d ago"})
                return
            self.stats["avail"] += 1
            # Every category the name fits. The app decides which ones count as rare (user setting).
            self.results["avail"].append({"name": name, "cats": cats})
        elif kind == "taken":
            self.stats["checked"] += 1
            self.stats["taken"] += 1
            if self.platform == "Minecraft":
                HISTORY.mark_taken(data)
            self.results["taken"].append({"name": data})
        elif kind == "skipped":
            self.stats["skipped"] += data
        elif kind == "notice":
            self.notice = data
        elif kind == "status":
            if self.running and not self.stop_evt.is_set():
                self.status = data.rstrip(".")
        elif kind == "done":
            self.running = False
            self.end_time = time.time()
            if self.notice:
                self.status = "Stopped - rate limited"
            else:
                self.status = "Stopped" if self.stop_evt.is_set() else "Finished"
            HISTORY.save(force=True)

    def snapshot(self, offsets, run_id):
        with self.lock:
            if run_id != self.run_id:
                offsets = {}
            end = self.end_time or time.time()
            elapsed = end - self.start_time if self.start_time else 0
            return {
                "runId": self.run_id, "running": self.running, "status": self.status, "notice": self.notice,
                "platform": self.platform, "total": self.total, "elapsed": elapsed, "stats": dict(self.stats),
                "historySize": len(HISTORY),
                "new": {k: v[int(offsets.get(k, 0)):] for k, v in self.results.items()},
            }


JOBS = {}
JOBS_LOCK = threading.Lock()


def get_job(cid):
    with JOBS_LOCK:
        job = JOBS.get(cid)
        if job is None:
            job = JOBS[cid] = Job()
        job.last_seen = time.time()
        return job


def reaper():
    """Stop checks whose page was closed, and forget idle visitors."""
    while True:
        time.sleep(15)
        now = time.time()
        with JOBS_LOCK:
            for cid, job in list(JOBS.items()):
                idle = now - job.last_seen
                if job.running and idle > 180:
                    job.stop()
                elif not job.running and idle > 30 * 60:
                    job.dead = True
                    del JOBS[cid]
        HISTORY.save()
        # The .exe has no window to close, so it quits by itself a few minutes after the last page is closed
        if FROZEN and not PUBLIC and not EMBEDDED[0] and LAST_PAGE_SEEN[0] and now - LAST_PAGE_SEEN[0] > 180:
            shutdown()


def start_job(cid, s):
    plat = s.get("platform", "Minecraft")
    if plat not in ENABLED:
        return "That platform isn't available here."
    spec = core.PLATFORMS[plat]

    def num(key, default, lo, hi, cast=int):
        try:
            v = cast(s.get(key, default))
        except (TypeError, ValueError):
            v = default
        return max(lo, min(hi, v))

    min_len = num("minLen", spec["min"], spec["min"], spec["max"])
    max_len = num("maxLen", min_len, spec["min"], spec["max"])
    if max_len < min_len:
        min_len, max_len = max_len, min_len
    limit = num("limit", min(5000, MAX_NAMES), 1, MAX_NAMES)
    if PUBLIC:  # visitors can go slower than the safe defaults, never faster
        threads = num("threads", spec["threads"], 1, spec["threads"])
        delay = num("delay", spec["delay"], spec["delay"], 120.0, float)
    else:
        threads = num("threads", spec["threads"], 1, 32)
        delay = num("delay", spec["delay"], 0.0, 120.0, float)
    randomize = bool(s.get("random", True))
    mode = s.get("mode", "brute")
    rare_max = {"strict": 2, "normal": 5, "loose": 6}.get(s.get("rare"), 5)

    charset = string.ascii_lowercase
    if s.get("digits"):
        charset += string.digits
    if s.get("underscore") and spec.get("underscore", True):
        charset += "_"
    if s.get("periods") and spec["periods"]:
        charset += "."
    if s.get("hyphens") and spec.get("hyphens", False):
        charset += "-"

    if mode == "words":
        total = min(limit, sum(1 for w in core.WORDS if min_len <= len(w) <= max_len))
        if total == 0:
            return "No words match that length range."
        names = (n for n in core.word_names(min_len, max_len, randomize, limit) if core.valid_name(plat, n))
    elif mode == "keyword":
        kw = str(s.get("keyword", "")).strip().lower()
        allowed = set(string.ascii_lowercase + string.digits
                      + ("_" if spec.get("underscore", True) else "")
                      + ("." if spec["periods"] else "")
                      + ("-" if spec.get("hyphens", False) else ""))
        if not kw:
            return "Type a keyword first."
        if not set(kw) <= allowed:
            return f"That keyword has characters {plat} doesn't allow."
        if len(kw) > max_len:
            return f"'{kw}' is {len(kw)} characters - raise the max length to at least {len(kw)}."
        lo = max(min_len, len(kw))
        every = sum((L - len(kw) + 1) * len(charset) ** (L - len(kw)) for L in range(lo, max_len + 1))
        total = min(limit, every)
        raw = core.keyword_names(kw, charset, min_len, max_len, randomize, every)
        names = itertools.islice((n for n in raw if core.valid_name(plat, n)), limit)
    else:
        every = sum(len(charset) ** n for n in range(min_len, max_len + 1))
        total = min(limit, every)
        raw = core.brute_force_names(charset, min_len, max_len, randomize, every)
        names = itertools.islice((n for n in raw if core.valid_name(plat, n)), limit)

    with JOBS_LOCK:
        job = JOBS.get(cid)
        if job is None:
            job = JOBS[cid] = Job()
        job.last_seen = time.time()
        if job.running:
            return "A check is already running."
        running = [j for j in JOBS.values() if j.running]
        if len(running) >= MAX_JOBS:
            return "The site is busy right now. Try again in a minute."
        if PUBLIC and sum(1 for j in running if j.platform == plat) >= PLATFORM_SLOTS.get(plat, 1):
            return f"Someone else is checking {plat} right now. Try again in a minute, or pick another platform."
        with job.lock:
            job.start(s, plat, names, total, threads, delay, rare_max)
    return None


def config():
    plats = {}
    for name in ENABLED:
        spec = core.PLATFORMS[name]
        plats[name] = {
            "min": spec["min"], "max": spec["max"], "threads": spec["threads"], "delay": spec["delay"],
            "periods": spec["periods"], "underscore": spec.get("underscore", True),
            "hyphens": spec.get("hyphens", False), "batch": spec["batch"], "note": spec["note"],
        }
    counts = {}
    for w in core.WORDS:
        counts[len(w)] = counts.get(len(w), 0) + 1
    return {"platforms": plats, "words": len(core.WORDS), "historySize": len(HISTORY),
            "wordCounts": counts, "public": PUBLIC, "maxNames": MAX_NAMES, "app": FROZEN}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def send_body(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.send_body(json.dumps(obj).encode(), "application/json", code)

    def client_id(self, qs=None):
        cid = self.headers.get("X-Client-Id") or ((qs or {}).get("cid") or [""])[0]
        return cid if CID_RE.match(cid or "") else None

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(STATIC, "index.html"), "rb") as fh:
                self.send_body(fh.read(), "text/html; charset=utf-8")
        elif url.path == "/healthz":
            self.send_body(b"ok", "text/plain")
        elif url.path == "/api/config":
            self.send_json(config())
        elif url.path == "/api/poll":
            qs = parse_qs(url.query)
            cid = self.client_id(qs)
            if not cid:
                return self.send_json({"error": "missing client id"}, 400)
            offsets = {k: int(qs.get(k, ["0"])[0] or 0) for k in ("avail", "locked", "taken")}
            run_id = int(qs.get("run", ["0"])[0] or 0)
            LAST_PAGE_SEEN[0] = time.time()
            self.send_json(get_job(cid).snapshot(offsets, run_id))
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        # Only this site's own page may start or stop checks
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and urlparse(origin).netloc not in (host, f"127.0.0.1:{PORT}", f"localhost:{PORT}") \
                and not (EMBEDDED[0] and origin == "null"):
            return self.send_json({"error": "forbidden"}, 403)
        length = min(int(self.headers.get("Content-Length") or 0), 10_000)
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            data = {}
        cid = self.client_id()
        if not cid:
            return self.send_json({"ok": False, "error": "missing client id"}, 400)
        path = urlparse(self.path).path
        if path == "/api/start":
            err = start_job(cid, data)
            self.send_json({"ok": err is None, "error": err})
        elif path == "/api/stop":
            get_job(cid).stop()
            self.send_json({"ok": True})
        elif path == "/api/clear":
            get_job(cid).clear()
            self.send_json({"ok": True})
        elif path == "/api/quit":
            if PUBLIC:
                return self.send_json({"ok": False}, 403)
            self.send_json({"ok": True})
            threading.Timer(0.3, shutdown).start()
        elif path == "/api/reset-history":
            if PUBLIC:  # shared data - visitors can't wipe it
                return self.send_json({"ok": False, "error": "Not available on the public site."}, 403)
            self.send_json({"ok": True, "forgot": HISTORY.reset()})
        else:
            self.send_json({"error": "not found"}, 404)


SERVER = [None]
EMBEDDED = [False]   # True when the page lives inside the app's own window (app.py)


def make_server(host, port):
    """Create the HTTP server and start background housekeeping. Caller runs serve_forever()."""
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    SERVER[0] = server
    threading.Thread(target=reaper, daemon=True).start()
    return server


def shutdown():
    with JOBS_LOCK:
        for job in JOBS.values():
            job.stop_evt.set()
    HISTORY.save(force=True)
    if SERVER[0]:
        threading.Thread(target=SERVER[0].shutdown, daemon=True).start()


def main():
    try:
        server = make_server(HOST, PORT)
    except OSError:
        print(f"Port {PORT} is busy - the app may already be running.")
        if not PUBLIC:
            webbrowser.open(f"http://127.0.0.1:{PORT}")
        return
    if PUBLIC:
        print(f"OG Checker (public mode) listening on port {PORT} · max {MAX_NAMES} names per check "
              f"· platforms: {', '.join(ENABLED)}", flush=True)
    else:
        url = f"http://127.0.0.1:{PORT}"
        print(f"OG Checker running at {url}")
        print("Leave this window open while you use the app. Press Ctrl+C to quit.")
        if "--no-browser" not in sys.argv:
            threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        with JOBS_LOCK:
            for job in JOBS.values():
                job.stop_evt.set()
        HISTORY.save(force=True)


if __name__ == "__main__":
    main()
