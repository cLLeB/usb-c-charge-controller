"""Draw smartsocket.kicad_pcb as a flat picture (board-layout.png): top copper red, bottom blue.

KiCad's own exports are SVG and PDF; this gives a PNG that opens anywhere.
Run with KiCad's Python:  python.exe draw_board.py
"""
import os

import pcbnew
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
S = 22                    # pixels per mm
M = 2.0                   # margin, mm
BOARD = os.path.join(HERE, "smartsocket.kicad_pcb")
OUT = os.path.join(HERE, "board-layout.png")

TOP = (200, 50, 40, 170)
BOT = (40, 90, 210, 110)
PAD = (205, 160, 40, 255)


def main():
    b = pcbnew.LoadBoard(BOARD)
    bb = b.GetBoardEdgesBoundingBox()
    x0, y0 = pcbnew.ToMM(bb.GetX()) - M, pcbnew.ToMM(bb.GetY()) - M
    w, h = pcbnew.ToMM(bb.GetWidth()) + 2 * M, pcbnew.ToMM(bb.GetHeight()) + 2 * M

    def px(v):
        return ((pcbnew.ToMM(v.x) - x0) * S, (pcbnew.ToMM(v.y) - y0) * S)

    base = Image.new("RGBA", (int(w * S), int(h * S)), (250, 250, 247, 255))
    d0 = ImageDraw.Draw(base)
    d0.rectangle([M * S, M * S, (w - M) * S, (h - M) * S], fill=(232, 238, 230, 255), outline=(30, 30, 30, 255), width=2)

    layers = {}
    for lay, col in ((pcbnew.B_Cu, BOT), (pcbnew.F_Cu, TOP)):
        img = Image.new("RGBA", base.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for z in b.Zones():
            if z.GetLayer() != lay:
                continue
            polys = z.GetFilledPolysList(lay)
            for i in range(polys.OutlineCount()):
                o = polys.Outline(i)
                d.polygon([px(o.CPoint(k)) for k in range(o.PointCount())], fill=col)
                for hcount in range(polys.HoleCount(i)):
                    hole = polys.Hole(i, hcount)
                    d.polygon([px(hole.CPoint(k)) for k in range(hole.PointCount())], fill=(0, 0, 0, 0))
        for t in b.GetTracks():
            if t.GetClass() == "PCB_TRACK" and t.GetLayer() == lay:
                r = pcbnew.ToMM(t.GetWidth()) * S / 2
                a, c = px(t.GetStart()), px(t.GetEnd())
                d.line([a, c], fill=col, width=int(2 * r))
                for p in (a, c):
                    d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=col)
        layers[lay] = img
    base = Image.alpha_composite(base, layers[pcbnew.B_Cu])
    base = Image.alpha_composite(base, layers[pcbnew.F_Cu])
    d = ImageDraw.Draw(base)

    for fp in b.GetFootprints():
        for p in fp.Pads():
            box = p.GetBoundingBox()
            a = px(box.GetOrigin())
            c = px(pcbnew.VECTOR2I(box.GetRight(), box.GetBottom()))
            if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH:
                d.ellipse([a, c], outline=(60, 60, 60, 255), width=2)
                continue
            if p.GetShape(pcbnew.F_Cu) in (pcbnew.PAD_SHAPE_CIRCLE, pcbnew.PAD_SHAPE_OVAL):
                d.ellipse([a, c], fill=PAD)
            else:
                d.rectangle([a, c], fill=PAD)
            if p.HasHole():
                dr = pcbnew.ToMM(p.GetDrillSize().x) * S / 2
                q = px(p.GetPosition())
                d.ellipse([q[0] - dr, q[1] - dr, q[0] + dr, q[1] + dr], fill=(250, 250, 247, 255))
    for t in b.GetTracks():
        if t.GetClass() == "PCB_VIA":
            q = px(t.GetPosition())
            r = 0.35 * S
            d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=(90, 90, 90, 255))
            r = 0.15 * S
            d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=(250, 250, 247, 255))

    try:
        font = ImageFont.truetype("arial.ttf", int(0.9 * S))
    except OSError:
        font = ImageFont.load_default()
    # Module outlines (fabrication layer) dashed-looking thin lines, and part names
    for dw in list(b.GetDrawings()) + [g for fp in b.GetFootprints() for g in fp.GraphicalItems()]:
        if dw.GetLayer() == pcbnew.F_Fab and dw.GetClass() == "PCB_SHAPE" and dw.GetShape() == pcbnew.SHAPE_T_SEGMENT:
            d.line([px(dw.GetStart()), px(dw.GetEnd())], fill=(90, 90, 90, 255), width=1)
    for fp in b.GetFootprints():
        ref = fp.GetReference()
        if ref.startswith("H"):
            continue
        q = px(fp.Reference().GetPosition())
        under = fp.GetLayer() == pcbnew.B_Cu
        d.text(q, ref, fill=(30, 60, 170, 255) if under else (20, 20, 20, 255), font=font, anchor="mm")
        if under:      # outline of parts on the underside
            cy = fp.GetCourtyard(pcbnew.B_CrtYd)
            if cy.OutlineCount():
                bb2 = cy.BBox()
                d.rectangle([px(bb2.GetOrigin()), px(pcbnew.VECTOR2I(bb2.GetRight(), bb2.GetBottom()))], outline=(30, 60, 170, 255), width=2)
    base.convert("RGB").save(OUT)
    print("wrote", OUT, base.size)


if __name__ == "__main__":
    main()
