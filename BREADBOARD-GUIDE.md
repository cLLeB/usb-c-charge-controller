# Breadboard guide: smart charge switch

Made by `breadboard/gen_breadboard.py`, which first checks that this wiring gives exactly the connections of the circuit in `DESIGN.md` (16 connections, 19 parts, 11 wires). Do not edit the tables by hand: change the script and run it again.

Picture: `breadboard/breadboard-layout.svg`.

J1's pads run GND, VBUS, SBU2, CC1, D-, D+, SBU1, CC2 from row 3 down, read from the labels printed on the real board. Plug it in with the GND pad in row 3.

## Before you start

- Do this before the lock-in amplifier is built on the same breadboard.
- Solder pin headers onto U1, J1 and the Q1 adapter board, and solder Q1 (the SI4435) onto its adapter with the dot at pin 1.
- Cut the USB-C cable. On the half that goes to the phone, find the 5 V, ground and CC wires (plug it into J1 and use the continuity beeper), and solder each to a single header pin.
- Basic current only: R4 and R5 stay in the bag. Breadboard contacts are not made for 3 A, and the page's 'Most current' setting must stay on Basic.
- A hole name like `c12` means column c, row 12. `L+`, `L-`, `R+`, `R-` are the long rails.

## Stage 1: Charger side

| Part | Value | Holes | Note |
|---|---|---|---|
| J1 | USB-C socket board | a3 to a10 | pins in column a, the board hangs out over the left edge. Rows follow the printed labels |
| C3 | 10 uF | L+ and L- | plugs into the two left-hand rails, long leg (+) in L+ |

| Wire from | To | Colour | What it carries |
|---|---|---|---|
| L+ | R+ | red | joins the two 5 V rails (at row 61) |
| L- | R- | black | joins the two ground rails (at row 62) |
| b4 | L+ | red | charger 5 V to the rail |
| b3 | L- | black | charger ground to the rail |

Check:

1. Before plugging anything in: multimeter on resistance, J1 pad CC1 to GND and CC2 to GND. Each should read about 5.1 k. If they read open, add a 5.1 k resistor from each to ground.
2. Plug the charger into J1 with a USB-C cable. Rail L+ to rail L- should read 4.75 to 5.25 V.
3. Unplug the charger before the next stage.

## Stage 2: Controller

| Part | Value | Holes | Note |
|---|---|---|---|
| U1 | XIAO ESP32-C3 | d20 to d26 and h20 to h26 | USB-C port toward row 1. Left pins in column d, right pins in column h |
| D1 | 1N5817 | R+ and j20 | band end in j20 |

| Wire from | To | Colour | What it carries |
|---|---|---|---|
| j21 | R- | black | U1 ground |

Check:

1. Load the program onto U1 from the computer first, with the charger unplugged.
2. Charger plugged in, computer unplugged: hole j20 to ground reads 4.4 to 5.0 V, hole j22 (3V3) reads 3.2 to 3.4 V.
3. A phone or computer sees a Wi-Fi network called SmartSocket-setup.

## Stage 3: Charger-offer reading

| Part | Value | Holes | Note |
|---|---|---|---|
| R7 | 100 k | c6 and c12 |  |
| R8 | 100 k | d10 and d12 | stand it on end if the rows are close |
| C1 | 100 nF | a21 and L- | spread the legs to reach the rail |

| Wire from | To | Colour | What it carries |
|---|---|---|---|
| e12 | b21 | any | charger-offer reading to U1 pin D1 |

Check:

1. Hole b21 to ground reads 0.12 to 0.31 V (basic charger), 0.35 to 0.58 V (1.5 A) or 0.65 to 1.02 V (3 A).
2. The Details section of the page shows the same thing in words.

## Stage 4: Power switch

| Part | Value | Holes | Note |
|---|---|---|---|
| Q1 | SI4435 on its adapter | e30 to f33 | across the centre gap, pin 1 (dot on the chip) at e30 |
| R1 | 100 k | L+ and a33 |  |
| Q2 | 2N7000 | j34 and j35 and j36 | flat face toward the centre gap |
| R2 | 100 k | h35 and h34 | stand it on end |

| Wire from | To | Colour | What it carries |
|---|---|---|---|
| a30 | L+ | red | 5 V to the power switch |
| g36 | c33 | any | Q2 to the gate of Q1. Crosses the centre gap |
| i23 | i35 | any | U1 pin D10 to Q2 |
| i34 | R- | black | Q2 ground |

Check:

1. Nothing on the phone side yet: hole j30 to ground reads about 0 V, hole a33 (gate) reads about 5 V.
2. On the page choose 'Always charge'. Still 0 V at j30, because no phone is detected.

## Stage 5: Phone side

| Part | Value | Holes | Note |
|---|---|---|---|
| R3 | 33 k | b23 and b28 |  |
| R6 | 100 k | d28 and d29 | stand it on end |
| C2 | 100 nF | b29 and L- | spread the legs to reach the rail |
| R10 | 1 k | i30 and R- |  |

| Wire from | To | Colour | What it carries |
|---|---|---|---|
| a22 | a29 | any | phone reading to U1 pin D2. Lay it outside column a |
| cable 5 V | j30 | red | to the phone |
| cable CC | e28 | any | to the phone |
| cable ground | R- | black | to the phone |

Check:

1. No phone: hole e28 to ground reads about 3.3 V.
2. Pretend phone: push a 5.1 k resistor between e28 and ground. e28 drops to 0.25 to 0.61 V, and within a second j30 reads about 5 V. Pull the resistor out: j30 falls back to 0 V.
3. Real phone on the cut cable: the phone shows it is charging. Choose 'Never charge': it stops.

## Stage 6: Light

| Part | Value | Holes | Note |
|---|---|---|---|
| R9 | 680 | i26 and i29 |  |
| LED | red LED | j29 and R- | long leg in j29, short leg in the rail |

Check:

1. Light on while charging, slow blink while paused, off with no phone.

## Final tests

1. Limits: open http://smartsocket.local/api/battery?level=80 in a browser. Charging stops. Open the same address with level=40. Charging starts.
2. Memory: while paused, unplug the charger for ten seconds and plug it back. After about 5 seconds it is still paused.
3. Unplug the phone while charging: j30 falls to 0 V at once.
4. Button: a short press on U1's BOOT button while paused starts charging to 100 %.
5. Leave it charging for 15 minutes and touch Q1: it should be barely warm.

## What is different from the final soldered build

- R4 (12 k) and R5 (4.7 k) are added, so the phone can be offered 1.5 A or 3 A.
- Q1 is soldered straight onto the board; the three source pins and four drain pins are all joined by copper.
- Short, wide copper replaces the breadboard contacts on the 5 V path.
