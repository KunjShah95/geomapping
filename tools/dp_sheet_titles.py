"""What is each of the four part sheets? Read the big type off each."""
import os, sys
import fitz
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dp_georef as G

blocks, grid = G.build_blocks()
for q, b in blocks.items():
    doc = fitz.open(b.path); page = doc[0]
    d = page.get_text("dict")
    big = []
    for blk in d.get("blocks", []):
        if blk.get("type") != 0: continue
        for line in blk.get("lines", []):
            for sp in line.get("spans", []):
                t = sp["text"].strip()
                if not t: continue
                sz = sp.get("size", 0)
                if sz >= 13:
                    big.append((round(sz,1), round(sp["bbox"][0]), round(sp["bbox"][1]), t))
    big.sort(key=lambda r: (-r[0], r[1], r[2]))
    print("=" * 80)
    print(q, b.path.split("\\")[-1])
    for sz, x, y, t in big[:30]:
        print(f"   {sz:5.1f}pt  @({x:6d},{y:6d})  {t[:70]}")
    doc.close()
