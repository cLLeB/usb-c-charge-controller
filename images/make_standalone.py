"""Make smart-socket-3d-standalone.html from smart-socket-3d.html.

The page published on claude.ai loads its 3D library from the internet and leaves out the
<html> wrapper. The standalone copy is one complete file with the library inside it, so it opens
in any browser, works offline, and can be sent to anyone as an attachment.

Usage:  python make_standalone.py three.min.js OrbitControls.js
(the two files from cdnjs three.js r128 and jsDelivr three@0.128.0/examples/js/controls)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "smart-socket-3d.html")
OUT = os.path.join(HERE, "smart-socket-3d-standalone.html")

CDN = ('<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>\n'
       '<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>')


def main():
    three, orbit = (open(p, encoding="utf-8").read() for p in sys.argv[1:3])
    page = open(SRC, encoding="utf-8").read()
    assert CDN in page, "library lines not found in the page"
    page = page.replace(CDN, "<script>\n" + three + "\n</script>\n<script>\n" + orbit + "\n</script>")
    head_end = page.index("</style>") + len("</style>")
    out = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
           + page[:head_end] + "\n</head>\n<body>\n" + page[head_end:] + "\n</body>\n</html>\n")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("wrote", OUT, len(out) // 1024, "kB")


if __name__ == "__main__":
    main()
