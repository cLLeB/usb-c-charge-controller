# Smart charge switch (the "smart socket"), version 1 (program 1.5)

Written 2026-10-09, updated 2026-11-02. Status: board rev 0.2 made by PCBWay, then assembled,
soldered and tested on 2026-10-28 (photos in `docs/photos/`). The firmware is now at version 1.8;
version 1.5 passed 26 checks over Wi-Fi on the real XIAO and the Android app is tested on a real
tablet against it. Build steps: `BUILD-GUIDE.md`.

## What it is

A small adapter that sits between a USB-C charger and the phone. The phone tells it the battery
percentage over Wi-Fi, and the adapter stops charging at an upper limit (for example 80 %) and starts
again at a lower one (for example 40 %).

It switches the 5 V side of the charger, not the 230 V mains. That is why it can be built by hand:
nothing in it is above 5 V.

## What version 1 does and does not do

- Works with anything that charges from USB-C at 5 V: phones, tablets, earbuds, power banks.
- Charges at 5 V, up to 3 A (15 W) if the charger offers it. No fast charging above 15 W.
- Does not charge laptops. A laptop needs 20 V, and passing that through needs a chip that speaks the
  USB Power Delivery protocol. That is a version 2 job.
- Charge only: the data wires are not connected, so the adapter cannot be used to transfer files.
- Needs a USB-C charger with a USB-C socket. Old chargers with the big rectangular USB-A socket are
  not supported.

## How it works

To the charger, the adapter looks like a simple 5 V device. To the phone, the adapter looks like a
simple 5 V charger. Stopping the charge is the same as pulling the plug out, and starting it is the
same as plugging it in, so every phone already knows how to handle it.

USB-C uses a signal wire called CC to say "something is connected" and "how much current is allowed":

- A device puts a 5.1 kilohm resistor from CC to ground. The charger sees it and turns on 5 V.
- A charger puts a resistor from CC up to its supply. The value tells the device the current limit.

## Circuit

Parts and connections:

| Ref | Part | Connection |
|---|---|---|
| J1 | USB-C socket board, Adafruit 4090 | Charger side. Its two built-in 5.1 k resistors make the charger turn on 5 V. Pins used: VBUS, GND, CC1, CC2 |
| J2 | Half of a cut USB-C cable (the end with the plug) | Phone side. Wires used: VBUS, GND, CC. The two data wires (D+, D-) twisted together and insulated: that marks a wall charger (USB BC 1.2), so devices take more than 0.5 A |
| U1 | Seeed XIAO ESP32-C3 | The controller, with Wi-Fi |
| D1 | 1N5817 Schottky diode | J1 VBUS to U1 pin 5V (stripe towards U1). Required by Seeed when powering through the 5V pin |
| Q1 | SI4435DDY P-channel MOSFET, SOIC-8 (on the adapter board for the breadboard; straight onto the custom board) | The power switch. Source (pins 1-3) to J1 VBUS, drain (pins 5-8) to J2 VBUS, gate is pin 4 |
| R1 | 330 k | Q1 gate to J1 VBUS. Keeps Q1 off by default |
| Q2 | 2N7000 | Drain to Q1 gate, source to GND, gate to U1 D10. Turns Q1 on |
| R2 | 330 k | Q2 gate to GND. Keeps Q2 off while U1 starts up |
| R3 | 33 k | U1 D3 to J2 CC. "Charger present, basic current" |
| R4 | 12 k | U1 D4 to J2 CC. "1.5 A allowed" |
| R5 | 4.7 k | U1 D5 to J2 CC. "3 A allowed" |
| R6 | 330 k | J2 CC to U1 D2 (analog input). Tells U1 whether a phone is plugged in |
| R7, R8 | 100 k each | J1 CC1 and J1 CC2 to U1 D1 (analog input). Tells U1 how much current the charger offers |
| C1, C2 | 100 nF disc | U1 D1 to GND, U1 D2 to GND. Steady the two analog readings |
| C3 | 100 nF disc | J1 VBUS to GND |
| R10 | 1 k | J2 VBUS to GND. Empties the phone-side 5 V quickly when Q1 opens, so the phone sees a clean unplug. Added 2026-10-09; uses a lock-in spare |
| LED1, R9 | Red LED, 680 ohm | U1 D7 to LED to GND. Shows the state |
| Button | The BOOT button already on U1 | "Charge to 100 % this once" |

U1 pin use: D1 = GPIO3, D2 = GPIO4, D3 = GPIO5, D4 = GPIO6, D5 = GPIO7, D7 = GPIO20, D10 = GPIO10.
The start-up pins GPIO2, GPIO8 and GPIO9 are left alone (GPIO9 is the BOOT button).

Exactly one of D3, D4, D5 is driven high at any time; the other two are set as inputs
(disconnected).

Drawings: `images/wiring.svg` (wiring) and `images/concept.svg` (the finished adapter).

## What the program does

1. Power on. Wait 5 seconds with the output off. A fast charger switches its 5 V off and on a few
   times while it finds out the adapter does not speak the fast-charge protocol; U1 restarts during
   that, which is harmless.
2. Read D1 to learn what the charger offers. Above 0.62 V means 3 A, 0.33 to 0.62 V means 1.5 A,
   below that means basic current.
3. Drive D3 high (33 k) all the time the output is off. Read D2: 0.15 to 1.6 V means a phone is
   plugged in, higher means the cable end is empty.
4. If a phone has been there for 0.15 s and charging is allowed: turn Q1 on, then switch to the
   resistor that matches step 2, capped by the "most current" setting.
5. To stop: turn Q1 off and go back to D3. With the 5 V gone the phone treats it as an unplug,
   while D3 lets U1 keep seeing whether the phone is still there.
6. Remember the allowed/stopped state in flash memory so it survives a restart.

The decision rule: a battery report at or above the upper limit stops charging, a report at or
below the lower limit allows it, and anything in between changes nothing.

Light (program 1.5): steady = charging, blink once a second = phone present but paused, fast blink
= setup open, short flash every 2 s = too warm, off = no phone, rapid flashing = "find me".

Button (BOOT on U1, pressed through the lid): 1 press = charge to 100 % once (press again to
cancel), 3 quick presses = join the Wi-Fi by WPS, hold 3 s = open setup for 5 minutes, hold 10 s =
forget everything (Wi-Fi, name, PIN, settings, history).

First start: with no Wi-Fi saved, the socket opens setup by itself (fast blinking). Four ways in:
- The Android app, "New socket": over Bluetooth, choose the home network, type its password, a name
  and an optional PIN. Tested end to end on 2026-10-16.
- WPS: press the socket's button 3 times, then the router's WPS button.
- The setup network `SmartSocket-setup-xxxxxx`: join it and the setup page opens by itself.
- The USB serial port: `setup {"ssid":"...","pass":"...","name":"...","pin":"..."}`.

Afterwards each socket answers at its own address, `http://smartsocket-xxxxxx.local` (the last six
characters of its Wi-Fi hardware address; this one is `smartsocket-157698.local`), so several
sockets can share one Wi-Fi. Each has a name chosen by the user and an optional PIN.

## How the battery level reaches the adapter

Anything that can open a web address can report: `http://smartsocket.local/api/battery?level=80`.

- iPhone: two Shortcuts automations (Battery Level rises above / falls below), action
  "Get Contents of URL". No app to install.
- Android: our app (`app-android/SmartSocket.apk`). It finds the sockets on the Wi-Fi, and its
  background reporter sends the phone's level to the chosen socket whenever it changes. An
  automation app such as MacroDroid also works.
- A laptop: the Windows app below has a tick box that sends the laptop's own level.

## Files

| Path | What it is | Checked how |
|---|---|---|
| `firmware/smart_socket/smart_socket.ino` | Program for U1, version 1.5 (the full API is listed at the top of the file) | Loaded on the real XIAO 2026-10-16: 26 of 26 checks pass over Wi-Fi (`tools/test_device.py`); Bluetooth setup from the tablet tested end to end after the join fix of the same day, and the Wi-Fi details survive a restart. Sensing and switching are untested until the circuit exists |
| `app-android/` (`SmartSocket.apk`, sources, `build.ps1`, `test_on_device.py`) | Android app 1.5: finds every socket, names, PINs, all controls, Bluetooth setup, background battery reporter | Built without Gradle (`build.ps1`); 17 of 17 checks pass on the tablet against the real socket. Keep `smartsocket.keystore` (not in the repository): updates must be signed with it |
| `tools/test_device.py` | Feature-by-feature check of a real socket over Wi-Fi | 26 of 26 pass |
| `BUILD-GUIDE.md` | Soldering in stages, with a multimeter check after each | Written 2026-10-16 for the PCBWay boards |
| `images/smart-socket-3d.html`, `images/smart-socket-3d-standalone.html` (made by `images/make_standalone.py`) | 3D view of the board in its case, with the cable advice | Published as a claude.ai page; the standalone copy opens offline |
| `firmware/smart_socket/index_html.h` | The web page, packed for U1 (made by `tools/embed_html.py`) | Generated |
| `web/index.html` | Control page served by the adapter | Run in a browser against the stand-in |
| `app-windows/SmartSocket.exe` (+ `.cs`, `build.cmd`, `test_ui.ps1`) | Windows remote control 1.5 (it does not charge laptops: the socket is 5 V only). Every socket on the network in one list with a live state; tabs for Charging (limits, modes, 100 % once, current), Timer and hours (charge timer, charging hours, safety hours), History (last 20 runs) and Socket (rename, PIN, light brightness, find me, program upload over Wi-Fi, details); notification-area icon with a message when charging starts or stops; optional start with Windows; set up a new socket over USB | Rebuilt 2026-10-16. Windows Defender first blocked the new program as unknown software; allowed by the user. Against the stand-ins: all four tabs show live data, a saved PIN works, the network search finds a socket in about 8 s, and `test_ui.ps1` (clicks the buttons, reads the socket back) passes 11 of 11. Not yet tried on the real XIAO (unplugged at the time): set up over USB and program upload |
| `tools/logic.py`, `tools/test_logic.py` | The decision rules, with timer, charging hours and the heat cut-out (75 C off, 65 C on again), and 62 scenario tests | 62 of 62 pass |
| `tools/mock_server.py` | Stand-in for one or more sockets (`python mock_server.py 8765 2`; the second has PIN 1234), same API as 1.5 | Used to try the page and the apps without hardware |
| `breadboard/gen_breadboard.py`, `breadboard/breadboard-layout.svg`, `BREADBOARD-GUIDE.md` | Breadboard layout for the proof build, with a check after each stage | Script check passes (16 connections); 60 of 66 deliberate wiring mistakes caught, the other 6 only moved pins that connect to nothing |
| `PHONE-SETUP.md` | iPhone and Android setup steps | Written from how the phone features normally work; not tried |
| `kicad/smartsocket.kicad_sch`, `kicad/smartsocket-schematic.pdf` (made by `kicad/gen_socket_schematic.py`) | Schematic of the board version | KiCad rule check: 0 errors, 0 warnings. Connection check: 16 of 16 match |
| `kicad/smartsocket.kicad_pcb` (made by `kicad/gen_socket_board.py`), `kicad/smartsocket-gerbers.zip`, `kicad/bom.csv`, `kicad/smartsocket-print-1to1.pdf`, `kicad/board-layout.png`, `kicad/board-3d.png` | Board rev 0.2, 51.2 x 24 mm, two layers, parts on both sides. `kicad/build_socket.ps1` rebuilds all of it | Drawn 2026-10-16. Design rule check: 0 errors, 0 unconnected, 0 differences from the schematic. Footprints checked against Seeed's and Adafruit's own files |
| `images/wiring.svg`, `images/concept.svg` | Wiring diagram and concept drawing | Viewed |
| `case/case.scad` | Draft 3D-print case, set to board rev 0.2 | Not rendered: no OpenSCAD on this computer. Heights are estimates |

The program's decision code is a line-for-line copy of `tools/logic.py`. The tests prove the rules,
not the copy, so the first build must still be watched.

## Program review, 2026-10-12 (version 1.4)

Four weaknesses found by reading the program against the circuit, all fixed and covered by tests
where the rule is testable (40 tests):

1. One bad sensor reading could cut the power and make the phone see an unplug. A phone must now
   read as missing for 100 ms before the power is cut. USB-C allows up to 650 ms.
2. The reading of what the charger offers could flip between two values near a threshold. It is
   now smoothed over about eight passes.
3. After falling back to the open setup network, the socket kept that network open even once it
   was back on the home Wi-Fi. It now closes it.
4. A charger that sags under load would restart the controller in a loop. After a restart caused
   by low voltage, the socket now offers basic current only until it is unplugged and plugged in.

## Program update fix (2026-10-16, not yet loaded)

An upload with a wrong PIN was ignored but still answered "ok" and restarted the socket, so anyone on the
Wi-Fi could restart it. Now it answers 401 and carries on. Compiled into
`firmware/smart_socket/build/smart_socket.ino.bin`; load it from the Windows app (Socket tab, Load a new
program) or over USB the next time the socket has power.

## Program 1.5 setup fix (2026-10-16)

The first Bluetooth setup from the tablet reported "Done" with no address and lost the Wi-Fi
details. Cause: the join check used a "connected" reading taken before the socket left the old
network, so it declared success at once and saved an empty network name. Fixed: a join only counts
once the socket is on the requested network with an address. The app also waits for the address,
reconnects if Bluetooth drops during the join, and gives up with a message after 45 s. Verified on
the tablet; the details survive a restart.

## Program 1.7: updates without USB (2026-10-28)

New programs load wirelessly, three ways, so USB is only a last resort:
- **Bluetooth** (new in 1.7): from the Windows app's "Load a new program..." whenever the socket is not
  reachable over Wi-Fi, e.g. on a school network that keeps devices apart. About 2 to 4 minutes for the
  1.5 MB program. The app checks the file (MD5) and the socket checks it again before switching to it;
  a cut-off upload leaves the old program running. A PIN, if set, is needed.
- **Wi-Fi**: the same button when the laptop and socket share a network that lets them talk (home).
  Also from the Arduino IDE (network port smartsocket-xxxxxx, password in OTA_PASSWORD).
- **The socket's own network**: hold the button 3 s; join "SmartSocket-setup-..." and load it from there.

Program 1.6 had no Bluetooth updates, so 1.7 has to be loaded once by another way (USB or Wi-Fi).
The compiled file is `firmware/smart_socket/build/smart_socket.ino.bin`.

## The cable to buy, and the case

- One plain USB-C to USB-C cable, "USB 2.0, 3 A / 60 W". Not USB 3.x, USB4, Thunderbolt, 100 W or
  5 A (those have extra wires and often a chip in the plug). Not USB-A to USB-C: that kind has no
  CC wire and a resistor in the plug that fights the socket's own signalling.
- Every working USB-C to USB-C cable has VBUS, GND and CC. Wire colours are not standard: find each
  with the beeper by plugging the cut cable into J1 (IDX 48).
- The case (`case/case.scad`) is closed and dust-tight: a socket-shaped hole for the charger, a
  round hole the cable's size with a strain-relief collar, the LED pressed into the lid, and the
  button pressed through a thin skin in the lid. U1's own USB port is closed in; take the lid off
  for a backup update over USB.

## Things specific to France

- Nothing in the adapter touches 230 V, so French plug types and mains safety rules do not apply.
- Wi-Fi: 2.4 GHz only, channels 1 to 13. The program sets the country code to FR.
- University Wi-Fi (eduroam) will not accept this kind of device. Test on a home box or a phone hotspot.

## Checks when the parts arrive, before soldering anything

1. J1: with a multimeter, measure CC1 to GND and CC2 to GND. Each should read about 5.1 k. If only
   one CC pad is present, the adapter still works but always uses basic current when the cable is
   plugged one way round.
2. J1 alone: plug it into the charger with a USB-C cable. VBUS to GND should read about 5 V.
3. Cut cable: plug its plug into J1 and use the continuity beeper to find which wire goes to VBUS,
   GND and CC.

## Build stages (decided 2026-10-09: nothing more is ordered)

Rule: the smart socket uses only what is already in the DigiKey order, plus lock-in spares.

1. Proof on the lock-in's breadboard, before the lock-in is built on it. U1, J1 and Q1 (on its
   adapter board) get their pin headers soldered on and plug in; resistors plug in directly.
   Basic current only (R4 and R5 left out), because breadboard contacts are not made for 3 A.
   This costs no parts: everything plugs out again.
2. Final build on a small custom circuit board, ordered from the board maker together with the
   lock-in board. The same U1 and J1 move across on their headers; the second SI4435 is soldered
   straight onto the board; resistors lie flat.
3. 3D-printed case, with a flexible tab in the lid that presses U1's BOOT button.

J1's pad order was read from the real board on 2026-10-12: GND, VBUS, SBU2, CC1, D-, D+, SBU1, CC2
(eight pads, 2.54 mm apart, CC1 and CC2 both present, two small resistors fitted beside CC2). The
custom board can now be drawn; its 5.1 k resistors still need the multimeter check. The solderable board SB400 stays as the fallback if the custom
board is late or wrong.

## Board rev 0.2 (drawn 2026-10-16, replaces rev 0.1)

Decided 2026-10-16 with the user: no extra cost, as little fine soldering as possible, a straight
"inline dongle" shape. Rev 0.1 (61.5 x 24 mm, charger at one end, cable out of a long side,
ten 0805 parts) is replaced.

Shape: charger in at the LEFT end, phone cable out at the RIGHT end, U1's USB port on the front
edge (programming), the light and the button through the lid. Board 51.2 x 24 mm; case about
55 x 28 x 16.5 mm, roughly a matchbox.

The "sandwich": J1 and U1 sit on the top side on their headers. The ten resistors and D1 lie flat
on the UNDERSIDE, under the two modules; their legs come up through the board and are soldered on
the top side. Q1, Q2, C1 to C3, the LED and the cable pads are on the top side beside U1.

- No 0805 parts any more: R1, R2, R6 (330 k), R7, R8 (100 k), R9 (680), R10 (1 k) are leaded
  resistors and C1 to C3 are 100 nF disc capacitors, all spares from the lock-in order. C3 was
  1 uF; 100 nF is enough because the charger and U1 have their own capacitors.
- Q1 (SOIC-8) is the only small part: there is no leaded part in the order that switches 3 A.
- Only the module pins that are used need soldering: U1 pins D1, D2, D3, D4, D5, D7, D10, GND and
  5V (9 of 14), J1 pins GND, VBUS, CC1 and CC2 (4 of 8). All holes are there, so the full header
  strips still go in.
- The 3 A path is copper areas: the 5 V input runs along the back edge on both layers (joined by
  vias), the phone side is an area on the top layer between Q1 and J2. Ground fills both layers.
- U1: Seeed's own footprint geometry (rows 15.24 mm apart, pins 1 and 14 at the USB end), holes
  1.0 mm. J1: Adafruit's rev B board file (pins 11.43 mm behind the socket edge; pin 1 GND at the
  back). U1's BOOT button is at the far end from its USB port, in the D6 corner (photo, 2026-10-16):
  the lid's pin hole is above it.
- J2's holes are 0.9 mm so a 3 A cable's 22 AWG red and black wires go through.
- Design rule check: 0 errors, 0 unconnected, 0 differences from the schematic. Remaining notices:
  U1's and LED1's footprints differ from the library on purpose (U1 widened; LED1's keep-out
  reduced to its legs, since it stands about 1 mm up to reach the lid), and a few silkscreen
  labels touch copper.

Final check before ordering (2026-10-16): order files read back against the board (72 of 72 holes,
outline 51.2 x 24 mm, everything inside JLCPCB's standard limits), firmware pins match the board,
values above match the board. Added printed labels so the modules cannot go on the wrong way:
GND / VBUS / CC1 beside J1's pins, "J1 parts up  < socket", and "U1 USB this edge".

Step-by-step build with a check after each stage: `BUILD-GUIDE.md`.

Assembly order (each step must be done before the next covers it):
1. Q1 on the top side (the only small part).
2. The ten resistors and D1 on the underside: legs through the board from below, solder them on
   the UNDERSIDE too (the holes are plated, so either side holds), then cut the legs flush on top
   (U1 and J1 sit 2.5 mm above). That way a resistor can be changed later from underneath without
   taking U1 or J1 off. If one was soldered on top: clip its body off underneath, leaving the two
   leg stubs, and solder the new resistor onto the stubs.
3. Q2, C1, C2, C3 and the LED on the top side; the LED's tip about 9.5 mm above the board.
4. Header pins into U1 and J1 (only the used pins need solder on the module side too), then the
   two modules onto the board.
5. The cut cable's three wires into J2: red +5V, CC, black GND.

## Board rev 0.1 (drawn 2026-10-16, replaced the same day)

Left to right: LED, D1 and Q2; U1 lengthwise; R3 to R5; Q1; J1 with its socket out of the right
end; cable out of the front edge. 61.5 x 24 mm, ten 0805 parts. Files overwritten by rev 0.2.

## Features

In the program, no parts needed:

- Stop and start limits, with "always charge", "never charge" and "100 % this once".
- Tells the phone how much current it may take, following what the charger offers.
- Knows whether a phone is plugged in; shows how long it has been charging.
- Setup by Bluetooth, WPS, setup network or USB serial; its own address and name; several sockets on
  one Wi-Fi; optional PIN (five wrong tries lock it for a minute).
- Charge timer, charging hours (France time from the internet), heat cut-out from U1's own
  temperature sensor, the last 20 charging runs, light brightness, "find me".
- New program versions load over Wi-Fi, uploaded from the page or the app (or from the Arduino IDE
  with the password in the program file), so the case need not be opened.
- Optional safety: charge again after a chosen number of hours without a report.

Left out on purpose:

- Measuring the charging power. It needs a current-sense chip that is not in the order.
- A second USB-C socket for the phone side instead of the fixed cable. Also needs parts not in the
  order, and extra circuitry to handle the plug being either way up.

## Numbers checked against the USB-C standard

The phone reads the voltage on CC to learn the current limit. With the phone's 5.1 k resistor and
U1 driving 3.3 V through each resistor:

| Resistor | Voltage on CC | Window the phone accepts | Meaning |
|---|---|---|---|
| R3 33 k | 0.44 V | 0.25 to 0.61 V | Basic current |
| R4 12 k | 0.98 V | 0.70 to 1.16 V | 1.5 A |
| R5 4.7 k | 1.72 V | 1.31 to 2.04 V | 3 A |

The first draft had 10 k for R5. That gives 1.11 V, which a phone reads as 1.5 A, so it was
corrected to 4.7 k.

On the charger side the same three windows appear across the 5.1 k on J1. R7 and R8 halve the
reading, which is why the program thresholds are 0.33 V and 0.62 V.

Q1 is switched with about 4.5 to 4.9 V on its gate, and its on-resistance is specified at 4.5 V.
At 3 A it wastes roughly 0.3 to 0.4 W, which a SOIC-8 on an adapter board can shed.

No circuit simulation was run. The circuit is resistor dividers and one switch, so the arithmetic
above is the whole of what a simulator would show. What a simulator cannot show is how real chargers
and phones react, and that is what the breadboard stage is for.

## Everything needed, start to finish

In the DigiKey order (section 1D of `BUY-LIST.txt`): controller board, USB-C socket board, two
power transistors, adapter board, two Schottky diodes, 4.7 k / 5.1 k / 12 k / 33 k / 100 k resistors,
two extra 2N7000, two extra 10 uF capacitors, solderable prototype board.

Shared with the lock-in order: breadboard, one 680 ohm and one 1 k resistor, two 100 nF capacitors, one red LED.

Already owned or at the lab: pin header strip, soldering tools, multimeter, hook-up wire, 3D printer,
a USB-C charger with its USB-C cable, a laptop to program U1.

Still to buy locally: one basic USB-C to USB-C cable to cut in half. Nothing else.

Software, free: Arduino IDE with the ESP32 board package, to load the program onto U1.

## Plan

1. Parts arrive: do the three checks above, and measure J1's pad order.
2. Proof on the breadboard (stage 1). Pass marks: charger turns its 5 V on; phone starts charging;
   stop and start from the page work; state survives pulling and restoring power.
3. Set up the phone automation and run one full cycle between the two limits.
4. Draw the custom board in KiCad from the proven circuit; print it 1:1 on paper and lay the real
   parts on the print before ordering.
5. Order it with the lock-in board (done 2026-10-16, PCBWay, order YX1829656). Solder, repeat the stage 1 tests, then add R4 and R5 and test
   at full current while checking Q1 stays cool.
6. Measure the finished board, set the numbers in `case/case.scad`, print the case.

## Known limits and risks

- Nothing here has been built yet. The circuit uses the standard USB-C method on both sides, but
  only a real test proves it.
- The 3 A setting lets the phone take 3 A while U1 takes about 0.1 A more, slightly over what the
  charger offered. If a charger objects, cap the adapter at 1.5 A in the program.
- While U1 is restarting its pins float for a moment, and a phone that can also power other
  devices may then put up to 5 V on CC through its own resistor. U1's pins are rated for 3.6 V;
  the current is limited to well under 1 mA by the resistors. Not yet tested.
- Without a PIN, anyone on the same Wi-Fi can change the settings: set one in the app. The PIN
  travels unencrypted over the home Wi-Fi, so it keeps out housemates, not a determined attacker.
- If Wi-Fi drops, the adapter does not know the percentage. The program must choose a fallback
  (keep charging is the safe one for the phone's owner).
