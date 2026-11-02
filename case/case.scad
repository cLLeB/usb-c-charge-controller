// SUPERSEDED: the case now in use is built by make_case_stl.py (cable slot open at the top, no soldering).

// Case for the smart charge switch, board rev 0.2 (kicad/smartsocket.kicad_pcb, 51.2 x 24 mm).
// Open in OpenSCAD (free), press F6, export STL.
//
// DRAFT: never rendered or printed. Positions come from the board file; heights are estimates
// from the parts' datasheets. Check them against the assembled board before printing.
//
// Closed and dust-tight: every opening is the size of what passes through it.
// - Charger socket: a socket-shaped hole. The board sits against that end wall, and the wall is
//   thinned to 0.6 mm around the socket, so the socket's face is only 0.8 mm in from the outside and any
//   plug goes fully in.
// - Phone cable: a round hole the size of the cable, with a strain-relief collar outside.
//   Thread the cut end in from outside before soldering it to the board.
// - Light: the LED is pressed into a 5.05 mm hole in the lid.
// - Button: no hole. The lid is 0.6 mm thin over U1's BOOT button, with a post underneath:
//   pressing the lid there presses the button.
// - U1's own USB port is closed in: programs load over Wi-Fi or from the app. For a backup
//   update over USB, take the lid off.
//
// Board coordinates used below: x from the charger end (left), y from the back edge, both in mm,
// z above the board's top surface (negative = underside).

// ---- from the board file ----
board_l = 51.2;
board_w = 24;
board_t = 1.6;
j1_y    = 12.0;     // centre of the charger socket across the board
led_x   = 43.0;     // LED centre (between its two legs)
led_y   = 20.5;
cable_y = 10.6;     // phone cable leaves the right end here (J2's middle pad)
button_x = 29.5;    // U1's BOOT button: far end from its USB port, D6 corner (photo, 2026-10-16)
button_y = 2.0;

// ---- estimates: measure on the assembled board ----
under_h = 3.4;      // resistors and diode lying flat on the underside, plus their joints
top_h   = 7.6;      // tallest part above the board (J1's socket top)
j1_z    = 5.7;      // centre height of the charger socket
sock_w  = 9.2;      // USB-C socket face, plus 0.2 mm clearance
sock_h  = 3.4;
sock_out = 0.6;     // how far the socket sticks out past the module's (and the board's) edge
button_top = 4.4;   // top of U1's BOOT button above the board
cable_d = 3.4;      // cable diameter: 11 mm round by paper strip (2026-10-28), minus the paper; the hole is 0.2 mm bigger
cable_z = 2.6;      // cable centre height above the board where it leaves

// ---- printing choices ----
wall = 1.6;
thin = 0.6;         // the wall around the charger socket: its face ends 0.8 mm inside the outer surface
gap  = 0.4;         // clearance around the board (none at the charger end)
lip  = 1.2;         // how far the lid drops into the base
rail = 1.0;         // ledge the board rests on, along both long walls (the board edge has no parts)
skin = 0.6;         // lid thickness over the button

in_l = board_l + gap;          // no gap at the charger end
in_w = board_w + 2 * gap;
out_l = in_l + 2 * wall;
out_w = in_w + 2 * wall;
out_h = wall + under_h + board_t + top_h + 0.6;

// board coordinates to case coordinates
function cx(x) = wall + x;
function cy(y) = wall + gap + y;
function cz(z) = wall + under_h + board_t + z;

module base() {
    difference() {
        union() {
            difference() {
                cube([out_l, out_w, out_h]);
                translate([wall, wall, wall]) cube([in_l, in_w, out_h]);
            }
            // rails under the two long board edges
            for (y = [wall, out_w - wall - rail])
                translate([wall, y, wall]) cube([in_l, rail, under_h]);
            // strain-relief collar around the cable hole
            translate([out_l, cy(cable_y), cz(cable_z)]) rotate([0, 90, 0])
                cylinder(d1 = cable_d + 3.2, d2 = cable_d + 1.6, h = 5, $fn = 48);
        }
        // charger socket: thin the wall from inside, then a socket-shaped hole through what is left
        translate([wall - (wall - thin) - 0.01, cy(j1_y) - 7, cz(j1_z) - 4]) cube([wall - thin + 0.02, 14, 8]);
        translate([-1, cy(j1_y), cz(j1_z)]) rotate([0, 90, 0]) linear_extrude(wall + 2)
            offset(r = 1.2) square([sock_h - 2.4, sock_w - 2.4], center = true);
        // phone cable: round hole through the wall and the collar
        translate([out_l - wall - 1, cy(cable_y), cz(cable_z)]) rotate([0, 90, 0])
            cylinder(d = cable_d + 0.2, h = wall + 8, $fn = 48);
    }
}

module lid() {
    // The lid is printed outer face down and turned over onto the base, which mirrors y.
    bx = cx(button_x);
    by = out_w - cy(button_y);
    post = cz(top_h + 0.6) - cz(button_top) - 0.3;  // from the lid's underside to just above the button
    difference() {
        union() {
            cube([out_l, out_w, wall]);
            translate([wall + 0.15, wall + 0.15, wall]) difference() {
                cube([in_l - 0.3, in_w - 0.3, lip]);
                translate([wall, wall, -1]) cube([in_l - 0.3 - 2 * wall, in_w - 0.3 - 2 * wall, lip + 2]);
            }
        }
        // the light is pressed into this hole
        translate([cx(led_x), out_w - cy(led_y), -1]) cylinder(d = 5.05, h = wall + 2, $fn = 60);
        // thin the lid over the button (from the inside), leaving a 0.6 mm skin to press
        translate([bx, by, skin]) cylinder(d = 7, h = wall, $fn = 48);
        // a small ring groove on the outside shows where to press
        translate([bx, by, -0.01]) difference() {
            cylinder(d = 7.4, h = 0.3, $fn = 48);
            cylinder(d = 6.6, h = 0.3, $fn = 48);
        }
    }
    // the post that reaches down to the button
    translate([bx, by, skin]) cylinder(d = 2.2, h = post, $fn = 24);
}

base();
translate([0, out_w + 8, 0]) lid();   // printed flat next to the base
