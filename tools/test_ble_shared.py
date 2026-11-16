"""Bluetooth stress test: this computer sends commands to the socket over Bluetooth while another device
(a phone or tablet with the app open on the socket's page, its Wi-Fi off) uses the same socket at the same
time. Every command must get its own complete answer.

Usage:  python test_ble_shared.py [seconds] [socket id]      (default 60 s, the first SmartSocket-* seen)
Needs:  pip install bleak

Before program 1.9 two devices shared one command slot and every answer went to every device, cut to the
size the last device to connect could take: answers went missing or arrived broken.
"""

import asyncio
import json
import sys
import time

from bleak import BleakClient, BleakScanner

CMD = "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e55"
REPLY = "6b5f9a10-3c2e-4f6a-9d1e-5a1b2c3d4e56"
SECONDS = float(sys.argv[1]) if len(sys.argv) > 1 else 60
WANT = sys.argv[2] if len(sys.argv) > 2 else ""


async def main():
    dev = None
    for _ in range(3):
        found = await BleakScanner.discover(timeout=8)
        dev = next((d for d in found if (d.name or "").startswith("SmartSocket-" + WANT)), None)
        if dev:
            break
    if not dev:
        print("FAIL: no SmartSocket found over Bluetooth")
        return 1
    print("found", dev.name, dev.address)
    buf = bytearray()
    lines = asyncio.Queue()

    def on_reply(_, data):
        buf.extend(data)
        while b"\n" in buf:
            i = buf.index(b"\n")
            lines.put_nowait(bytes(buf[:i]).decode("utf8", "replace").strip())
            del buf[:i + 1]

    ok = bad = lost = foreign = 0
    async with BleakClient(dev, timeout=20) as c:
        print("connected, MTU", c.mtu_size)
        await c.start_notify(REPLY, on_reply)
        end = time.time() + SECONDS
        n = 0
        while time.time() < end:
            cmd = ("full", "info")[n % 2]
            n += 1
            await c.write_gatt_char(CMD, cmd.encode(), response=True)
            got = None
            t0 = time.time()
            while time.time() - t0 < 5:
                try:
                    line = await asyncio.wait_for(lines.get(), 5 - (time.time() - t0))
                except asyncio.TimeoutError:
                    break
                if line.startswith("#" + cmd + " "):
                    got = line
                    break
                foreign += 1  # an answer meant for someone else, or a broken one
            if got is None:
                lost += 1
                continue
            try:
                j = json.loads(got[len(cmd) + 2:])
                ok += 1 if "id" in j else 0
                bad += 0 if "id" in j else 1
            except ValueError:
                bad += 1
            await asyncio.sleep(0.3)
        await c.stop_notify(REPLY)
    total = ok + bad + lost
    print(f"commands {total}: answered whole {ok}, broken {bad}, lost {lost}; other lines seen {foreign}")
    print("PASS" if bad == 0 and lost == 0 and foreign == 0 else "FAIL")
    return 0 if bad == 0 and lost == 0 else 1


sys.exit(asyncio.run(main()))
