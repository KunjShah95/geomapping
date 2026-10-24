"""Dump GeoPDF /VP and /Measure dictionaries to recover the ground coordinate space."""
import fitz, os, sys, re, json

BASE = r"C:\ideabot internships\geomapping\NEW DP BOOK AUDA"
FILES = [
    os.path.join(BASE, "AUDA DP EACH  VILLAGE MAP", "THALTEJ.pdf"),
    os.path.join(BASE, "AUDA DP EACH  VILLAGE MAP", "BOL.pdf"),
    os.path.join(BASE, "AUDA DP EACH  VILLAGE MAP", "ASARVA.pdf"),
    os.path.join(BASE, "AUDA FULL DP 4 PART", "DP 2021 AUDA Final-1.pdf"),
    os.path.join(BASE, "AUDA FULL DP 4 PART", "DP 2021 AUDA Final-4.pdf"),
]


def dump(path):
    print("=" * 110)
    print(os.path.relpath(path, BASE))
    doc = fitz.open(path)
    for xref in range(1, doc.xref_length()):
        try:
            s = doc.xref_object(xref, compressed=False)
        except Exception:
            continue
        if "/VP" in s or "/Measure" in s:
            print(f"  --- xref {xref}:")
            for line in s.splitlines():
                if any(k in line for k in ("/VP", "/Measure", "/GCS", "/GPTS", "/BBox", "/W", "/H", "/Matrix", "/S", "/WKT", "/Bounds")):
                    print("      ", line.strip()[:220])
    doc.close()


for f in FILES:
    if os.path.exists(f):
        dump(f)
    else:
        print("missing", f)
