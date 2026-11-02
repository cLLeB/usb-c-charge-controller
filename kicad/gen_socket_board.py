"""Create the smart socket board (smartsocket.kicad_pcb) from smartsocket.net: place, route, pour.

Board rev 0.2, the inline "sandwich": the charger plugs into J1 at the left end, the phone cable
leaves at the right end, the light shines up through the lid. Every part except Q1 has wire legs.
The leaded resistors and the diode lie flat on the UNDERSIDE, below the two modules, so the board
is only 51.2 x 24 mm. Solder those first: their joints are on the top side, under U1 and J1.

Same method as the lock-in board (../../kicad/gen_board.py): load every footprint, assign nets,
place by hand-chosen coordinates, route with a grid router, ground plane on the bottom layer.
Tracks have a width per net; the 3 A path is copper areas (zones), the 5 V input on both layers.

Run with KiCad's Python:  python.exe gen_socket_board.py
Rerunning overwrites smartsocket.kicad_pcb.

Mechanical facts the placement relies on:
- J1, Adafruit 4090 (rev B board file): 20.32 x 13.97 mm, 8 header pins 2.54 mm apart along the
  long side, 11.43 mm behind the socket's front edge. Seen from above with the socket facing the
  left-hand board edge, pin 1 (GND) is at the back (top of the drawing) and pin 8 (CC2) at the front.
- U1, Seeed XIAO ESP32-C3 (Seeed's own footprint): 21 x 17.8 mm, two rows of 7 pins 15.24 mm
  apart, pins 1 and 14 at the USB end. Mounted crosswise with its USB port at the front edge.
  Its BOOT button is at the far end from the USB port, in the D6 corner (photo, 2026-10-16).
- Both modules sit 2.5 mm up on their header plastic; the parts under them are on the underside.
"""
import heapq
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from kisym import parse  # noqa: E402

FP_ROOT = os.path.join(os.path.dirname(os.path.dirname(sys.executable)), "share", "kicad", "footprints")
OUT = os.path.join(HERE, "smartsocket.kicad_pcb")

OX, OY = 100.0, 80.0
W, H = 51.2, 24.0

TRACK = 0.3
NET_W = {"5V_IN": 0.5, "5V_U1": 0.5, "GND": 0.5}
VIA_D, VIA_DRILL = 0.7, 0.3
G = 0.25
PAD_INFL = 0.4
EDGE = 0.75
BOTTOM_COST = 4
VIA_COST = 30

U1L, U1R = 15.8, 31.04    # U1 pin columns (left, right); its pins run front to back
U1_FRONT = 18.87          # U1 pin 1 and pin 14 row (USB end)

# ref: (x, y of pad 1 in mm from the board's top-left corner, direction from pad 1 to pad 2, side)
PLACE = {
    # ---- top side ----
    "J1": (11.43, 3.11, "S", "F"),          # charger socket board, socket out of the left end
    "U1": (U1R, U1_FRONT, "N", "F"),        # controller, crosswise, USB port at the front edge
    "Q1": (38.91, 2.92, "W", "F"),          # power switch: source row at the back, drain row in front
    "C3": (41.0, 2.4, "E", "F"),            # 5 V input capacitor, standing
    "J2": (49.0, 6.0, "S", "F"),            # cut cable to the phone, right end
    "Q2": (33.2, 13.0, "E", "F"),           # gate driver, standing
    "C2": (33.9, 16.6, "S", "F"), "C1": (36.9, 16.6, "S", "F"),
    "LED1": (41.73, 20.5, "E", "F"),        # standing, through a hole in the lid
    # ---- underside, lying flat: under J1 ----
    "R7": (9.22, 8.6, "W", "B"), "R8": (9.22, 20.6, "W", "B"),
    # ---- underside, lying flat: under U1 ----
    "R5": (26.62, 4.4, "W", "B"), "R4": (26.62, 7.5, "W", "B"), "R3": (26.62, 10.6, "W", "B"),
    "R6": (19.0, 13.7, "E", "B"), "R2": (19.0, 16.8, "E", "B"), "D1": (18.0, 20.05, "E", "B"),
    # ---- underside, lying flat: beside Q1 ----
    "R1": (40.6, 4.9, "S", "B"), "R9": (43.7, 10.4, "S", "B"), "R10": (46.8, 9.0, "S", "B"),
}
HOLES = []

# Copper areas for the 3 A path: name -> (polygon in board mm, layers)
ZONES = {
    "5V_IN": ([(10.3, 4.6), (12.45, 4.6), (12.45, 0.8), (42.4, 0.8), (42.4, 4.1), (35.8, 4.1), (35.8, 2.55),
               (14.7, 2.55), (14.7, 6.7), (10.3, 6.7)], ("F", "B")),
    "5V_PHONE": ([(34.3, 6.4), (50.5, 6.4), (50.5, 9.3), (34.3, 9.3)], ("F",)),
}
# Vias joining the top and bottom 5 V input copper
STITCH = {"5V_IN": [(13.6, 1.6), (13.6, 4.5), (20.0, 1.6), (26.0, 1.6), (32.0, 1.6), (37.0, 1.2), (38.3, 1.2), (42.0, 3.7)]}

NX, NY = int(round(W / G)) + 1, int(round(H / G)) + 1
N = NX * NY
F, B = 0, 1
LAYER = {F: pcbnew.F_Cu, B: pcbnew.B_Cu}
VIA_RING = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)] + [(2, 0), (-2, 0), (0, 2), (0, -2)]
DISC3 = [(dx, dy) for dx in range(-3, 4) for dy in range(-3, 4) if dx * dx + dy * dy <= 8]
MOVES = [(1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, 1.414), (1, -1, 1.414), (-1, 1, 1.414), (-1, -1, 1.414)]


def disc(r):
    return [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if dx * dx + dy * dy <= r * r]


def short(name):
    """Schematic nets are exported as /NAME; the script uses NAME."""
    return name[1:] if name.startswith("/") else name


def width(name):
    return NET_W.get(name, TRACK)


def extra(name):
    return int(math.ceil((width(name) - TRACK) / 2 / G - 1e-9))


def kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def kid(node, key):
    found = kids(node, key)
    return found[0] if found else None


def mm(v):
    return pcbnew.FromMM(float(v))


def pt(x, y):
    return pcbnew.VECTOR2I(mm(OX + x), mm(OY + y))


def inside(poly, x, y):
    hit = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


class Router:
    def __init__(self, board):
        self.board = board
        self.occ = [[0] * N, [0] * N]
        self.novia = [False] * N
        self.failed = []
        self.segs = []
        self.via_at = []
        self.length = 0.0
        self.vias = 0
        for iy in range(NY):
            for ix in range(NX):
                x, y = ix * G, iy * G
                if x < EDGE or y < EDGE or x > W - EDGE or y > H - EDGE:
                    self.occ[F][iy * NX + ix] = self.occ[B][iy * NX + ix] = -1

    def claim(self, layer, ix, iy, nid):
        if 0 <= ix < NX and 0 <= iy < NY:
            i = iy * NX + ix
            cur = self.occ[layer][i]
            if cur == 0:
                self.occ[layer][i] = nid
            elif cur != nid:
                self.occ[layer][i] = -1

    def claim_rect(self, layers, x1, y1, x2, y2, nid):
        for iy in range(int(-(-y1 // G)), int(y2 // G) + 1):
            for ix in range(int(-(-x1 // G)), int(x2 // G) + 1):
                for layer in layers:
                    self.claim(layer, ix, iy, nid)

    def ok(self, layer, i, nid):
        cur = self.occ[layer][i]
        return cur == 0 or cur == nid

    def fat_ok(self, layer, ix, iy, nid, offs):
        for dx, dy in offs:
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < NX and 0 <= jy < NY):
                return False
            if not self.ok(layer, jy * NX + jx, nid):
                return False
        return True

    def via_ok(self, ix, iy, nid):
        for dx, dy in VIA_RING:
            jx, jy = ix + dx, iy + dy
            if not (0 <= jx < NX and 0 <= jy < NY):
                return False
            j = jy * NX + jx
            if self.novia[j] or not self.ok(F, j, nid) or not self.ok(B, j, nid):
                return False
        return True

    def seed(self, pad, nid):
        cx, cy = pad["x"], pad["y"]
        best = None
        for iy in range(int(-(-pad["y1"] // G)), int(pad["y2"] // G) + 1):
            for ix in range(int(-(-pad["x1"] // G)), int(pad["x2"] // G) + 1):
                if 0 <= ix < NX and 0 <= iy < NY and all(self.ok(l, iy * NX + ix, nid) for l in pad["layers"]):
                    d = (ix * G - cx) ** 2 + (iy * G - cy) ** 2
                    if best is None or d < best[0]:
                        best = (d, ix, iy)
        return None if best is None else (best[1], best[2])

    def search(self, sources, is_target, nid, tree, offs, top_only=False):
        dist, prev, heap = {}, {}, []
        for s in sources:
            dist[s] = 0.0
            heap.append((0.0, s))
        heapq.heapify(heap)
        while heap:
            d, s = heapq.heappop(heap)
            if d > dist.get(s, 1e18):
                continue
            if is_target(s) and s not in sources:
                path = [s]
                while s in prev:
                    s = prev[s]
                    path.append(s)
                return path[::-1]
            layer, i = divmod(s, N)
            iy, ix = divmod(i, NX)
            w = BOTTOM_COST if layer == B else 1
            for dx, dy, c in MOVES:
                jx, jy = ix + dx, iy + dy
                if not (0 <= jx < NX and 0 <= jy < NY):
                    continue
                j = jy * NX + jx
                t = layer * N + j
                if t not in tree:
                    if not self.ok(layer, j, nid):
                        continue
                    if len(offs) > 1 and not (is_target(t) or self.fat_ok(layer, jx, jy, nid, offs)):
                        continue
                if dx and dy and not (self.ok(layer, iy * NX + jx, nid) and self.ok(layer, jy * NX + ix, nid)):
                    continue
                nd = d + c * w
                if nd < dist.get(t, 1e18):
                    dist[t] = nd
                    prev[t] = s
                    heapq.heappush(heap, (nd, t))
            if not top_only and self.via_ok(ix, iy, nid):
                t = (1 - layer) * N + i
                nd = d + VIA_COST
                if nd < dist.get(t, 1e18):
                    dist[t] = nd
                    prev[t] = s
                    heapq.heappush(heap, (nd, t))
        return None

    def add_track(self, layer, a, b, net):
        if a != b:
            self.segs.append((layer, a, b, net))

    def emit_tracks(self):
        def key(p):
            return (round(p[0] * 1000), round(p[1] * 1000))
        ends = {}
        for layer, a, b, net in self.segs:
            ends.setdefault((net.GetNetCode(), layer), set()).update((key(a), key(b)))
        for code, p in self.via_at:
            for layer in (F, B):
                ends.setdefault((code, layer), set()).add(key(p))
        for layer, a, b, net in self.segs:
            pts = [a]
            steps = int(round(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / G))
            on_grid = all(abs(v / G - round(v / G)) < 1e-6 for v in a + b)
            if on_grid:
                for k in range(1, steps):
                    p = (a[0] + (b[0] - a[0]) * k / steps, a[1] + (b[1] - a[1]) * k / steps)
                    if key(p) in ends[(net.GetNetCode(), layer)]:
                        pts.append(p)
            pts.append(b)
            for p, q in zip(pts, pts[1:]):
                t = pcbnew.PCB_TRACK(self.board)
                t.SetStart(pt(*p))
                t.SetEnd(pt(*q))
                t.SetWidth(mm(width(short(net.GetNetname()))))
                t.SetLayer(LAYER[layer])
                t.SetNet(net)
                self.board.Add(t)
                self.length += ((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2) ** 0.5

    def add_via(self, ix, iy, net, nid):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pt(ix * G, iy * G))
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        v.SetDrill(mm(VIA_DRILL))
        try:
            v.SetWidth(pcbnew.F_Cu, mm(VIA_D))
        except TypeError:
            v.SetWidth(mm(VIA_D))
        v.SetNet(net)
        self.board.Add(v)
        self.vias += 1
        self.via_at.append((net.GetNetCode(), (ix * G, iy * G)))
        for dx, dy in DISC3:
            self.claim(F, ix + dx, iy + dy, nid)
            self.claim(B, ix + dx, iy + dy, nid)

    def lay(self, path, net, nid, tree, claim):
        cells = [divmod(s, N) for s in path]
        k = 0
        while k < len(cells) - 1:
            (la, a), (lb, b) = cells[k], cells[k + 1]
            if la != lb:
                iy, ix = divmod(a, NX)
                self.add_via(ix, iy, net, nid)
                k += 1
                continue
            m = k + 1
            while m + 1 < len(cells) and cells[m + 1][0] == la and cells[m + 1][1] - cells[m][1] == b - a:
                m += 1
            (layer, i), (_, j) = cells[k], cells[m]
            ay, ax = divmod(i, NX)
            by, bx = divmod(j, NX)
            self.add_track(layer, (ax * G, ay * G), (bx * G, by * G), net)
            k = m
        for layer, i in cells:
            iy, ix = divmod(i, NX)
            for dx, dy in claim:
                self.claim(layer, ix + dx, iy + dy, nid)
        tree.update(path)


def load_fp(full):
    lib, name = full.split(":")
    fp = pcbnew.FootprintLoad(os.path.join(FP_ROOT, lib + ".pretty"), name)
    if fp is None:
        raise SystemExit("footprint not found: " + full)
    fp.SetFPID(pcbnew.LIB_ID(lib, name))
    return fp


def add_rect(container, layer, x1, y1, x2, y2, w, local=None):
    """Closed rectangle of four segments. `local` = footprint: coordinates are board mm."""
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        s = pcbnew.PCB_SHAPE(container)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(layer)
        s.SetStart(pt(ax, ay))
        s.SetEnd(pt(bx, by))
        s.SetWidth(mm(w))
        container.Add(s)


def text(board, s, x, y, size=0.8, align=None, layer=pcbnew.F_SilkS, angle=0, mirror=False):
    t = pcbnew.PCB_TEXT(board)
    if mirror:
        t.SetMirrored(True)
    t.SetText(s)
    t.SetLayer(layer)
    t.SetPosition(pt(x, y))
    if align == "right":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_RIGHT)
    elif align == "left":
        t.SetHorizJustify(pcbnew.GR_TEXT_H_ALIGN_LEFT)
    t.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
    t.SetTextThickness(mm(size * 0.15))
    if angle:
        t.SetTextAngleDegrees(angle)
    board.Add(t)


DIRS = {"E": 0.0, "S": 90.0, "W": 180.0, "N": -90.0}


def pad_xy(fp, num):
    p = [q for q in fp.Pads() if q.GetNumber() == num][0].GetPosition()
    return pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)


def place(fp, x, y, way, side):
    """Put pad 1 at (x, y), pad 2 in direction `way` from it, on side F (top) or B (underside)."""
    fp.SetOrientationDegrees(0)
    if side == "B":
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
    (ax, ay), (bx, by) = pad_xy(fp, "1"), pad_xy(fp, "2")
    now = math.degrees(math.atan2(by - ay, bx - ax))
    turn = now - DIRS[way]          # KiCad turns counter-clockwise on screen for a positive angle
    fp.Rotate(fp.GetPosition(), pcbnew.EDA_ANGLE(turn, pcbnew.DEGREES_T))
    ax, ay = pad_xy(fp, "1")
    pos = fp.GetPosition()
    fp.SetPosition(pcbnew.VECTOR2I(pos.x + mm(OX + x - ax), pos.y + mm(OY + y - ay)))


def shape_xiao(fp):
    """DIP-14 7.62 mm wide becomes the XIAO's 15.24 mm: move pins 8 to 14 out, redraw outlines."""
    for pad in fp.Pads():
        if int(pad.GetNumber()) >= 8:
            p = pad.GetFPRelativePosition()
            pad.SetFPRelativePosition(pcbnew.VECTOR2I(p.x + mm(7.62), p.y))
        # Square header pins (0.64 mm, 0.9 mm across the corners) need a bigger hole than an IC lead.
        # Seeed's own XIAO footprint uses 0.889 mm; 1.0 mm is the usual pin header hole.
        pad.SetDrillSize(pcbnew.VECTOR2I(mm(1.0), mm(1.0)))
        pad.SetSize(pcbnew.F_Cu, pcbnew.VECTOR2I(mm(1.7), mm(1.7)))
    for item in list(fp.GraphicalItems()):
        fp.Remove(item)
    fp.Models().clear()      # the library's 3D model is a 7.62 mm chip, not the XIAO
    # Outlines in footprint coordinates: pin 1 at (0, 0), USB end towards negative y
    def seg(layer, ax, ay, bx, by, w):
        s = pcbnew.PCB_SHAPE(fp)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetLayer(layer)
        s.SetStart(pcbnew.VECTOR2I(mm(ax), mm(ay)))
        s.SetEnd(pcbnew.VECTOR2I(mm(bx), mm(by)))
        s.SetWidth(mm(w))
        fp.Add(s)
    def rect(layer, x1, y1, x2, y2, w):
        for a, b in (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))):
            seg(layer, a[0], a[1], b[0], b[1], w)
    # Module body 17.8 x 21, pins centred along its length
    rect(pcbnew.F_Fab, 7.62 - 8.9, 7.62 - 10.5, 7.62 + 8.9, 7.62 + 10.5, 0.1)
    # USB connector, sticking out 1.2 mm past the module end
    rect(pcbnew.F_Fab, 7.62 - 4.5, 7.62 - 11.7, 7.62 + 4.5, 7.62 - 7.0, 0.1)
    # Courtyard: one box per pin row (parts may sit between the rows, under the module)
    for cx in (0.0, 15.24):
        rect(pcbnew.F_CrtYd, cx - 1.1, -1.1, cx + 1.1, 15.24 + 1.1, 0.05)
    # Silkscreen: the module edges beside each pin row, and a pin 1 mark
    for x in (-1.25, 16.49):
        seg(pcbnew.F_SilkS, x, -1.0, x, 16.24, 0.15)
    seg(pcbnew.F_SilkS, -1.25, -1.0, 0.6, -1.0, 0.15)


def grown(bb, d):
    b = pcbnew.BOX2I(bb.GetOrigin(), bb.GetSize())
    b.Inflate(mm(d))
    return b


def place_refs(board):
    """Put each part's name near it, clear of pads, outlines, other parts' bodies and other names.

    Names go on the part's own side. First try spots clear of every other part's courtyard on that
    side; if none is near enough, allow small parts' courtyards. Spots under U1 or J1 are allowed:
    those modules go on last.
    """
    silk = {pcbnew.F_Cu: pcbnew.F_SilkS, pcbnew.B_Cu: pcbnew.B_SilkS}
    yard = {pcbnew.F_Cu: pcbnew.F_CrtYd, pcbnew.B_Cu: pcbnew.B_CrtYd}
    hard = {k: [] for k in silk}
    bodies = {k: [] for k in silk}
    for fp in board.GetFootprints():
        side = fp.GetLayer()
        for pad in fp.Pads():
            for k in silk:
                hard[k].append(grown(pad.GetBoundingBox(), 0.2))
        for g in fp.GraphicalItems():
            for k in silk:
                if g.GetLayer() == silk[k]:
                    hard[k].append(grown(g.GetBoundingBox(), 0.15))
        cy = fp.GetCourtyard(yard[side])
        if cy.OutlineCount() and fp.GetReference() != "U1":
            body = cy.BBox()
            big = body.GetWidth() > mm(5) or body.GetHeight() > mm(5)
            (hard[side] if big else bodies[side]).append(body)
    for d in board.GetDrawings():
        for k in silk:
            if d.GetLayer() == silk[k]:
                hard[k].append(grown(d.GetBoundingBox(), 0.15))
    inner = pcbnew.BOX2I(pt(0.3, 0.3), pcbnew.VECTOR2I(mm(W - 0.6), mm(H - 0.6)))
    steps = [(dx * 0.2, dy * 0.2) for dx in range(-40, 41) for dy in range(-25, 26)]
    steps.sort(key=lambda v: v[0] ** 2 + (1.6 * v[1]) ** 2)
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        if fp.GetReference().startswith("H"):
            continue
        side = fp.GetLayer()
        r = fp.Reference()
        r.SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        r.SetTextThickness(mm(0.12))
        r.SetTextAngleDegrees(0)
        xs = [pcbnew.ToMM(p.GetPosition().x) - OX for p in fp.Pads()]
        ys = [pcbnew.ToMM(p.GetPosition().y) - OY for p in fp.Pads()]
        cx, cyy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        own = fp.GetCourtyard(yard[side])
        own = own.BBox() if own.OutlineCount() else None
        done = False
        for strict in (True, False):
            for dx, dy in steps:
                r.SetPosition(pt(cx + dx, cyy + dy))
                bb = r.GetBoundingBox()
                if not inner.Contains(bb):
                    continue
                if any(bb.Intersects(o) for o in hard[side]):
                    continue
                if strict and any(bb.Intersects(o) for o in bodies[side] if o != own):
                    continue
                hard[side].append(grown(bb, 0.1))
                done = True
                break
            if done:
                break
        if not done:
            print("no free spot for the name of", fp.GetReference())

def build(priority):
    with open(os.path.join(HERE, "smartsocket.net"), encoding="utf-8") as f:
        net = parse(f.read())[0]

    board = pcbnew.NewBoard(OUT)
    ds = board.GetDesignSettings()
    ds.SetCopperLayerCount(2)

    pin_net, net_id, net_item = {}, {}, {}
    for n in kids(kid(net, "nets"), "net"):
        full = str(kid(n, "name")[1])
        name = short(full)
        item = pcbnew.NETINFO_ITEM(board, full)
        board.Add(item)
        net_id[name] = len(net_id) + 1
        net_item[name] = item
        for node in kids(n, "node"):
            pin_net[(str(kid(node, "ref")[1]), str(kid(node, "pin")[1]))] = name

    missing = []
    for comp in kids(kid(net, "components"), "comp"):
        ref = str(kid(comp, "ref")[1])
        fp = load_fp(str(kid(comp, "footprint")[1]))
        fp.SetReference(ref)
        fp.SetValue(str(kid(comp, "value")[1]))
        fp.SetPath(pcbnew.KIID_PATH("/" + str(kid(comp, "tstamps")[1])))
        for key, kind in (("description", pcbnew.FIELD_T_DESCRIPTION), ("datasheet", pcbnew.FIELD_T_DATASHEET)):
            node = kid(comp, key)
            field = fp.GetField(kind)
            if node is not None and field is not None:
                field.SetText(str(node[1]))
        if ref == "U1":
            shape_xiao(fp)
        if ref == "LED1":
            # The LED stands about 1 mm up on its legs so its tip reaches the hole in the lid:
            # its keep-out area is only the legs, and a joint may sit under its rim.
            for item in list(fp.GraphicalItems()):
                if item.GetLayer() == pcbnew.F_CrtYd:
                    fp.Remove(item)
            for (ax, ay), (bx, by) in (((-1.3, -1.3), (3.84, -1.3)), ((3.84, -1.3), (3.84, 1.3)),
                                       ((3.84, 1.3), (-1.3, 1.3)), ((-1.3, 1.3), (-1.3, -1.3))):
                sh = pcbnew.PCB_SHAPE(fp)
                sh.SetShape(pcbnew.SHAPE_T_SEGMENT)
                sh.SetLayer(pcbnew.F_CrtYd)
                sh.SetStart(pcbnew.VECTOR2I(mm(ax), mm(ay)))
                sh.SetEnd(pcbnew.VECTOR2I(mm(bx), mm(by)))
                sh.SetWidth(mm(0.05))
                fp.Add(sh)
        # The wire-pad footprint is marked "not in the bill of materials"; the symbol is not
        fp.SetExcludedFromBOM(False)
        # Copy the symbol's simulation fields (hidden), or the board-against-schematic check reports them missing
        for fld in kids(kid(comp, "fields") or [], "field"):
            fname = str(kid(fld, "name")[1])
            if fname.startswith("Sim."):
                fp.SetField(fname, str(fld[-1]))
                for f in fp.GetFields():
                    if f.GetName() == fname:
                        f.SetVisible(False)
                        f.SetLayer(pcbnew.F_Fab)
        fp.Value().SetVisible(False)
        board.Add(fp)
        if ref not in PLACE:
            missing.append(ref)
            continue
        place(fp, *PLACE[ref])
        for pad in fp.Pads():
            name = pin_net.get((ref, pad.GetNumber()))
            if name is not None:
                pad.SetNet(net_item[name])
    if missing:
        raise SystemExit("no position for: " + ", ".join(missing))

    for k, (x, y) in enumerate(HOLES):
        fp = load_fp("MountingHole:MountingHole_2.2mm_M2")
        fp.SetReference("H%d" % (k + 1))
        fp.SetAttributes(pcbnew.FP_BOARD_ONLY | pcbnew.FP_EXCLUDE_FROM_BOM | pcbnew.FP_EXCLUDE_FROM_POS_FILES)
        fp.Reference().SetVisible(False)
        fp.Value().SetVisible(False)
        board.Add(fp)
        fp.SetPosition(pt(x, y))

    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        seg = pcbnew.PCB_SHAPE(board)
        seg.SetShape(pcbnew.SHAPE_T_SEGMENT)
        seg.SetLayer(pcbnew.Edge_Cuts)
        seg.SetStart(pt(x1, y1))
        seg.SetEnd(pt(x2, y2))
        seg.SetWidth(mm(0.1))
        board.Add(seg)

    # Outlines of the two modules (they overhang parts and joints below them), fabrication layer
    add_rect(board, pcbnew.F_Fab, 0, 12 - 10.16, 13.97, 12 + 10.16, 0.1)

    text(board, "Smart socket rev 0.2  2026-10", 23.0, 23.25, 0.9, layer=pcbnew.B_SilkS, mirror=True)
    text(board, "+5V", 47.1, 6.0, 0.8, align="right")
    text(board, "CC", 47.3, 12.3, 0.8, align="right")
    text(board, "GND", 47.1, 15.2, 0.8, align="right")
    # Which way up the two modules go: J1 upside down or U1 turned round puts 5 V on signal pins
    for name, pin in (("GND", 1), ("VBUS", 2), ("CC1", 4)):
        text(board, name, 9.8, 3.11 + 2.54 * (pin - 1), 0.8, align="right")
    text(board, "J1 parts up", 5.4, 13.6, 0.8)
    text(board, "< socket", 5.4, 15.0, 0.8)
    text(board, "U1 USB this edge", 23.4, 23.1, 0.8)

    # ---- routing ----
    r = Router(board)
    pads_by_net = {}
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            bb = pad.GetBoundingBox()
            x1 = pcbnew.ToMM(bb.GetX()) - OX
            y1 = pcbnew.ToMM(bb.GetY()) - OY
            x2, y2 = x1 + pcbnew.ToMM(bb.GetWidth()), y1 + pcbnew.ToMM(bb.GetHeight())
            pos = pad.GetPosition()
            through = pad.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH)
            name = short(pad.GetNetname())
            nid = net_id.get(name, -1)
            p = {"x": pcbnew.ToMM(pos.x) - OX, "y": pcbnew.ToMM(pos.y) - OY, "x1": x1, "y1": y1, "x2": x2, "y2": y2,
                 "layers": (F, B) if through else (F,), "ref": fp.GetReference() + "." + pad.GetNumber()}
            infl = PAD_INFL + (0.3 if pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH else 0)
            r.claim_rect(p["layers"], x1 - infl, y1 - infl, x2 + infl, y2 + infl, nid)
            for iy in range(int(-(-(y1 - 0.45) // G)), int((y2 + 0.45) // G) + 1):
                for ix in range(int(-(-(x1 - 0.45) // G)), int((x2 + 0.45) // G) + 1):
                    if 0 <= ix < NX and 0 <= iy < NY:
                        r.novia[iy * NX + ix] = True
            if nid > 0:
                pads_by_net.setdefault(name, []).append(p)

    # Zone areas belong to their net on their layers; other nets keep 0.6 mm away
    zone_cells = {}
    for name, (poly, lays) in ZONES.items():
        nid = net_id[name]
        layers = [F if l == "F" else B for l in lays]
        cells = set()
        for iy in range(NY):
            for ix in range(NX):
                x, y = ix * G, iy * G
                if inside(poly, x, y):
                    cells.add(iy * NX + ix)
                    for l in layers:
                        r.claim(l, ix, iy, nid)
                elif any(inside(poly, x + dx * G, y + dy * G) for dx, dy in disc(2)):
                    for l in layers:
                        r.claim(l, ix, iy, nid)
        zone_cells[name] = {l * N + c for c in cells for l in layers if r.occ[l][c] == nid}

    def states(pad, cell):
        return [l * N + cell[1] * NX + cell[0] for l in pad["layers"]]

    def stub(pad, cell, layer, item):
        r.add_track(layer, (pad["x"], pad["y"]), (cell[0] * G, cell[1] * G), item)

    def connect_ends(path, seeds, item):
        for s in (path[0], path[-1]):
            if s in seeds:
                pad, cell = seeds[s]
                stub(pad, cell, s // N, item)

    gnd = "GND"
    nid, item = net_id[gnd], net_item[gnd]
    claim_g = disc(2 + extra(gnd))
    offs_g = disc(extra(gnd))
    tree, seeds, smd = set(), {}, []
    for p in pads_by_net.pop(gnd):
        cell = r.seed(p, nid)
        if cell is None:
            r.failed.append("GND %s (pad boxed in)" % p["ref"])
            continue
        for s in states(p, cell):
            seeds[s] = (p, cell)
        if len(p["layers"]) == 2:
            tree.update(states(p, cell))
        else:
            smd.append((p, cell))
    for p, cell in smd:
        src = set(states(p, cell))

        def hit(s, nid=nid, tree=tree):
            if s in tree:
                return True
            iy, ix = divmod(s % N, NX)
            return s < N and r.via_ok(ix, iy, nid)
        path = r.search(src, hit, nid, tree, offs_g, top_only=True)
        if path is None:
            r.failed.append("GND " + p["ref"])
            continue
        end = path[-1]
        new_via = end not in tree
        r.lay(path, item, nid, tree, claim_g)
        connect_ends(path, seeds, item)
        if new_via:
            iy, ix = divmod(end % N, NX)
            r.add_via(ix, iy, item, nid)
            tree.add(N + end % N)

    def span(name):
        ps = pads_by_net[name]
        return (max(p["x"] for p in ps) - min(p["x"] for p in ps)) + (max(p["y"] for p in ps) - min(p["y"] for p in ps))

    order = sorted((n for n in pads_by_net if len(pads_by_net[n]) > 1),
                   key=lambda n: (priority.index(n) if n in priority else len(priority), span(n)))
    failed_nets = []
    for name in order:
        nid, item = net_id[name], net_item[name]
        claim_n, offs_n = disc(2 + extra(name)), disc(extra(name))
        seeds, todo = {}, {}
        zone = zone_cells.get(name, set())
        for p in pads_by_net[name]:
            if name in ZONES and inside(ZONES[name][0], p["x"], p["y"]):
                continue      # a pad inside its own zone is connected by the fill
            cell = r.seed(p, nid)
            if cell is None:
                r.failed.append("%s %s (pad boxed in)" % (name, p["ref"]))
                continue
            for s in states(p, cell):
                seeds[s] = (p, cell)
                todo[s] = p["ref"]
        if zone:
            tree = set(zone)
        else:
            if not todo:
                continue
            first = next(iter(todo.values()))
            tree = {s for s, ref in todo.items() if ref == first}
            todo = {s: ref for s, ref in todo.items() if ref != first}
        while todo:
            path = r.search(set(tree), lambda s: s in todo, nid, tree, offs_n)
            if path is None:
                r.failed.append("%s: %s" % (name, ", ".join(sorted(set(todo.values())))))
                failed_nets.append(name)
                break
            ref = todo[path[-1]]
            r.lay(path, item, nid, tree, claim_n)
            connect_ends(path, seeds, item)
            for s in [s for s, v in todo.items() if v == ref]:
                tree.add(s)
                del todo[s]

    r.emit_tracks()

    for name, (poly, lays) in ZONES.items():
        for l in lays:
            z = pcbnew.ZONE(board)
            z.SetLayer(pcbnew.F_Cu if l == "F" else pcbnew.B_Cu)
            z.SetNet(net_item[name])
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
            z.SetMinThickness(mm(0.25))
            z.SetAssignedPriority(1)
            o = z.Outline()
            o.NewOutline()
            for x, y in poly:
                o.Append(mm(OX + x), mm(OY + y))
            board.Add(z)
    for name, spots in STITCH.items():
        for x, y in spots:
            r.add_via(int(round(x / G)), int(round(y / G)), net_item[name], net_id[name])

    # Ground on both layers wherever nothing else is: the bottom plane is cut up by the
    # underside parts' tracks, and the top fill joins its pieces through the through-hole pads.
    for layer in (pcbnew.B_Cu, pcbnew.F_Cu):
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNet(net_item[gnd])
        o = z.Outline()
        o.NewOutline()
        for x, y in corners:
            o.Append(mm(OX + x), mm(OY + y))
        board.Add(z)

    place_refs(board)
    pcbnew.SaveBoard(OUT, board)
    print("wrote", OUT)
    print("footprints %d, track %.0f mm, vias %d" % (len(board.GetFootprints()), r.length, r.vias))
    print("not routed:", r.failed if r.failed else "none")
    return failed_nets, bool(r.failed)


def main():
    priority = []
    for attempt in range(8):
        failed_nets, any_failed = build(priority)
        if not failed_nets:
            break
        priority = failed_nets + [n for n in priority if n not in failed_nets]
        print("retrying with first:", priority)
    if any_failed:
        raise SystemExit("routing incomplete")


if __name__ == "__main__":
    main()
