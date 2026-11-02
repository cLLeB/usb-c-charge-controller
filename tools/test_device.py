"""Checks a real socket (program 1.5) over Wi-Fi, feature by feature, and puts its settings back.

Usage:  python test_device.py 192.168.1.69
Takes about 90 s (it waits out the one-minute PIN lock). Leaves the socket without a PIN.
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://" + (sys.argv[1] if len(sys.argv) > 1 else "192.168.1.69")
results = []


def call(path, method="GET", **args):
    url = BASE + path + ("?" + urllib.parse.urlencode(args) if args else "")
    req = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
    try:
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read()
        return e.code, json.loads(body) if body.startswith(b"{") else {}


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print("%-58s %s %s" % (name, "PASS" if cond else "FAIL", extra))


code, info = call("/api/info")
check("info answers without a PIN", code == 200 and info.get("fw") == "1.5", info.get("id", ""))
check("own host name smartsocket-xxxxxx.local", info.get("host") == "smartsocket-%s.local" % info.get("id", "").lower(), info.get("host"))

code, st = call("/api/status")
for key in ("reason", "temp_c", "time", "timer_min", "sched_on", "led", "name", "rssi", "pin_set"):
    check("status has " + key, key in st)
check("clock set from the internet", len(st.get("time", "")) == 5, st.get("time"))
check("temperature sensor reads 15 to 70 C", 15 < st.get("temp_c", 0) < 70, str(st.get("temp_c")))
saved = {k: st[k] for k in ("upper", "lower", "mode", "max_ma", "safety_h", "timer_min", "led")}
saved_sched = (st["sched_on"], st["sched_start"], st["sched_end"])
old_name = st["name"]

code, _ = call("/api/identify", "POST")
check("find me (identify) accepted", code == 200)

code, st = call("/api/config", "POST", timer_min=30, led=40, sched_start="09:00", sched_end="09:01", sched_on=1)
check("timer, brightness and hours saved", st.get("timer_min") == 30 and st.get("led") == 40 and st.get("sched_end") == "09:01")
code, st = call("/api/config", "POST", sched_on=0, timer_min=0)

code, i = call("/api/name", "POST", name="Test socket")
check("rename", i.get("name") == "Test socket")

code, h = call("/api/history")
check("history answers", code == 200 and "runs" in h, "%d runs" % len(h.get("runs", [])))

code, _ = call("/api/pin", "POST", new="12")
check("PIN shorter than 4 refused", code == 400)
code, i = call("/api/pin", "POST", new="4321")
check("PIN set", code == 200 and i.get("pin_set") is True)
code, _ = call("/api/status")
check("status without the PIN refused (401)", code == 401)
code, _ = call("/api/status", pin="4321")
check("status with the PIN allowed", code == 200)
code, _ = call("/api/info")
check("info still open (for the app to find it)", code == 200)
for n in range(5):
    code, _ = call("/api/status", pin="0000")
code, j = call("/api/status", pin="4321")
check("five wrong PINs lock it, even for the right PIN (429)", code == 429, str(j))
print("waiting out the one-minute lock...")
time.sleep(62)
code, _ = call("/api/status", pin="4321")
check("unlocked after a minute", code == 200)

code, i = call("/api/name", "POST", pin="4321", name=old_name)
args = dict(saved, pin="4321", sched_on=1 if saved_sched[0] else 0, sched_start=saved_sched[1], sched_end=saved_sched[2])
call("/api/config", "POST", **args)
code, i = call("/api/pin", "POST", pin="4321", new="")
check("PIN removed again", code == 200 and i.get("pin_set") is False)
code, st = call("/api/status")
check("settings put back", st.get("name") == old_name and st.get("led") == saved["led"])

failed = [n for n, ok in results if not ok]
print("\n%d of %d passed" % (len(results) - len(failed), len(results)))
sys.exit(1 if failed else 0)
