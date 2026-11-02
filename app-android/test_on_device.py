"""Drives the Smart Socket app on a connected Android device (adb) and checks each action on the socket.

Usage:  python test_on_device.py <adb serial> <socket ip>
The app must already list the socket. Taps are found by on-screen text (uiautomator), so the
test does not depend on the screen size. Leaves the socket without a PIN and with its old name.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ADB = os.path.join(os.environ["LOCALAPPDATA"], "Android", "Sdk", "platform-tools", "adb.exe")
SERIAL, IP = sys.argv[1], sys.argv[2]
results = []


def adb(*args):
    return subprocess.run([ADB, "-s", SERIAL, *args], capture_output=True, text=True, encoding="utf-8").stdout


def screen():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    return adb("shell", "cat", "/sdcard/ui.xml")


def tap(text, contains=True, scroll=True):
    for attempt in range(6):
        xml = screen()
        for m in re.finditer(r'<node [^>]*?text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', xml):
            t = m.group(1).replace("&quot;", '"').replace("&amp;", "&").replace("&#39;", "'")
            if (text in t) if contains else (t == text):
                x1, y1, x2, y2 = map(int, m.groups()[1:])
                adb("shell", "input", "tap", str((x1 + x2) // 2), str((y1 + y2) // 2))
                time.sleep(1.5)
                return True
        if not scroll:
            break
        adb("shell", "input", "swipe", "600", "1500", "600", "700", "300")
        time.sleep(0.8)
    return False


def scroll_top():
    for _ in range(4):
        adb("shell", "input", "swipe", "600", "500", "600", "1700", "200")
    time.sleep(0.5)


def status(pin=None):
    url = "http://%s/api/status" % IP + ("?pin=" + pin if pin else "")
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print("%-62s %s %s" % (name, "PASS" if cond else "FAIL", extra))


def typing(text):
    adb("shell", "input", "text", text.replace(" ", "%s"))
    time.sleep(0.5)


_, st0 = status()
old_name = st0.get("name", "")
adb("logcat", "-c")
adb("shell", "am", "force-stop", "org.smartsocket.app")
adb("shell", "am", "start", "-n", "org.smartsocket.app/.MainActivity")
time.sleep(5)
check("home screen lists the socket", old_name in screen(), old_name)
check("opened the socket's page", tap(old_name, scroll=False))
time.sleep(3)
check("page shows the live state", "Charging" in screen() or "Paused" in screen() or "No device" in screen())

# Battery reports from this device
tap("Send this device's battery level to this socket")
time.sleep(6)
level = int(re.search(r"level: (\d+)", adb("shell", "dumpsys", "battery")).group(1))
_, st = status()
check("this device's battery level reached the socket", st.get("level") == level, "%s vs %s" % (st.get("level"), level))
check("background reporter is running", "ReporterService" in adb("shell", "dumpsys", "activity", "services", "org.smartsocket.app"))

# Mode buttons
scroll_top()
tap("Always", contains=False)
time.sleep(1.5)
check("'Always' sets the mode on the socket", status()[1].get("mode") == "on")
tap("Use limits", contains=False)
time.sleep(1.5)
check("'Use limits' sets it back", status()[1].get("mode") == "auto")

# Timer switch
tap("Use the timer")
time.sleep(2)
check("timer switch turns the timer on", status()[1].get("timer_min", 0) > 0, str(status()[1].get("timer_min")))
tap("Use the timer")
time.sleep(2)
check("and off again", status()[1].get("timer_min") == 0)

# Find
scroll_top()
check("Find button present and tapped", tap("Find", contains=False, scroll=False))

# Rename
scroll_top()
tap(old_name, contains=False, scroll=False)
typing("Tablet test")
tap("Save", contains=False, scroll=False)
time.sleep(2)
check("rename from the app reaches the socket", status()[1].get("name") == "Tablet test")

# PIN set from the app, then the app keeps working with it
tap("Set or change the PIN")
typing("2468")
tap("Save", contains=False, scroll=False)
time.sleep(2)
code, _ = status()
check("PIN set from the app: socket refuses requests without it", code == 401)
code, _ = status("2468")
check("socket accepts the PIN", code == 200)
scroll_top()
time.sleep(3)
check("the app still shows the state (it saved the PIN)", "Not answering" not in screen() and "PIN" not in screen().split("Charge timer")[0])

# History
check("history opens", tap("Show the last charging runs") and ("Charging history" in screen()))
tap("Close", contains=False, scroll=False)

# Put things back: name, no PIN, reports off
tap("Rename")
typing(old_name)
tap("Save", contains=False, scroll=False)
tap("Set or change the PIN")
tap("Save", contains=False, scroll=False)
time.sleep(2)
code, st = status()
check("PIN removed and name restored from the app", code == 200 and st.get("name") == old_name, st.get("name", ""))

crash = adb("logcat", "-d", "-s", "AndroidRuntime:E")
check("no crash in the app's log", "FATAL" not in crash)
failed = [n for n, ok in results if not ok]
print("\n%d of %d passed" % (len(results) - len(failed), len(results)))
