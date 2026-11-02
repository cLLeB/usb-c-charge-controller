"""Stand-in for one or more sockets, for trying the web page and the apps without hardware.

Run: python mock_server.py [first_port] [count]      e.g.  python mock_server.py 8765 2
Each socket answers on its own port with the same /api paths as program 1.5, driven by logic.py:
its own ID and name, an optional PIN (with the five-tries lock), timer, charging hours, history.
A pretend phone is plugged into each one: its battery rises while powered and falls while not,
and it reports its level every time it changes by one percent. Time runs 60 times faster than
real life, so a full cycle takes a few minutes. Socket 2 starts with PIN 1234.
"""

import json
import random
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from logic import HIGH_MA, Config, State, clamp_config, report_battery, step

ROOT = Path(__file__).resolve().parent.parent
SPEED = 60
NAMES = ["Bedroom", "Kitchen", "Office", "Living room"]


def hhmm(m):
    return "%02d:%02d" % (m // 60, m % 60)


def parse_hhmm(s, fallback):
    try:
        if ":" in s:
            h, m = s.split(":")
            return int(h) * 60 + int(m)
        return int(s)
    except ValueError:
        return fallback


class Socket:
    def __init__(self, n, port):
        self.id = "%06X" % random.randint(0, 0xFFFFFF)
        self.name = NAMES[n % len(NAMES)]
        self.pin = "1234" if n == 1 else ""
        self.port = port
        self.cfg, self.st = Config(), State()
        self.lock = threading.Lock()
        self.start = time.time()
        self.battery = 62.0 - 20 * n
        self.charge_since = None
        self.led = 100
        self.fails, self.locked_until = 0, 0
        self.runs = []
        self.run_open = None
        self.identify_until = 0

    def now_ms(self):
        return int((time.time() - self.start) * 1000)

    def tick(self):
        with self.lock:
            t = self.now_ms()
            lt = time.localtime()
            was = self.st.vbus
            step(self.st, self.cfg, t, True, HIGH_MA, minute=lt.tm_hour * 60 + lt.tm_min, temp_c=38.0)
            if self.st.vbus and not was:
                self.run_open = {"start": int(time.time()), "clock": 1, "lvl0": self.st.level}
            if was and not self.st.vbus and self.run_open:
                r = self.run_open
                r.update(dur_s=int(time.time()) - r["start"], lvl1=self.st.level, why=self.st.reason)
                self.runs.insert(0, r)
                del self.runs[20:]
                self.run_open = None
            self.battery += (0.5 if self.st.vbus else -0.25) * 0.1 * SPEED / 60
            self.battery = max(1.0, min(100.0, self.battery))
            level = int(self.battery)
            if level != self.st.level:
                report_battery(self.st, self.cfg, level, t)

    def info(self):
        return {"id": self.id, "name": self.name, "fw": "mock 1.5", "pin_set": bool(self.pin),
                "host": "smartsocket-%s.local" % self.id.lower(), "ip": "127.0.0.1:%d" % self.port, "setup": False, "wifi": True}

    def status(self):
        t = self.now_ms()
        c, s = self.cfg, self.st
        self.charge_since = (self.charge_since if self.charge_since is not None else t) if s.vbus else None
        lt = time.localtime()
        left = max(0, c.timer_min * 60000 - s.session_ms) // 1000 if c.timer_min > 0 else -1
        return {
            "id": self.id, "name": self.name, "settled": t >= 5000, "phone": s.present_since >= 0, "charging": s.vbus,
            "reason": s.reason, "allow": s.allow, "boost": s.boost, "mode": c.mode,
            "level": s.level, "level_age_s": -1 if s.report_ms < 0 else (t - s.report_ms) // 1000,
            "upper": c.upper, "lower": c.lower, "max_ma": c.max_ma, "safety_h": c.safety_h,
            "timer_min": c.timer_min, "timer_left_s": left, "sched_on": c.sched_on,
            "sched_start": hhmm(c.sched_start), "sched_end": hhmm(c.sched_end), "time": hhmm(lt.tm_hour * 60 + lt.tm_min),
            "temp_c": 38.0, "hot": s.hot, "led": self.led, "src_ma": HIGH_MA, "out_ma": s.out_ma,
            "charge_s": -1 if self.charge_since is None else (t - self.charge_since) // 1000, "limited": False,
            "uptime_s": t // 1000, "ssid": "mock network", "rssi": -48, "ip": "127.0.0.1:%d" % self.port,
            "host": "smartsocket-%s.local" % self.id.lower(), "pin_set": bool(self.pin), "fw": "mock 1.5",
            "identify": time.time() < self.identify_until,  # stand-in only: the light is flashing for "find me"
        }


def make_handler(sock: Socket):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body, ctype="application/json", code=200):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def authed(self, q):
            if not sock.pin:
                return True
            if time.time() < sock.locked_until:
                self.reply({"error": "locked", "wait_s": int(sock.locked_until - time.time()) + 1}, code=429)
                return False
            if q.get("pin", self.headers.get("X-PIN", "")) == sock.pin:
                sock.fails = 0
                return True
            sock.fails += 1
            if sock.fails >= 5:
                sock.fails, sock.locked_until = 0, time.time() + 60
            self.reply({"error": "pin"}, code=401)
            return False

        def handle_any(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query, keep_blank_values=True).items()}
            p = url.path
            if p == "/":
                return self.reply((ROOT / "web" / "index.html").read_bytes(), "text/html; charset=utf-8")
            if p == "/api/info":
                return self.reply(sock.info())
            if p == "/api/identify":
                sock.identify_until = time.time() + 8
                print("[%s] identify: light flashing" % sock.name)
                return self.reply({"ok": True})
            if p == "/api/setup_status":
                return self.reply({"state": "idle", "ip": "", "host": sock.info()["host"], "reason": 0})
            if p == "/api/scan":
                if not self.authed(q):
                    return None
                return self.reply([{"ssid": "HomeWifi", "rssi": -45, "open": False}, {"ssid": "Neighbour", "rssi": -80, "open": False}])
            if not p.startswith("/api/") or not self.authed(q):
                return None if p.startswith("/api/") else self.send_error(404)
            with sock.lock:
                c = sock.cfg
                if p == "/api/status":
                    return self.reply(sock.status())
                if p == "/api/battery":
                    if "level" not in q:
                        return self.reply({"error": "level is missing"}, code=400)
                    report_battery(sock.st, c, int(q["level"]), sock.now_ms())
                    return self.reply(sock.status())
                if p == "/api/config":
                    for key in ("upper", "lower", "max_ma", "safety_h", "timer_min"):
                        if key in q:
                            setattr(c, key, int(q[key]))
                    if "led" in q:
                        sock.led = max(0, min(100, int(q["led"])))
                    if "mode" in q:
                        c.mode = q["mode"]
                    if "sched_on" in q:
                        c.sched_on = q["sched_on"] in ("1", "true")
                    if "sched_start" in q:
                        c.sched_start = parse_hhmm(q["sched_start"], c.sched_start)
                    if "sched_end" in q:
                        c.sched_end = parse_hhmm(q["sched_end"], c.sched_end)
                    clamp_config(c)
                    return self.reply(sock.status())
                if p == "/api/boost":
                    sock.st.boost = q.get("on") == "1"
                    return self.reply(sock.status())
                if p == "/api/name":
                    if not q.get("name", "").strip():
                        return self.reply({"error": "name is empty"}, code=400)
                    sock.name = q["name"].strip()[:32]
                    return self.reply(sock.info())
                if p == "/api/pin":
                    new = q.get("new", "").strip()
                    if new and not 4 <= len(new) <= 12:
                        return self.reply({"error": "PIN must be 4 to 12 characters"}, code=400)
                    sock.pin = new
                    return self.reply(sock.info())
                if p == "/api/history":
                    return self.reply({"now": int(time.time()), "runs": sock.runs})
                if p == "/api/wifi":
                    return self.reply({"ok": True})
                if p == "/api/update":
                    n = int(self.headers.get("Content-Length") or 0)
                    if n:
                        self.rfile.read(n)
                    return self.reply({"ok": True})
            return self.send_error(404)

        do_GET = do_POST = handle_any

    return Handler


def main():
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    socks = [Socket(n, first + n) for n in range(count)]

    def loop():
        while True:
            time.sleep(0.1)
            for s in socks:
                s.tick()

    threading.Thread(target=loop, daemon=True).start()
    servers = []
    for s in socks:
        srv = ThreadingHTTPServer(("0.0.0.0", s.port), make_handler(s))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        print("Mock socket %-12s ID %s on http://localhost:%d  %s" % (s.name, s.id, s.port, "PIN " + s.pin if s.pin else "no PIN"))
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
