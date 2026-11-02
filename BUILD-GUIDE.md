# Smart socket build guide (board rev 0.2)

Written 2026-10-16 for the PCBWay boards ordered that day (`kicad/smartsocket-gerbers.zip`,
51.2 x 24 mm, 5 copies). The circuit has not been built before: the expected readings below come
from the design arithmetic in `DESIGN.md`, except the two 5.1 k readings on J1, which were measured
(5.04 and 5.05 k).

The board is built in stages, each ending with a multimeter check, so a fault shows up while only a
few parts are on the board. Do not go on until a stage passes. If a check fails, stop and send a
photo of both sides of the board and the reading.

Tools: soldering iron, the DT-830B multimeter, side cutters, the laptop (for the USB checks), a
USB-C charger with its cable, and the phone.

## Rules

- Nothing plugged in while soldering.
- Resistors and D1 lie on the UNDERSIDE and are soldered on the underside too (the holes are plated,
  so either side holds). Cut their legs flush on the top side before the modules go on. Then any
  resistor can be changed later without taking U1 or J1 off.
- Only the module pins that are used need solder on the module side. All header pins still go in.

## Before soldering anything

1. Compare a bare board with `kicad/board-layout.png` (top side in red, underside parts in blue).
   The top side has the labels GND / VBUS / CC1 beside J1's row, "J1 parts up  < socket",
   "U1 USB this edge", and +5V / CC / GND beside J2.
2. Multimeter on resistance (200 k range is fine), bare board:
   - J1's GND pad (labelled) to its VBUS pad: open (the display shows 1).
   - J2's +5V pad to its GND pad: open.
   If either reads low, stop: the board has a short.

## Stage 1: Q1, the power switch (the only small part)

Fit: Q1, SI4435DDY (IDX 50), SOIC-8, on the top side. Pin 1 has a dot on the chip; it goes to the
corner marked by the small triangle on the board (the back row, nearest C3). Tack one corner pin,
check all eight pins sit on their pads, then solder the rest. Bridges between the four pins of one
row do not matter (pins 1 to 3 are all source, 5 to 8 all drain), but pin 4 (gate) must not touch
pin 3, and nothing may join the two rows.

Check:
- J1 GND to VBUS: still open. J2 +5V to GND: still open.
- J1 VBUS pad to J2 +5V pad: open or a diode-like high reading, not a short (Q1 is off).

## Stage 2: the underside (ten resistors and D1)

Legs through from the underside, solder on the underside, cut flush on top.

| Part | Value | Colour bands (5-band 1 % resistors) | Where (underside) |
|---|---|---|---|
| R1 | 330 k | orange orange black orange brown | right of U1, near Q1 |
| R2 | 330 k | orange orange black orange brown | under U1 |
| R3 | 33 k | orange orange black red brown | under U1 |
| R4 | 12 k | brown red black red brown | under U1 |
| R5 | 4.7 k | yellow violet black brown brown | under U1, nearest the back edge |
| R6 | 330 k | orange orange black orange brown | under U1 |
| R7, R8 | 100 k | brown black black orange brown | under J1 |
| R9 | 680 | blue grey black black brown | right side, near LED1 |
| R10 | 1 k | brown black black brown brown | right side, near J2 |
| D1 | 1N5817 | band towards the printed band | under U1, at the front |

Check (multimeter on resistance):
- J2 +5V to J2 GND: about 1 k (that is R10).
- J1 GND to VBUS: still open.

## Stage 3: the top-side small parts

Fit: Q2 (2N7000, IDX 2, from the 5 in the bag; flat face to the printed flat, legs spread slightly), C1, C2, C3 (100 nF
discs, either way round), LED1 (red 5 mm; the flat side of its rim and the short leg go to the
square pad). Stand the LED so its tip is about 9.5 mm above the board, to reach the hole in the lid.

Check:
- J1 GND to VBUS: open, or a slowly rising reading as C3 charges, not a steady low value.

## Stage 4: J1 alone, with a charger

Fit the header strip to J1 (IDX 48, the USB-C socket board): **parts up, socket at the board's left
edge, its GND pad on the hole labelled GND**. Solder all 8 pins on the board side and the 4 used
pins (GND, VBUS, CC1, CC2) on the module side. Leave U1 off for now.

Check, with the charger plugged into J1:
- J1 VBUS to GND: 4.75 to 5.25 V. This proves the charger sees J1's 5.1 k resistors and turns on.
- J2 +5V to GND: 0 V. This proves Q1 stays off when nothing tells it to switch on.
Unplug the charger.

## Stage 5: U1, the XIAO

U1 already carries program 1.5 and your Wi-Fi details. Fit the header strips (short ends into the
board), then U1 on top: **its USB port at the edge labelled "U1 USB this edge"**, the antenna
connector towards the back. Solder all 14 pins on the board side and the 9 used pins on the module
side: D1, D2, D3, D4, D5, D7, D10, GND, 5V. Click the antenna onto its connector.

Check, charger in J1, no phone:
- The light stays off (no phone yet). Fast blinking instead means it opened setup because it has
  no Wi-Fi details: run the app's "New socket" setup again.
- Within a minute the socket appears in the app (or at http://smartsocket-157698.local).
- J2 +5V to GND: 0 V (no phone, so no power to the phone side).
- Optional, with the XIAO's USB port also plugged into the laptop: the status line every 5 s shows
  `offer_mV` about 200 (default charger), about 450 (1.5 A charger) or about 850 (3 A charger),
  and `phone_mV` high, above about 2500 (no phone). Send me the line and I will read it.

## Stage 6: the phone cable

Cut the USB-C to USB-C cable about 15 cm from the plug that will go to the phone. Find its three
wires with the beeper: plug the cut cable into J1 (IDX 48) on a spare surface, and touch each wire
end and J1's VBUS, GND, CC1 and CC2 pads. Every working cable has VBUS, GND and CC. The ground may be the
bare braid (no black wire). Twist the two data wires (often white and green) to each other and tape them:
joined, they tell phones and tablets "wall charger", so they take up to about 2 A instead of 0.5 A
(left apart, a Samsung tablet showed "slow charger" and took 0.5 A; joined, 1 A into the battery).

Thread the cut end through the case's cable hole first (from outside), then solder: red/VBUS to
+5V, the CC wire to CC, black/GND to GND.

Check, cable not yet in the phone: J2 +5V to GND still about 1 k.

## Stage 7: first charge

Charger in J1, cable into the phone.

Check:
- Within about 10 s the light goes steady and the phone shows charging.
- The app shows the phone present and charging; the phone's own battery screen confirms.
- After 10 minutes, touch Q1: warm is fine, too hot to hold is not. Stop and tell me if so.
- With the app's battery reporting switched on in this phone, set the upper limit just below the
  phone's current level. Charging stops (light
  blinks once a second, J2 +5V drops to 0 V). Set it back. Charging starts again.

Only after this, try the higher current settings in the app, and check Q1 again after 10 minutes.

## Stage 8: case

Measure the finished board (tallest part above the board, J1's socket height, the cable's
diameter) and send me the numbers: `case/case.scad` is set from them, then printed.

## What the light means (program 1.5)

| Light | Meaning |
|---|---|
| Off | No phone found |
| Steady | Charging |
| Blinks once a second | Phone present, charging paused (limit, schedule or timer) |
| Fast blinking | Setup open (Bluetooth and the setup network) |
| Short flash every 2 s | Too warm, charging paused until it cools |
| Rapid flashing for a few seconds | "Find me" from the app |
