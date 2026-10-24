import fitz, sys, json

path = r"C:\Users\jksha\Downloads\audareviseddevelopmentplan2022map04112022064034976.pdf"
doc = fitz.open(path)
print("pages:", doc.page_count)
print("metadata:", json.dumps(doc.metadata, indent=2)[:1500])
print("is_pdf:", doc.is_pdf, "needs_pass:", doc.needs_pass, "is_repaired:", doc.is_repaired)
try:
    print("pdf_catalog:", doc.pdf_catalog())
except Exception as e:
    print("pdf_catalog err:", e)

sizes = {}
for i, page in enumerate(doc):
    r = page.rect
    key = (round(r.width, 2), round(r.height, 2), page.rotation)
    sizes.setdefault(key, []).append(i + 1)
print("\ndistinct page geometries:")
for k, v in sizes.items():
    print(f"  {k} -> {len(v)} pages; first={v[:8]}{'...' if len(v) > 8 else ''}")

print("\nper-page detail (first 15):")
for i in range(min(15, doc.page_count)):
    p = doc[i]
    xref = p.xref
    gp = p.get("gpts") if hasattr(p, "get") else None
    print(f"  p{i+1}: rect={p.rect} rot={p.rotation} mediabox={p.mediabox} cropbox={p.cropbox}")
    for key in ("Measure", "GPTS", "LGIDict", "VP", "Metadata"):
        try:
            v = p.xref_get_key(xref, key)
            if v and v[0] != "null":
                print(f"        {key} = {str(v)[:300]}")
        except Exception:
            pass
doc.close()
