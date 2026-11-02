"""Checks the socket's features on the real board over Wi-Fi, with a device plugged in and charging.

Usage:  python test_features.py 192.168.1.31

Needs a phone or tablet on the socket that reports its battery (the app's "Send this device's battery
level"), so the limit tests have a level to work with. Every setting is put back as it was at the end.
Charging pauses for a few seconds at a time during the test.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://" + sys.argv[1]
results = []


def call(path, method="GET", pin=None, ok_codes=(200,)):
    req = urllib.request.Request(BASE + path, method=method, data=b"" if method == "POST" else None)
    if pin:
        req.add_header("X-PIN", pin)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def status():
    return call("/api/status")[1]


def config(**kv):
    kv = {k: (1 if v else 0) if isinstance(v, bool) else v for k, v in kv.items()}
    return call("/api/config?" + urllib.parse.urlencode(kv), "POST")[1]


def wait_for(pred, secs):
    end = time.time() + secs
    while time.time() < end:
        s = status()
        if pred(s):
            return s
        time.sleep(1)
    return status()


def check(name, cond, detail=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (" - " + detail if detail else ""))


before = status()
keep = {k: before[k] for k in ("upper", "lower", "mode", "max_ma", "safety_h", "timer_min", "sched_on", "led")}
keep["sched_start"], keep["sched_end"] = before["sched_start"], before["sched_end"]
keep["mode"] = before["mode"]
name0 = before["name"]
print("socket", before["id"], "program", before["fw"], "level", before["level"], "charging", before["charging"])
check("device present and charging at start", before["phone"] and before["charging"])
check("battery level known", before["level"] >= 0, str(before["level"]))
level = before["level"]

try:
    # 1. limits: stop below the level, resume above it (applied at once, program 1.7.3)
    s = config(upper=max(20, level - 1), lower=max(5, level - 10))
    s = wait_for(lambda s: not s["charging"], 5)
    check("upper limit below the level stops charging", not s["charging"] and s["reason"] == "limit", s["reason"])
    # 2. boost: charge to 100 % once, past the limit
    call("/api/boost?on=1", "POST")
    s = wait_for(lambda s: s["charging"], 5)
    check("'charge to 100 % once' charges past the limit", s["charging"] and s["boost"])
    call("/api/boost?on=0", "POST")
    s = wait_for(lambda s: not s["charging"], 5)
    check("boost off: back to the limit", not s["charging"] and s["reason"] == "limit", s["reason"])
    # 3. mode always: charges whatever the limit
    config(mode="on")
    s = wait_for(lambda s: s["charging"], 5)
    check("mode 'always' charges past the limit", s["charging"], s["reason"])
    config(mode="auto", upper=keep["upper"], lower=keep["lower"])
    s = wait_for(lambda s: s["charging"], 5)
    check("limit raised again: charging resumes at once", s["charging"], s["reason"])
    # 4. mode never
    config(mode="off")
    s = wait_for(lambda s: not s["charging"], 5)
    check("mode 'never' stops charging", not s["charging"] and s["reason"] == "off", s["reason"])
    config(mode="auto")
    s = wait_for(lambda s: s["charging"], 5)
    check("mode 'use limits' charges again", s["charging"])
    # 5. charging hours: a window that excludes now, then one that includes it
    now = s["time"]
    h, m = map(int, now.split(":"))
    t = h * 60 + m
    fmt = lambda x: "%02d:%02d" % ((x // 60) % 24, x % 60)
    config(sched_on=1, sched_start=fmt(t + 60), sched_end=fmt(t + 120))
    s = wait_for(lambda s: not s["charging"], 5)
    check("charging hours outside the window: paused", not s["charging"] and s["reason"] == "schedule",
          "now %s, window %s-%s, reason %s" % (now, fmt(t + 60), fmt(t + 120), s["reason"]))
    config(sched_start=fmt(t - 30), sched_end=fmt(t + 30))
    s = wait_for(lambda s: s["charging"], 5)
    check("charging hours inside the window: charging", s["charging"], s["reason"])
    config(sched_on=0, sched_start=keep["sched_start"], sched_end=keep["sched_end"])
    # 6. timer: 1 minute (the charging run so far counts, so start a fresh run with an off/on)
    config(mode="off"); wait_for(lambda s: not s["charging"], 5)
    config(mode="auto", timer_min=1)
    s = wait_for(lambda s: s["charging"], 5)
    check("timer set: charging, time left shown", s["charging"] and 0 < s["timer_left_s"] <= 60, str(s["timer_left_s"]))
    s = wait_for(lambda s: not s["charging"], 75)
    check("timer: stops after 1 minute", not s["charging"] and s["reason"] == "timer", s["reason"])
    config(timer_min=keep["timer_min"])
    s = wait_for(lambda s: s["charging"], 5)
    check("timer off: charging again", s["charging"], s["reason"])
    # 7. current setting reaches the device's CC line
    for ma in (500, 1500, 3000):
        s = config(max_ma=ma)
        time.sleep(1.5)
        s = status()
        check("most current %d mA: offered %d mA" % (ma, s["out_ma"]), s["out_ma"] == min(ma, s["src_ma"]))
    config(max_ma=keep["max_ma"])
    # 8. light brightness and find me
    s = config(led=30)
    check("light brightness setting", s["led"] == 30)
    config(led=keep["led"])
    c, j = call("/api/identify", "POST")
    check("find me", c == 200 and j.get("ok"))
    # 9. rename
    call("/api/name?name=" + urllib.parse.quote("Test socket"), "POST")
    c, j = call("/api/info")
    check("rename", j.get("name") == "Test socket", j.get("name"))
    call("/api/name?name=" + urllib.parse.quote(name0), "POST")
    # 10. PIN: set, refused without it, accepted with it, removed
    c, _ = call("/api/pin?new=4826", "POST")
    c1, _ = call("/api/status")
    c2, _ = call("/api/status", pin="4826")
    c3, _ = call("/api/status", pin="1111")
    call("/api/pin?new=", "POST", pin="4826")
    c4, _ = call("/api/status")
    check("PIN: refused without it, wrong one refused, right one accepted, removed",
          (c, c1, c2, c3, c4) == (200, 401, 200, 401, 200), str((c, c1, c2, c3, c4)))
    # 11. history, networks, scan
    c, j = call("/api/history")
    whys = {r["why"] for r in j.get("runs", [])}
    check("history logs runs with reasons", c == 200 and {"limit", "off", "timer"} <= whys, ",".join(sorted(whys)))
    c, j = call("/api/nets")
    check("saved networks listed", c == 200 and len(j.get("saved", [])) >= 1, str([n.get("ssid") for n in j.get("saved", [])]))
    call("/api/scan")
    time.sleep(6)
    c, j = call("/api/scan")
    n = j if isinstance(j, list) else j.get("networks", j.get("nets", []))
    check("Wi-Fi scan", c == 200 and len(n) >= 1, "%d networks" % len(n))
    # 12. temperature
    s = status()
    check("controller temperature sensible", 15 < s["temp_c"] < 70 and not s["hot"], "%.1f C" % s["temp_c"])
finally:
    config(**keep)
    call("/api/boost?on=0", "POST")
    call("/api/name?name=" + urllib.parse.quote(name0), "POST")
    after = status()
    restored = all(str(after[k]) == str(v) for k, v in keep.items())
    print("settings restored:", restored, {k: after[k] for k in keep})

print("\n%d of %d passed" % (sum(r for _, r in results), len(results)))
