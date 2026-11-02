# Builds the STL files for the smart socket case without OpenSCAD.
# Same geometry and numbers as case.scad (keep the two in step).
#
# Needs: numpy, trimesh, manifold3d, matplotlib
#   venv/Scripts/python make_case_stl.py
#
# Writes, next to this file:
#   socket-base.stl     floor down
#   socket-lid.stl      outer face down (already turned over for printing)
#   socket-case-render.png
#
# No soldering to fit it: the phone cable drops into a slot open at the top of the end wall, and a
# tongue under the lid closes the slot and clamps the cable. The lid is a friction fit with a finger
# notch, so the case opens again to look inside.

import os
import numpy as np
import trimesh
from manifold3d import Manifold

HERE = os.path.dirname(os.path.abspath(__file__))
FN = 48

# ---- from the board file (x from the charger end, y from the back edge, mm) ----
board_l, board_w, board_t = 51.2, 24.0, 1.6
j1_y = 12.0          # charger socket centre across the board
led_x, led_y = 43.0, 20.5
button_x, button_y = 29.5, 2.0   # U1 BOOT button
usb_x = 23.42        # U1's own USB-C port, centre (between its pin columns 15.8 and 31.04), front edge
j2_x = 49.0          # J2 holes (cable wires come up out of them)

# ---- measured on the assembled board, 2026-10-28 ----
under_h = 3.4        # resistors and diode flat on the underside, plus joints
led_top = 9.0        # LED tip above the board (measured)
j1_top = 7.2         # charger socket top: blue board at 4 mm + 3.2 mm socket
top_h = 9.0          # inside height above the board: the LED tip touches the lid, in its hole
j1_z = 5.6           # charger socket centre height (blue board top 4.0 + 1.6)
sock_w, sock_h = 9.2, 3.4
usb_z = 5.1          # U1's USB-C centre height (estimate: XIAO on 2.5 mm header plastic)
button_top = 4.5
cable_d = 3.4        # cable, measured by paper strip
cable_z = 4.5        # cable centre above the board where it leaves through the end wall
bay = 9.0            # room past the board's right end for the cable wires to bend round

# ---- printing choices ----
wall = 1.6
thin = 0.6           # wall left in front of the charger socket
gap = 0.4
lip = 1.2
lip_w = 1.2
fit = 0.15
rail = 1.0
skin = 0.6
usb_win = (13.4, 8.0)   # opening for a USB-C plug's moulded grip into U1's port (w, from 1 mm above the board up)
slot_w = cable_d        # slot the cable is pressed down into (hole at the bottom is 0.2 mm bigger)

in_l = board_l + gap + bay
in_w = board_w + 2 * gap
out_l = in_l + 2 * wall
out_w = in_w + 2 * wall
out_h = wall + under_h + board_t + top_h


def cx(x): return wall + x
def cy(y): return wall + gap + (board_w - y)   # board y runs from the back edge, case y from the front wall
def cz(z): return wall + under_h + board_t + z


def cube(size, at=(0, 0, 0)):
    return Manifold.cube(tuple(float(s) for s in size)).translate(tuple(float(a) for a in at))


def cyl(d, h, at=(0, 0, 0), fn=FN, d2=None):
    r2 = (d if d2 is None else d2) / 2.0
    return Manifold.cylinder(float(h), d / 2.0, r2, fn).translate(tuple(float(a) for a in at))


def xcyl(d, h, at, fn=FN, d2=None):
    """Cylinder along +x starting at `at`."""
    c = Manifold.cylinder(float(h), d / 2.0, (d if d2 is None else d2) / 2.0, fn).rotate((0, 90, 0))
    return c.translate(tuple(float(a) for a in at))


def union(parts):
    out = parts[0]
    for p in parts[1:]:
        out = out + p
    return out


cab_y, cab_z = cy(j1_y), cz(cable_z)   # cable in the middle of the end wall
collar_l = 5.0


def base():
    b = cube((out_l, out_w, out_h)) - cube((in_l, in_w, out_h), (wall, wall, wall))
    parts = [b]
    for y in (wall, out_w - wall - rail):                   # rails under the long board edges
        parts.append(cube((board_l + gap, rail, under_h), (wall, y, wall)))
    # stops at the board's right end, at the corners only (the cable wires pass in the middle)
    stop_x = cx(board_l + gap)
    for y0 in (wall, out_w - wall - 3.0):
        parts.append(cube((1.2, 3.0, under_h + board_t + 1.0), (stop_x, y0, wall)))
    parts.append(xcyl(cable_d + 3.2, collar_l, (out_l, cab_y, cab_z), d2=cable_d + 1.6))   # collar
    b = union(parts)
    # charger socket: wall thinned from inside, then a socket-shaped hole
    b = b - cube((wall - thin + 0.02, 14, 8), (thin, cy(j1_y) - 7, cz(j1_z) - 4))
    r = 1.2
    sock = (cube((wall + 2, sock_w - 2 * r, sock_h), (-1, cy(j1_y) - sock_w / 2 + r, cz(j1_z) - sock_h / 2)) +
            cube((wall + 2, sock_w, sock_h - 2 * r), (-1, cy(j1_y) - sock_w / 2, cz(j1_z) - sock_h / 2 + r)))
    for dy in (-1, 1):
        for dz in (-1, 1):
            sock = sock + xcyl(2 * r, wall + 2, (-1, cy(j1_y) + dy * (sock_w / 2 - r), cz(j1_z) + dz * (sock_h / 2 - r)), fn=24)
    b = b - sock
    # U1's own USB port: an opening in the front wall, open to the top, so a plug's grip goes in
    w, _ = usb_win
    b = b - cube((w, wall + 2, out_h), (cx(usb_x) - w / 2, -1, cz(1.0)))   # front wall (y = 0)
    # phone cable: round seat plus a slot up to the top, through the wall and the collar
    b = b - xcyl(cable_d + 0.2, wall + collar_l + 2, (out_l - wall - 1, cab_y, cab_z))
    b = b - cube((wall + collar_l + 2, slot_w, out_h), (out_l - wall - 1, cab_y - slot_w / 2, cab_z))
    # finger notch in the back wall's top edge, to lift the lid
    b = b - cube((12, wall + 2, 1.5), (out_l / 2 - 6, out_w - wall - 1, out_h - 1.5))
    return b


def lid():
    """As it sits on the base, underside at z = 0 (so z = 0 is the base's top edge, out_h)."""
    L = [cube((out_l, out_w, wall))]
    ring = cube((in_l - 2 * fit, in_w - 2 * fit, lip + 0.01)) - cube(
        (in_l - 2 * fit - 2 * lip_w, in_w - 2 * fit - 2 * lip_w, lip + 2), (lip_w, lip_w, -1))
    L.append(ring.translate((wall + fit, wall + fit, -lip)))
    # tongue that closes the cable slot from above and presses the cable into its seat
    t_len = wall + collar_l
    top_of_collar = cab_z + (cable_d + 3.2) / 2
    tongue = cube((t_len - 0.2, slot_w - 0.3, out_h - cab_z + 0.01), (out_l - wall + 0.1, cab_y - (slot_w - 0.3) / 2, cab_z - out_h))
    tongue = tongue - xcyl(cable_d - 0.2, t_len + 2, (out_l - wall - 1, cab_y, cab_z - out_h))   # 0.2 mm squeeze
    # outside the wall the tongue only needs to reach the collar's top
    tongue = tongue - cube((collar_l + 1, slot_w + 2, out_h), (out_l, cab_y - slot_w / 2 - 1, top_of_collar - out_h))
    tongue = tongue + cube((collar_l - 0.1, slot_w - 0.3, wall), (out_l, cab_y - (slot_w - 0.3) / 2, top_of_collar - out_h))
    # join the collar part of the tongue to the lid: a bridge over the slot outside the wall
    bridge = cube((collar_l - 0.1, slot_w + 3.0, out_h - top_of_collar + wall), (out_l, cab_y - (slot_w + 3.0) / 2, top_of_collar - out_h))
    L.append(tongue)
    L.append(bridge)
    # tab filling the USB opening's top above the plug grip (keeps the opening tidy)
    lid_m = union(L)
    # ring lip must not block the USB opening or the cable
    w, h = usb_win
    lid_m = lid_m - cube((w, lip_w + fit + 1, lip + 1), (cx(usb_x) - w / 2, wall + fit - 0.5, -lip - 0.5))
    lid_m = lid_m - cube((lip_w + fit + 1, cable_d + 4, lip + 1), (out_l - wall - fit - lip_w - 0.5, cab_y - cable_d / 2 - 2, -lip - 0.5))
    # LED hole and the button
    lid_m = lid_m - cyl(5.05, wall + 2, (cx(led_x), cy(led_y), -1), fn=60)
    bx, by = cx(button_x), cy(button_y)
    lid_m = lid_m - cyl(7, wall, (bx, by, -0.01))                 # thinned from inside...
    lid_skin = cyl(7, skin, (bx, by, wall - skin))                 # ...leaving the 0.6 mm skin
    lid_m = lid_m + lid_skin
    ring_groove = cyl(7.4, 0.3, (bx, by, wall - 0.3)) - cyl(6.6, 0.31, (bx, by, wall - 0.31))
    lid_m = lid_m - ring_groove
    post_len = (top_h - button_top - 0.3) + (wall - skin)
    lid_m = lid_m + cyl(2.2, post_len, (bx, by, wall - skin - post_len), fn=24)
    return lid_m


def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                           faces=np.asarray(mesh.tri_verts), process=True)


def main():
    b = base()
    l_asm = lid()
    l_on = l_asm.translate((0, 0, out_h))
    # print orientation: turned over (rotate 180 about x), outer face down
    l_print = l_asm.rotate((180.0, 0.0, 0.0))
    lo = l_print.bounding_box()
    l_print = l_print.translate((-lo[0], -lo[1], -lo[2]))
    results = {}
    for name, m in (("socket-base.stl", b), ("socket-lid.stl", l_print)):
        t = to_trimesh(m)
        t.export(os.path.join(HERE, name))
        ext = t.bounds[1] - t.bounds[0]
        results[name] = t
        print(f"{name:16s} watertight={t.is_watertight} volume={t.volume / 1000:.1f} cm3 "
              f"size={ext[0]:.1f} x {ext[1]:.1f} x {ext[2]:.1f} mm")
    print(f"closed case {out_l:.1f} x {out_w:.1f} x {out_h + wall:.1f} mm (+{collar_l:.0f} mm cable collar)")
    print("overlap base/lid (mm3):", round((b ^ l_on).volume(), 3))

    # dummy board and parts, to check nothing hits the case
    brd = cube((board_l, board_w, board_t), (cx(0), wall + gap, cz(-board_t)))
    led = cyl(5.0, led_top, (cx(led_x), cy(led_y), cz(0)))
    j1 = cube((14.0, 20.3, j1_top), (cx(0), cy(j1_y) - 10.15, cz(0)))
    # U1's USB-C port must sit in the front opening
    usb = cube((9.0, 7.5, 3.2), (cx(usb_x) - 4.5, cy(24.0) - 1.0, cz(usb_z) - 1.6))
    cable = xcyl(cable_d, 20, (cx(board_l) + 2, cab_y, cab_z))
    for nm, d in (("board", brd), ("LED", led), ("J1", j1), ("cable", cable), ("U1 USB", usb)):
        print(f"overlap {nm:5s} with base/lid (mm3): {round((d ^ b).volume(), 3)} / {round((d ^ l_on).volume(), 3)}")
    render(b, l_on, brd, led, cable)


def render(b, l_on, brd, led, cable):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    def draw(ax, items, title, elev=28, azim=-60):
        allv = []
        for m, col, alpha in items:
            t = to_trimesh(m)
            v_, f_ = trimesh.remesh.subdivide_to_size(t.vertices, t.faces, max_edge=2.0)
            t = trimesh.Trimesh(v_, f_, process=False)
            n = t.face_normals
            k = np.array([0.3, -0.5, 0.8]); k = k / np.linalg.norm(k)
            shade = 0.45 + 0.55 * np.clip(n @ k, 0, 1)
            fc = np.clip(np.array(matplotlib.colors.to_rgb(col))[None, :] * shade[:, None], 0, 1)
            ax.add_collection3d(Poly3DCollection(t.vertices[t.faces], facecolors=np.c_[fc, np.full(len(fc), alpha)],
                                                 edgecolors="none"))
            allv.append(t.vertices)
        v = np.vstack(allv); lo, hi = v.min(0), v.max(0); c = (lo + hi) / 2; r = (hi - lo).max() / 2
        ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
        ax.set_box_aspect((1, 1, 1)); ax.view_init(elev=elev, azim=azim); ax.set_axis_off()
        ax.set_title(title, fontsize=11)

    fig = plt.figure(figsize=(16, 11))
    draw(fig.add_subplot(2, 2, 1, projection="3d"),
         [(b, "#4a78b5", 1), (l_on, "#d9d9d9", 0.35), (brd, "#2e8b57", 1), (led, "#d03030", 1), (cable, "#f2f2f2", 1)],
         "Assembled (lid see-through), charger end left, cable right")
    lift = 18
    draw(fig.add_subplot(2, 2, 2, projection="3d"),
         [(b, "#4a78b5", 1), (brd.translate((0, 0, lift)), "#2e8b57", 1), (l_on.translate((0, 0, 2 * lift)), "#d9d9d9", 1)],
         "Exploded: base, board, lid", elev=22, azim=-50)
    draw(fig.add_subplot(2, 2, 3, projection="3d"), [(b, "#4a78b5", 1), (cable, "#f2f2f2", 1)],
         "Cable end: slot open at the top, press the cable in", elev=25, azim=-20)
    draw(fig.add_subplot(2, 2, 4, projection="3d"), [(b, "#4a78b5", 1)],
         "Front: opening for U1's own USB-C port", elev=15, azim=-100)
    fig.tight_layout()
    out = os.path.join(HERE, "socket-case-render.png")
    fig.savefig(out, dpi=100)
    print("render:", out)


if __name__ == "__main__":
    main()
