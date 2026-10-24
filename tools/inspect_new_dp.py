"""Probe the NEW DP BOOK AUDA PDFs: geometry, embedded rasters, text layers, georeference keys."""
import fitz, os, json, sys

BASE = r"C:\ideabot internships\geomapping\NEW DP BOOK AUDA"

TARGETS = [
    os.path.join(BASE, "BOOK TITLE.pdf"),
    os.path.join(BASE, "Key Map-New.pdf"),
    os.path.join(BASE, "MAP NAME LIST 2.pdf"),
    os.path.join(BASE, "AUDA FULL DP 4 PART", "DP 2021 AUDA Final-1.pdf"),
    os.path.join(BASE, "DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER", "A Key Map.pdf"),
    os.path.join(BASE, "DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER", "D-13.pdf"),
    os.path.join(BASE, "DP PLAN ALAG ALAG PAGE WITH ORIGINAL NUMBER", "H-05.pdf"),
    os.path.join(BASE, "AUDA DP EACH  VILLAGE MAP", "THALTEJ.pdf"),
]


def probe(path):
    print("=" * 100)
    print(path.replace(BASE, "<BASE>"), f"({os.path.getsize(path)/1e6:.1f} MB)")
    doc = fitz.open(path)
    print(f"  pages={doc.page_count}  metadata={ {k: v for k, v in doc.metadata.items() if v} }")
    for i in range(min(doc.page_count, 4)):
        p = doc[i]
        r = p.rect
        print(f"  -- page {i+1}: rect={r} rot={p.rotation} mediabox={p.mediabox}")
        txt = p.get_text().strip()
        print(f"     text layer: {len(txt)} chars")
        if txt:
            print("     text[:400]:", repr(txt[:400]))
        imgs = p.get_images(full=True)
        print(f"     images: {len(imgs)}")
        for xref, *rest in imgs:
            info = doc.extract_image(xref)
            print(f"       xref={xref} {info['width']}x{info['height']} {info['ext']} cs={info.get('colorspace')} bytes={len(info['image'])}")
        # drawing / vector content
        try:
            dr = p.get_drawings()
            print(f"     vector drawings: {len(dr)}")
        except Exception as e:
            print("     drawings err", e)
        for key in ("Measure", "GPTS", "LGIDict", "VP", "Metadata"):
            try:
                v = p.xref_get_key(p.xref, key)
                if v and v[0] != "null":
                    print(f"     {key} = {str(v)[:400]}")
            except Exception:
                pass
    doc.close()


for t in TARGETS:
    if os.path.exists(t):
        try:
            probe(t)
        except Exception as e:
            print("ERR", t, e)
    else:
        print("MISSING", t)
