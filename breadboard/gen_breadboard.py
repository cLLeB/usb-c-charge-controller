"""Breadboard layout for the smart charge switch (stage 1 proof build).

Run: python gen_breadboard.py
The script checks that the placement below gives exactly the connections of the circuit
(EXPECTED, taken from ../DESIGN.md), then writes breadboard-layout.svg and ../BREADBOARD-GUIDE.md.
Do not edit the guide by hand: change this file and run it again.

Board: 830-hole breadboard. Columns a-e are joined in each row, f-j likewise; the long rails
down each side are L+, L-, R-, R+.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROWS = 63
RAILS = {"L+": "5 V from the charger", "L-": "GND", "R+": "5 V from the charger", "R-": "GND"}

# Pads of the USB-C socket board J1, in the order they sit along its edge, first pad in a3.
# Read from the labels printed on the real board (photo of 2026-10-12), GND end first.
J1_ORDER = ["GND", "VBUS", "SBU2", "CC1", "D-", "D+", "SBU1", "CC2"]
J1_TOP = 3


def j1_row(name):
    return J1_TOP + J1_ORDER.index(name)

XIAO_LEFT = ["D0", "D1", "D2", "D3", "D4", "D5", "D6"]        # USB end first
XIAO_RIGHT = ["5V", "GND", "3V3", "D10", "D9", "D8", "D7"]
U1_TOP = 20

# stage, reference, value, what it is, {pin: hole}, note
PARTS = [
    (1, "J1", "USB-C socket board", "module",
     {"J1.%s" % n: "a%d" % (J1_TOP + i) for i, n in enumerate(J1_ORDER)},
     "pins in column a, the board hangs out over the left edge. Rows follow the printed labels"),
    (1, "C3", "10 uF", "electrolytic", {"C3.1": "L+", "C3.2": "L-"},
     "plugs into the two left-hand rails, long leg (+) in L+"),
    (2, "U1", "XIAO ESP32-C3", "module",
     dict([("U1.%s" % n, "d%d" % (U1_TOP + i)) for i, n in enumerate(XIAO_LEFT)] +
          [("U1.%s" % n, "h%d" % (U1_TOP + i)) for i, n in enumerate(XIAO_RIGHT)]),
     "USB-C port toward row 1. Left pins in column d, right pins in column h"),
    (2, "D1", "1N5817", "diode", {"D1.A": "R+", "D1.K": "j20"}, "band end in j20"),
    (3, "R7", "100 k", "resistor", {"R7.1": "c%d" % j1_row("CC1"), "R7.2": "c12"}, ""),
    (3, "R8", "100 k", "resistor", {"R8.1": "d%d" % j1_row("CC2"), "R8.2": "d12"}, "stand it on end if the rows are close"),
    (3, "C1", "100 nF", "capacitor", {"C1.1": "a21", "C1.2": "L-"}, "spread the legs to reach the rail"),
    (4, "Q1", "SI4435 on its adapter", "module",
     {"Q1.1": "e30", "Q1.2": "e31", "Q1.3": "e32", "Q1.4": "e33",
      "Q1.8": "f30", "Q1.7": "f31", "Q1.6": "f32", "Q1.5": "f33"},
     "across the centre gap, pin 1 (dot on the chip) at e30"),
    (4, "R1", "100 k", "resistor", {"R1.1": "L+", "R1.2": "a33"}, ""),
    (4, "Q2", "2N7000", "MOSFET", {"Q2.1": "j34", "Q2.2": "j35", "Q2.3": "j36"},
     "flat face toward the centre gap"),
    (4, "R2", "100 k", "resistor", {"R2.1": "h35", "R2.2": "h34"}, "stand it on end"),
    (5, "R3", "33 k", "resistor", {"R3.1": "b23", "R3.2": "b28"}, ""),
    (5, "R6", "100 k", "resistor", {"R6.1": "d28", "R6.2": "d29"}, "stand it on end"),
    (5, "C2", "100 nF", "capacitor", {"C2.1": "b29", "C2.2": "L-"}, "spread the legs to reach the rail"),
    (5, "R10", "1 k", "resistor", {"R10.1": "i30", "R10.2": "R-"}, ""),
    (6, "R9", "680", "resistor", {"R9.1": "i26", "R9.2": "i29"}, ""),
    (6, "LED", "red LED", "LED", {"LED.A": "j29", "LED.K": "R-"}, "long leg in j29, short leg in the rail"),
    (7, "R4", "12 k", "resistor", {"R4.1": "c24", "R4.2": "c28"}, "NOT on the breadboard: soldered build only"),
    (7, "R5", "4.7 k", "resistor", {"R5.1": "a25", "R5.2": "a28"}, "NOT on the breadboard: soldered build only"),
]

# stage, from, to, colour hint, what it carries
WIRES = [
    (1, "L+", "R+", "red", "joins the two 5 V rails (at row 61)"),
    (1, "L-", "R-", "black", "joins the two ground rails (at row 62)"),
    (1, "b%d" % j1_row("VBUS"), "L+", "red", "charger 5 V to the rail"),
    (1, "b%d" % j1_row("GND"), "L-", "black", "charger ground to the rail"),
    (2, "j21", "R-", "black", "U1 ground"),
    (3, "e12", "b21", "any", "charger-offer reading to U1 pin D1"),
    (4, "a30", "L+", "red", "5 V to the power switch"),
    (4, "g36", "c33", "any", "Q2 to the gate of Q1. Crosses the centre gap"),
    (4, "i23", "i35", "any", "U1 pin D10 to Q2"),
    (4, "i34", "R-", "black", "Q2 ground"),
    (5, "a22", "a29", "any", "phone reading to U1 pin D2. Lay it outside column a"),
]

# The cut cable to the phone. Each wire is soldered to a single header pin first.
EXTERNAL = {"J2.VBUS": "j30", "J2.CC": "e28", "J2.GND": "R-"}

# Pins joined inside a part: the SI4435 has three source pins and four drain pins.
INTERNAL = [("e30", "e31"), ("e31", "e32"), ("f30", "f31"), ("f31", "f32"), ("f32", "f33")]

EXPECTED = {
    "5V_IN": ["J1.VBUS", "C3.1", "D1.A", "Q1.1", "Q1.2", "Q1.3", "R1.1"],
    "GND": ["J1.GND", "C3.2", "U1.GND", "C1.2", "C2.2", "Q2.1", "R2.2", "R10.2", "LED.K", "J2.GND"],
    "5V_U1": ["D1.K", "U1.5V"],
    "CC1_IN": ["J1.CC1", "R7.1"],
    "CC2_IN": ["J1.CC2", "R8.1"],
    "OFFER": ["R7.2", "R8.2", "C1.1", "U1.D1"],
    "RP_BASIC": ["U1.D3", "R3.1"],
    "RP_MID": ["U1.D4", "R4.1"],
    "RP_HIGH": ["U1.D5", "R5.1"],
    "CC_PHONE": ["R3.2", "R4.2", "R5.2", "R6.1", "J2.CC"],
    "PHONE_SENSE": ["R6.2", "C2.1", "U1.D2"],
    "GATE": ["Q1.4", "R1.2", "Q2.3"],
    "POWER_ON": ["U1.D10", "Q2.2", "R2.1"],
    "5V_PHONE": ["Q1.5", "Q1.6", "Q1.7", "Q1.8", "R10.1", "J2.VBUS"],
    "LED_DRIVE": ["U1.D7", "R9.1"],
    "LED_A": ["R9.2", "LED.A"],
}
# Module pins that must stay unconnected.
UNUSED = ["U1.D0", "U1.D6", "U1.3V3", "U1.D9", "U1.D8", "J1.D+", "J1.D-", "J1.SBU1", "J1.SBU2"]


def node_of(hole):
    if hole in RAILS:
        return hole
    col, row = hole[0], int(hole[1:])
    if col not in "abcdefghij" or not 1 <= row <= ROWS:
        raise SystemExit("bad hole " + hole)
    return ("L" if col in "abcde" else "R") + str(row)


def build():
    pin_hole, used = {}, {}
    for stage, ref, value, kind, legs, note in PARTS:
        pin_hole.update(legs)
    pin_hole.update(EXTERNAL)
    entries = list(pin_hole.items())
    for i, (stage, a, b, colour, what) in enumerate(WIRES):
        entries += [("wire%d" % i, a), ("wire%d'" % i, b)]
    for who, hole in entries:
        if hole in RAILS:
            continue
        node_of(hole)
        if hole in used:
            raise SystemExit("hole %s used by %s and %s" % (hole, used[hole], who))
        used[hole] = who

    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in [(w[1], w[2]) for w in WIRES] + INTERNAL:
        parent[find(node_of(a))] = find(node_of(b))
    groups = {}
    for pin, hole in pin_hole.items():
        groups.setdefault(find(node_of(hole)), set()).add(pin)
    return pin_hole, list(groups.values()), used


def check(groups):
    errors = []
    for net, pins in sorted(EXPECTED.items()):
        want = set(pins)
        match = [g for g in groups if g & want]
        if len(match) != 1 or match[0] != want:
            got = sorted(set().union(*match)) if match else []
            errors.append("%s: want %s, breadboard gives %s" % (net, sorted(want), got))
    for pin in UNUSED:
        g = [x for x in groups if pin in x]
        if not g or len(g[0]) != 1:
            errors.append("pin that must stay free is connected or missing: " + pin)
    placed = set().union(*groups)
    wanted = set(UNUSED).union(*[set(v) for v in EXPECTED.values()])
    if placed != wanted:
        errors.append("pins placed but not in the circuit, or the reverse: %s" % sorted(placed ^ wanted))
    return errors


# ---------------------------------------------------------------- picture

P = 12.0
X0, Y0 = 170.0, 96.0
COLX = {c: X0 + i * P for i, c in enumerate("abcde")}
COLX.update({c: X0 + (7 + i) * P for i, c in enumerate("fghij")})
RAILX = {"L+": X0 - 4 * P, "L-": X0 - 3 * P, "R-": X0 + 14 * P, "R+": X0 + 15 * P}
WIRE_COL = {"red": "#d22", "black": "#222", "any": "#27c"}
RAIL_ROW = {0: 61, 1: 62}   # the two rail-to-rail links


def row_of(hole):
    return None if hole in RAILS else int(hole[1:])


def xy(hole, near_row):
    if hole in RAILS:
        return RAILX[hole], Y0 + (near_row - 1) * P
    return COLX[hole[0]], Y0 + (int(hole[1:]) - 1) * P


def ends(a, b, fallback):
    row = row_of(a) or row_of(b) or fallback
    return xy(a, row), xy(b, row)


def svg():
    w, h = X0 + 15 * P + 400, Y0 + ROWS * P + 50
    o = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
         'font-family="Arial" font-size="9">' % (w, h, w, h),
         '<rect width="100%" height="100%" fill="#fff"/>',
         '<text x="20" y="28" font-size="16" font-weight="bold">Smart charge switch: breadboard layout</text>',
         '<text x="20" y="44" font-size="10" fill="#555">Proof build, basic current only. '
         'Row 1 at the top. J1 pad order read from the real board.</text>',
         '<rect x="%g" y="%g" width="%g" height="%g" rx="6" fill="#f7f4ea" stroke="#bbb"/>'
         % (X0 - 5.5 * P, Y0 - 1.5 * P, 22 * P, (ROWS + 2) * P)]
    for rail, x in RAILX.items():
        col = "#d22" if rail.endswith("+") else "#222"
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="%s" stroke-width="0.6" opacity="0.5"/>'
                 % (x, Y0 - P, x, Y0 + ROWS * P, col))
        o.append('<text x="%g" y="%g" text-anchor="middle" fill="%s">%s</text>' % (x, Y0 - 1.9 * P, col, rail))
    for c, x in COLX.items():
        o.append('<text x="%g" y="%g" text-anchor="middle" fill="#777">%s</text>' % (x, Y0 - 1.9 * P, c))
    for r in range(1, ROWS + 1):
        y = Y0 + (r - 1) * P
        if r == 1 or r % 5 == 0:
            o.append('<text x="%g" y="%g" text-anchor="end" fill="#777">%d</text>' % (X0 - 5 * P - 8, y + 3, r))
        for x in list(COLX.values()) + list(RAILX.values()):
            o.append('<circle cx="%g" cy="%g" r="1.3" fill="#c9c4b3"/>' % (x, y))

    def module(ref, legs, fill, extra_left=0.0):
        xs = [xy(hh, 1)[0] for hh in legs.values()]
        ys = [xy(hh, 1)[1] for hh in legs.values()]
        x0, x1, y0, y1 = min(xs) - 5 - extra_left, max(xs) + 5, min(ys) - 5, max(ys) + 5
        o.append('<rect x="%g" y="%g" width="%g" height="%g" rx="3" fill="%s" opacity="0.88" stroke="#222"/>'
                 % (x0, y0, x1 - x0, y1 - y0, fill))
        o.append('<text x="%g" y="%g" text-anchor="middle" fill="#fff" font-weight="bold">%s</text>'
                 % ((x0 + x1) / 2, (y0 + y1) / 2 + 3, ref))

    body = {"resistor": "#d8c08a", "capacitor": "#d8a637", "electrolytic": "#1d2f6f", "diode": "#333",
            "LED": "#e0271b", "MOSFET": "#222"}
    for stage, ref, value, kind, legs, note in PARTS:
        ghost = stage == 7
        if kind == "module":
            if ref == "J1":
                module(ref, legs, "#1f4fa8", extra_left=5 * P)
            else:
                module(ref, legs, "#16181c" if ref == "U1" else "#2f7d4f")
            for pin, hole in legs.items():
                x, y = xy(hole, 1)
                o.append('<circle cx="%g" cy="%g" r="2" fill="#eee" stroke="#222" stroke-width="0.5"/>' % (x, y))
            continue
        holes = list(legs.values())
        if len(holes) == 3:
            pts = [xy(hh, 1) for hh in holes]
            o.append('<rect x="%g" y="%g" width="11" height="%g" rx="2" fill="#222"/>'
                     % (pts[0][0] - 5.5, pts[0][1] - 5, pts[2][1] - pts[0][1] + 10))
            o.append('<text x="%g" y="%g" fill="#111">%s</text>' % (pts[0][0] + 9, pts[1][1] + 3, ref))
            continue
        (x1, y1), (x2, y2) = ends(holes[0], holes[1], 50 if ref == "C3" else 1)
        dash = ' stroke-dasharray="3 2" opacity="0.55"' if ghost else ""
        o.append('<line x1="%g" y1="%g" x2="%g" y2="%g" stroke="#666" stroke-width="1.2"%s/>' % (x1, y1, x2, y2, dash))
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        o.append('<circle cx="%g" cy="%g" r="4.2" fill="%s" stroke="#444" stroke-width="0.5"%s/>'
                 % (mx, my, body.get(kind, "#999"), ' opacity="0.5"' if ghost else ""))
        o.append('<text x="%g" y="%g" fill="#111">%s</text>' % (mx + 6, my - 4, ref))
    for i, (stage, a, b, colour, what) in enumerate(WIRES):
        (x1, y1), (x2, y2) = ends(a, b, RAIL_ROW.get(i, 60))
        bend = 0 if abs(x1 - x2) > 1 or abs(y1 - y2) < 2 * P else -1.4 * P
        o.append('<path d="M%g %g Q%g %g %g %g" stroke="%s" stroke-width="2.2" fill="none" stroke-linecap="round"/>'
                 % (x1, y1, (x1 + x2) / 2 + bend, (y1 + y2) / 2, x2, y2, WIRE_COL[colour]))
    for pin, hole in EXTERNAL.items():
        x, y = xy(hole, 32)
        o.append('<circle cx="%g" cy="%g" r="3.2" fill="#fff" stroke="#b0b" stroke-width="1.6"/>' % (x, y))
        o.append('<text x="%g" y="%g" fill="#b0b">%s</text>' % (x + 8, y + 12, pin.replace("J2.", "cable ")))
    lx, ly = X0 + 17 * P + 20, Y0 + 10
    notes = ["Blue block: J1, USB-C socket board (from the charger)", "Black block: U1, the controller",
             "Green block: Q1 on its adapter board", "Purple rings: the three wires of the cut cable (to the phone)",
             "Red wire: 5 V.  Black wire: ground.  Blue wire: signal",
             "Dashed parts (R4, R5): left out on the breadboard", "", "Stage 1 rows 3 to 10: charger side",
             "Stage 2 rows 20 to 26: controller", "Stage 3 rows 6 to 21: charger-offer reading",
             "Stage 4 rows 30 to 36: power switch", "Stage 5 rows 22 to 30: phone side",
             "Stage 6 rows 26 to 29: light"]
    for i, t in enumerate(notes):
        o.append('<text x="%g" y="%g" font-size="10" fill="#333">%s</text>' % (lx, ly + i * 15, t))
    o.append("</svg>")
    return "\n".join(o)


# ---------------------------------------------------------------- guide

STAGES = {
    1: ("Charger side", [
        "Before plugging anything in: multimeter on resistance, J1 pad CC1 to GND and CC2 to GND. "
        "Each should read about 5.1 k. If they read open, add a 5.1 k resistor from each to ground.",
        "Plug the charger into J1 with a USB-C cable. Rail L+ to rail L- should read 4.75 to 5.25 V.",
        "Unplug the charger before the next stage."]),
    2: ("Controller", [
        "Load the program onto U1 from the computer first, with the charger unplugged.",
        "Charger plugged in, computer unplugged: hole j20 to ground reads 4.4 to 5.0 V, hole j22 (3V3) reads 3.2 to 3.4 V.",
        "A phone or computer sees a Wi-Fi network called SmartSocket-setup."]),
    3: ("Charger-offer reading", [
        "Hole b21 to ground reads 0.12 to 0.31 V (basic charger), 0.35 to 0.58 V (1.5 A) or 0.65 to 1.02 V (3 A).",
        "The Details section of the page shows the same thing in words."]),
    4: ("Power switch", [
        "Nothing on the phone side yet: hole j30 to ground reads about 0 V, hole a33 (gate) reads about 5 V.",
        "On the page choose 'Always charge'. Still 0 V at j30, because no phone is detected."]),
    5: ("Phone side", [
        "No phone: hole e28 to ground reads about 3.3 V.",
        "Pretend phone: push a 5.1 k resistor between e28 and ground. e28 drops to 0.25 to 0.61 V, "
        "and within a second j30 reads about 5 V. Pull the resistor out: j30 falls back to 0 V.",
        "Real phone on the cut cable: the phone shows it is charging. Choose 'Never charge': it stops."]),
    6: ("Light", [
        "Light on while charging, slow blink while paused, off with no phone."]),
}

FINAL = [
    "Limits: open http://smartsocket.local/api/battery?level=80 in a browser. Charging stops. "
    "Open the same address with level=40. Charging starts.",
    "Memory: while paused, unplug the charger for ten seconds and plug it back. After about 5 seconds it is still paused.",
    "Unplug the phone while charging: j30 falls to 0 V at once.",
    "Button: a short press on U1's BOOT button while paused starts charging to 100 %.",
    "Leave it charging for 15 minutes and touch Q1: it should be barely warm.",
]


def guide(counts):
    g = ["# Breadboard guide: smart charge switch", "",
         "Made by `breadboard/gen_breadboard.py`, which first checks that this wiring gives exactly the "
         "connections of the circuit in `DESIGN.md` (%d connections, %d parts, %d wires). "
         "Do not edit the tables by hand: change the script and run it again." % counts, "",
         "Picture: `breadboard/breadboard-layout.svg`.", "",
         "J1's pads run %s from row %d down, read from the labels printed on the real board. "
         "Plug it in with the GND pad in row %d." % (", ".join(J1_ORDER), J1_TOP, J1_TOP), "",
         "## Before you start", "",
         "- Do this before the lock-in amplifier is built on the same breadboard.",
         "- Solder pin headers onto U1, J1 and the Q1 adapter board, and solder Q1 (the SI4435) onto its adapter "
         "with the dot at pin 1.",
         "- Cut the USB-C cable. On the half that goes to the phone, find the 5 V, ground and CC wires "
         "(plug it into J1 and use the continuity beeper), and solder each to a single header pin.",
         "- Basic current only: R4 and R5 stay in the bag. Breadboard contacts are not made for 3 A, "
         "and the page's 'Most current' setting must stay on Basic.",
         "- A hole name like `c12` means column c, row 12. `L+`, `L-`, `R+`, `R-` are the long rails.", ""]
    for stage in sorted(STAGES):
        title, tests = STAGES[stage]
        g += ["## Stage %d: %s" % (stage, title), "", "| Part | Value | Holes | Note |", "|---|---|---|---|"]
        for st, ref, value, kind, legs, note in PARTS:
            if st == stage:
                holes = list(legs.values())
                where = "%s to %s" % (holes[0], holes[-1]) if kind == "module" and ref != "U1" else \
                    "d%d to d%d and h%d to h%d" % (U1_TOP, U1_TOP + 6, U1_TOP, U1_TOP + 6) if ref == "U1" else \
                    " and ".join(holes)
                g.append("| %s | %s | %s | %s |" % (ref, value, where, note))
        wires = [w for w in WIRES if w[0] == stage]
        if stage == 5:
            wires = wires + [(5, "cable 5 V", EXTERNAL["J2.VBUS"], "red", "to the phone"),
                             (5, "cable CC", EXTERNAL["J2.CC"], "any", "to the phone"),
                             (5, "cable ground", EXTERNAL["J2.GND"], "black", "to the phone")]
        if wires:
            g += ["", "| Wire from | To | Colour | What it carries |", "|---|---|---|---|"]
            g += ["| %s | %s | %s | %s |" % (a, b, c, what) for _, a, b, c, what in wires]
        g += ["", "Check:", ""] + ["%d. %s" % (i + 1, t) for i, t in enumerate(tests)] + [""]
    g += ["## Final tests", ""] + ["%d. %s" % (i + 1, t) for i, t in enumerate(FINAL)] + [
        "", "## What is different from the final soldered build", "",
        "- R4 (12 k) and R5 (4.7 k) are added, so the phone can be offered 1.5 A or 3 A.",
        "- Q1 is soldered straight onto the board; the three source pins and four drain pins are all joined by copper.",
        "- Short, wide copper replaces the breadboard contacts on the 5 V path.", ""]
    return "\n".join(g)


def main():
    pin_hole, groups, used = build()
    errors = check(groups)
    if errors:
        print("WIRING DOES NOT MATCH THE CIRCUIT:")
        for e in errors:
            print("  " + e)
        sys.exit(1)
    counts = (len(EXPECTED), len(PARTS), len(WIRES))
    print("WIRING OK: %d connections match the circuit" % len(EXPECTED))
    print("parts: %d, wires: %d, holes used: %d" % (len(PARTS), len(WIRES), len(used)))
    with open(os.path.join(HERE, "breadboard-layout.svg"), "w", encoding="utf-8", newline="\n") as f:
        f.write(svg())
    with open(os.path.join(HERE, "..", "BREADBOARD-GUIDE.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write(guide(counts))
    print("wrote breadboard-layout.svg and ../BREADBOARD-GUIDE.md")


if __name__ == "__main__":
    main()
