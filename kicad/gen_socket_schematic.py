"""Generate smartsocket.kicad_sch for the smart charge switch and the netlist it must produce.

Run with KiCad's bundled Python:  python gen_socket_schematic.py
Uses the drawing helpers of the lock-in project (../../kicad/gen_schematic.py and kisym.py).
Regenerating overwrites smartsocket.kicad_sch: edit this file, not the schematic.

Board version of the circuit in ../DESIGN.md (rev 0.2). Differences from the breadboard build:
R1, R2, R6 are 330 k; C3 is 100 nF; R4 and R5 are fitted. Every part except Q1 has wire legs
(no 0805 parts), all from the DigiKey order or the lock-in spares.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "kicad"))
import gen_schematic as base  # noqa: E402

base.PROJECT = "smartsocket"
G = base.G

FP_DISC = "Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm"   # K104K15X7RF5TL2, 5 mm lead spacing
FP_RAX = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal"
FP_DO41 = "Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal"
FP_LED = "LED_THT:LED_D5.0mm"   # standing, shining up through the lid
FP_J1 = "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"
FP_J2 = "Connector_Wire:SolderWire-0.5sqmm_1x03_P4.6mm_D0.9mm_OD2.1mm"   # 0.9 mm holes take 22 AWG
# The XIAO has two rows of 7 pins, 2.54 mm pitch, rows 15.24 mm apart. KiCad has no such footprint,
# so the board script loads this one and moves pins 8 to 14 outward by 7.62 mm.
FP_XIAO = "Package_DIP:DIP-14_W7.62mm"

# XIAO ESP32-C3 pins in DIP numbering, USB end at pin 1 / pin 14.
XIAO = {1: "D0", 2: "D1", 3: "D2", 4: "D3", 5: "D4", 6: "D5", 7: "D6",
        8: "D7", 9: "D8", 10: "D9", 11: "D10", 12: "3V3", 13: "GND", 14: "5V"}
J1_PADS = {1: "GND", 2: "VBUS", 3: "SBU2", 4: "CC1", 5: "D-", 6: "D+", 7: "SBU1", 8: "CC2"}

DIRS = {"L": (-1, 0, 180), "R": (1, 0, 0), "U": (0, -1, 90), "D": (0, 1, 270)}


def at(s, pin):
    x, y = s.pin_at[tuple(pin.split("."))]
    return x / G, y / G


def tag(s, pin, name, way, length=3):
    """Short wire from a pin, ending in a net label."""
    dx, dy, rot = DIRS[way]
    x, y = at(s, pin)
    end = (x + dx * length, y + dy * length)
    s.wire(pin, end)
    s.label(name, end, rot)


def ground(s, pin, way="D", length=2):
    dx, dy, _ = DIRS[way]
    x, y = at(s, pin)
    end = (x + dx * length, y + dy * length)
    s.wire(pin, end)
    s.power("GND", end)
    return end


def res(s, ref, value, x, y, top, bottom, fp=None):
    s.place(ref, "Device:R", x, y, value=value, fp=fp or FP_RAX, ref_at=(x + 2, y - 0.5), val_at=(x + 2, y + 0.5))
    for pin, net, way in ((ref + ".1", top, "U"), (ref + ".2", bottom, "D")):
        if net == "GND":
            ground(s, pin, way)
        else:
            tag(s, pin, net, way, 2)


def cap(s, ref, value, x, y, top):
    s.place(ref, "Device:C", x, y, value=value, fp=FP_DISC, ref_at=(x + 2.2, y - 0.5), val_at=(x + 2.2, y + 0.5))
    tag(s, ref + ".1", top, "U", 2)
    ground(s, ref + ".2")


def build():
    s = base.Sheet()

    s.text("Smart charge switch (board version)", 14, 12, 2.5)

    # ---- charger side ----------------------------------------------------------------
    s.text("1  Charger side (USB-C socket board)", 14, 20)
    s.place("J1", "Connector_Generic:Conn_01x08", 24, 30, value="USB-C socket board", fp=FP_J1,
            ref_at=(24, 24.5), val_at=(24, 36.5))
    x, y = at(s, "J1.1")
    s.wire("J1.1", (x - 4, y))
    s.wire((x - 4, y), (x - 9, y))
    s.power("GND", (x - 9, y))
    s.flag((x - 4, y))
    tag(s, "J1.2", "5V_IN", "L", 4)
    tag(s, "J1.4", "CC1_IN", "L", 4)
    tag(s, "J1.8", "CC2_IN", "L", 4)
    for pin in ("3", "5", "6", "7"):
        s.nc("J1." + pin)
    s.text("pads: 1 GND, 2 VBUS, 3 SBU2, 4 CC1, 5 D-, 6 D+, 7 SBU1, 8 CC2", 11, 39, 1.2)

    # ---- controller ------------------------------------------------------------------
    s.text("2  Controller (XIAO ESP32-C3)", 46, 20)
    s.place("U1", "Connector_Generic:Conn_02x07_Counter_Clockwise", 62, 30, value="XIAO ESP32-C3", fp=FP_XIAO,
            ref_at=(62, 24.5), val_at=(62, 35.5))
    nets = {2: "OFFER", 3: "PHONE_SENSE", 4: "RP_BASIC", 5: "RP_MID", 6: "RP_HIGH",
            8: "LED_DRIVE", 11: "POWER_ON", 14: "5V_U1"}
    for n in range(1, 15):
        pin = "U1.%d" % n
        way = "L" if n <= 7 else "R"
        if n == 13:
            ground(s, pin, way, 9)
        elif n in nets:
            tag(s, pin, nets[n], way, 4)
        else:
            s.nc(pin)
    s.text("pins: 1 D0, 2 D1, 3 D2, 4 D3, 5 D4, 6 D5, 7 D6,", 46, 39, 1.2)
    s.text("8 D7, 9 D8, 10 D9, 11 D10, 12 3V3, 13 GND, 14 5V", 46, 40, 1.2)

    # ---- phone side ------------------------------------------------------------------
    s.text("3  Phone side (cut cable)", 92, 20)
    s.place("J2", "Connector_Generic:Conn_01x03", 104, 30, value="cable to phone", fp=FP_J2,
            ref_at=(104, 27), val_at=(104, 33.5))
    tag(s, "J2.1", "5V_PHONE", "L", 4)
    tag(s, "J2.2", "CC_PHONE", "L", 4)
    ground(s, "J2.3", "L", 4)

    # ---- power switch ----------------------------------------------------------------
    s.text("4  Power switch: Q1 passes the 5 V when Q2 pulls its gate low", 14, 44)
    s.place("Q1", "Transistor_FET:IRF7404", 34, 54, value="SI4435DDY", fp=base.FP_SO8, ref_at=(37, 53.5), val_at=(37, 54.5))
    tag(s, "Q1.1", "5V_IN", "D", 2)
    tag(s, "Q1.5", "5V_PHONE", "U", 2)
    tag(s, "Q1.4", "GATE", "L", 3)
    res(s, "R1", "330k", 22, 54, "5V_IN", "GATE")
    s.place("Q2", "Transistor_FET:2N7000", 50, 54, value="2N7000", fp=base.FP_TO92, ref_at=(53, 53.5), val_at=(53, 54.5))
    ground(s, "Q2.1")
    tag(s, "Q2.3", "GATE", "U", 2)
    tag(s, "Q2.2", "POWER_ON", "L", 3)
    res(s, "R2", "330k", 60, 54, "POWER_ON", "GND")
    s.place("D1", "Device:D_Schottky", 72, 54, value="1N5817", fp=FP_DO41, ref_at=(72, 51.5), val_at=(72, 56))
    tag(s, "D1.1", "5V_U1", "L", 3)
    tag(s, "D1.2", "5V_IN", "R", 3)
    cap(s, "C3", "100n", 86, 54, "5V_IN")
    res(s, "R10", "1k", 96, 54, "5V_PHONE", "GND")

    # ---- signalling ------------------------------------------------------------------
    s.text("5  What the charger offers", 14, 64)
    res(s, "R7", "100k", 18, 74, "CC1_IN", "OFFER")
    res(s, "R8", "100k", 26, 74, "CC2_IN", "OFFER")
    cap(s, "C1", "100n", 34, 74, "OFFER")

    s.text("6  What the phone is told, and whether it is there", 44, 64)
    res(s, "R3", "33k", 48, 74, "RP_BASIC", "CC_PHONE", FP_RAX)
    res(s, "R4", "12k", 56, 74, "RP_MID", "CC_PHONE", FP_RAX)
    res(s, "R5", "4.7k", 64, 74, "RP_HIGH", "CC_PHONE", FP_RAX)
    res(s, "R6", "330k", 72, 74, "CC_PHONE", "PHONE_SENSE")
    cap(s, "C2", "100n", 80, 74, "PHONE_SENSE")

    s.text("7  Light", 90, 64)
    res(s, "R9", "680", 92, 74, "LED_DRIVE", "LED_A")
    s.place("LED1", "Device:LED", 102, 74, value="red 5 mm", fp=FP_LED, ref_at=(102, 71.5), val_at=(102, 76))
    tag(s, "LED1.2", "LED_A", "R", 3)
    ground(s, "LED1.1", "L", 2)
    return s


# The netlist the drawing must produce: net name -> pins. Checked by check_netlist.py.
EXPECTED = {
    "5V_IN": ["J1.2", "D1.2", "Q1.1", "Q1.2", "Q1.3", "R1.1", "C3.1"],
    "GND": ["J1.1", "U1.13", "J2.3", "Q2.1", "R2.2", "R10.2", "C1.2", "C2.2", "C3.2", "LED1.1"],
    "5V_U1": ["D1.1", "U1.14"],
    "CC1_IN": ["J1.4", "R7.1"],
    "CC2_IN": ["J1.8", "R8.1"],
    "OFFER": ["R7.2", "R8.2", "C1.1", "U1.2"],
    "RP_BASIC": ["U1.4", "R3.1"],
    "RP_MID": ["U1.5", "R4.1"],
    "RP_HIGH": ["U1.6", "R5.1"],
    "CC_PHONE": ["R3.2", "R4.2", "R5.2", "R6.1", "J2.2"],
    "PHONE_SENSE": ["R6.2", "C2.1", "U1.3"],
    "GATE": ["Q1.4", "R1.2", "Q2.3"],
    "POWER_ON": ["U1.11", "Q2.2", "R2.1"],
    "5V_PHONE": ["Q1.5", "Q1.6", "Q1.7", "Q1.8", "R10.1", "J2.1"],
    "LED_DRIVE": ["U1.8", "R9.1"],
    "LED_A": ["R9.2", "LED1.2"],
}

if __name__ == "__main__":
    sheet = build()
    sheet.write(os.path.join(HERE, base.PROJECT + ".kicad_sch"), "Smart charge switch")
    pro = os.path.join(HERE, base.PROJECT + ".kicad_pro")
    if not os.path.exists(pro):
        with open(pro, "w", encoding="utf-8") as f:
            json.dump({"meta": {"filename": base.PROJECT + ".kicad_pro", "version": 3}}, f)
    with open(os.path.join(HERE, "expected_nets.json"), "w", encoding="utf-8") as f:
        json.dump(EXPECTED, f, indent=1)
    print("wrote", base.PROJECT + ".kicad_sch", "with", len(sheet.pin_at), "pins placed")
