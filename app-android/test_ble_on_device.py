"""Checks the app's Bluetooth link on a connected Android device (adb), where the device's Wi-Fi cannot reach the socket.

Usage:
  python test_ble_on_device.py <adb serial> [--name "Bedroom"] [--wifi-off] [--check-com COM5 | --check-ip 192.168.1.69] [--pin 2468]

  --name       the socket's name as the app shows it (default: the first socket shown "via Bluetooth")
  --wifi-off   turn the device's Wi-Fi off during the test (and back on after), so it surely cannot reach
               the socket over Wi-Fi. Without it, put the device on a network that keeps devices apart
               (or any network other than the socket's).
  --check-com  read the socket's mode independently over its USB serial port ("full" command; needs pyserial)
  --check-ip   or over HTTP from this computer (the computer must reach the socket)
  --pin        the socket's PIN, for --check-ip

Needs: the socket with program 1.6 powered and near the device, Bluetooth on the device (the test tries to
turn it on), and the app (1.6 or later) installed. If the socket has a PIN, the app must already know it.
Without --check-com/--check-ip the mode is read back through the app (the page footer shows the mode the
socket reported over Bluetooth), which still proves the round trip but not independently.
Leaves the socket's mode as it was.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

ADB = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Android", "Sdk", "platform-tools", "adb.exe")
if not os.path.exists(ADB):
    ADB = "adb"
PKG = "org.smartsocket.app"

ap = argparse.ArgumentParser()
ap.add_argument("serial")
ap.add_argument("--name")
ap.add_argument("--wifi-off", action="store_true")
ap.add_argument("--check-com")
ap.add_argument("--check-ip")
ap.add_argument("--pin", default="")
opt = ap.parse_args()
results = []


def adb(*args):
    return subprocess.run([ADB, "-s", opt.serial, *args], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def screen():
    for _ in range(3):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb("shell", "cat", "/sdcard/ui.xml")
        if "<hierarchy" in xml:
            return xml.replace("&#10;", "\n").replace("&quot;", '"').replace("&amp;", "&").replace("&#39;", "'")
        time.sleep(1)
    return ""


NODE = re.compile(r'<node [^>]*?text="([^"]*)"[^>]*?bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')


def find(pred, xml=None):
    for m in NODE.finditer(xml if xml is not None else screen()):
        if pred(m.group(1)):
            x1, y1, x2, y2 = map(int, m.groups()[1:])
            return (x1 + x2) // 2, (y1 + y2) // 2, m.group(1)
    return None


def tap(text, contains=True, scroll=True):
    for _ in range(6):
        hit = find(lambda t: (text in t) if contains else (t == text))
        if hit:
            adb("shell", "input", "tap", str(hit[0]), str(hit[1]))
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


def scroll_bottom():
    for _ in range(8):
        adb("shell", "input", "swipe", "600", "1600", "600", "400", "200")
    time.sleep(0.5)


def wait_for(text, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if text in screen():
            return True
        time.sleep(2)
    return False


def check(name, cond, extra=""):
    results.append((name, bool(cond)))
    print("%-66s %s %s" % (name, "PASS" if cond else "FAIL", extra))


# ------------------------------------------------------------------ reading the socket's mode
def mode_from_socket():
    """The socket's own mode, read without the phone; None if no independent way was given."""
    if opt.check_com:
        import serial  # pip install pyserial
        with serial.Serial(opt.check_com, 115200, timeout=1) as s:
            time.sleep(0.3)
            s.reset_input_buffer()
            s.write(b"full\n")
            end = time.time() + 4
            while time.time() < end:
                line = s.readline().decode("utf-8", "replace").strip()
                if line.startswith("#full "):
                    return json.loads(line[6:]).get("mode")
        return "?"
    if opt.check_ip:
        url = "http://%s/api/status" % opt.check_ip + ("?pin=" + opt.pin if opt.pin else "")
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                return json.loads(r.read()).get("mode")
        except (urllib.error.URLError, OSError):
            return "?"
    return None


MODE_WORD = {"on": "always", "off": "never", "auto": "use limits"}
MODE_BUTTON = {"on": "Always", "off": "Never", "auto": "Use limits"}


def mode_from_app(seconds=12):
    """The mode the app last got from the socket (page footer "mode: ..."), polled until it settles."""
    scroll_bottom()
    m = re.search(r"mode: (always|never|use limits)", screen())
    return {v: k for k, v in MODE_WORD.items()}.get(m.group(1)) if m else None


def wait_mode(want, seconds=15):
    end = time.time() + seconds
    got_app = got_sock = None
    while time.time() < end:
        got_app = mode_from_app()
        got_sock = mode_from_socket()
        if got_app == want and got_sock in (None, want):
            break
        time.sleep(2)
    return got_app, got_sock


# ------------------------------------------------------------------ setting the device up
sdk = int((adb("shell", "getprop", "ro.build.version.sdk").strip() or "0"))
perms = ["android.permission.BLUETOOTH_SCAN", "android.permission.BLUETOOTH_CONNECT"] if sdk >= 31 else ["android.permission.ACCESS_FINE_LOCATION"]
if sdk >= 33:
    perms.append("android.permission.POST_NOTIFICATIONS")
for p in perms:
    adb("shell", "pm", "grant", PKG, p)
granted = adb("shell", "dumpsys", "package", PKG)
check("Bluetooth permissions granted to the app", all(re.search(re.escape(p) + r": granted=true", granted) for p in perms[:2 if sdk >= 31 else 1]))

adb("shell", "cmd", "bluetooth_manager", "enable")
adb("shell", "svc", "bluetooth", "enable")
time.sleep(2)
bt_on = "enabled: true" in adb("shell", "dumpsys", "bluetooth_manager").lower() or adb("shell", "settings", "get", "global", "bluetooth_on").strip() == "1"
check("Bluetooth is on", bt_on)

wifi_was_on = "Wi-Fi is enabled" in adb("shell", "dumpsys", "wifi")
if opt.wifi_off:
    adb("shell", "svc", "wifi", "disable")
    time.sleep(3)
    check("device Wi-Fi turned off for the test", "Wi-Fi is disabled" in adb("shell", "dumpsys", "wifi"))
elif opt.check_ip:
    reach = adb("shell", "ping", "-c", "1", "-W", "2", opt.check_ip)
    check("precondition: the device cannot reach the socket over Wi-Fi", " 0% packet loss" not in reach,
          "(it can: use --wifi-off or another network)" if " 0% packet loss" in reach else "")

mode0 = None
try:
    # ------------------------------------------------------------------ home screen
    adb("logcat", "-c")
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", PKG + "/.MainActivity")
    end = time.time() + 45
    card = None
    while time.time() < end and not card:
        xml = screen()
        if opt.name:
            if opt.name in xml and find(lambda t: "via Bluetooth" in t, xml):
                card = find(lambda t: t == opt.name, xml)
        else:
            card = find(lambda t: "via Bluetooth" in t, xml)
        time.sleep(2)
    check("home screen lists the socket 'via Bluetooth'", card, card[2].replace("\n", " ") if card else "")
    if not card:
        print("Is the socket powered, near the device, and with program 1.6? Stopping here.")
        raise SystemExit

    # ------------------------------------------------------------------ the socket's page
    adb("shell", "input", "tap", str(card[0]), str(card[1]))
    time.sleep(2)
    check("socket page shows 'via Bluetooth'", wait_for("via Bluetooth", 40))
    xml = screen()
    check("page shows the live state", any(w in xml for w in ("Charging", "Paused", "No device", "Never charge", "Starting")))
    scroll_bottom()
    check("program update is disabled with a note on Bluetooth", "Update its program (needs Wi-Fi)" in screen())

    mode0 = mode_from_app()
    sock0 = mode_from_socket()
    check("the app read the mode over Bluetooth", mode0 in MODE_WORD, "%s (socket: %s)" % (mode0, sock0))
    if mode0 not in MODE_WORD:
        raise SystemExit
    target = "off" if mode0 == "on" else "on"

    # ------------------------------------------------------------------ a mode change over Bluetooth
    scroll_top()
    check("tapped '%s'" % MODE_BUTTON[target], tap(MODE_BUTTON[target], contains=False))
    app_mode, sock_mode = wait_mode(target)
    check("mode change reached the socket (as the socket reports it back to the app)", app_mode == target, str(app_mode))
    if sock_mode is not None:
        check("mode change reached the socket (read independently: %s)" % ("USB" if opt.check_com else "HTTP"), sock_mode == target, str(sock_mode))

    scroll_top()
    tap(MODE_BUTTON[mode0], contains=False)
    app_mode, sock_mode = wait_mode(mode0)
    check("mode set back to '%s' over Bluetooth" % MODE_WORD[mode0], app_mode == mode0 and sock_mode in (None, mode0), "%s / %s" % (app_mode, sock_mode))

    # ------------------------------------------------------------------ Wi-Fi networks screen over Bluetooth
    scroll_bottom()
    if tap("Wi-Fi networks it knows"):
        ok = wait_for("Networks it knows", 5) and (wait_for("via Bluetooth", 20))
        xml = screen()
        check("Wi-Fi networks screen answers over Bluetooth", ok and ("In use" in xml or "Known" in xml or "None yet" in xml))
        adb("shell", "input", "keyevent", "4")
        time.sleep(1)
finally:
    if opt.wifi_off and wifi_was_on:
        adb("shell", "svc", "wifi", "enable")
    crash = adb("logcat", "-d", "-s", "AndroidRuntime:E")
    check("no crash in the app's log", "FATAL" not in crash)
    failed = [n for n, ok in results if not ok]
    print("\n%d of %d passed" % (len(results) - len(failed), len(results)))
    sys.exit(1 if failed else 0)
