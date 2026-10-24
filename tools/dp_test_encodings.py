"""Compare tile encodings on real tiles from the new pyramid.

The plan is flat vector artwork with hairline boundaries and small text, which
is the case where JPEG's ringing around 1 px black lines is most visible and
PNG's lossless-ness is most expensive. This measures both.
"""
import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "work", "tiles_test", "15")
OUT = os.path.join(ROOT, "work", "newdp", "encodings")
os.makedirs(OUT, exist_ok=True)

samples = []
for dp, dn, fn in os.walk(SRC):
    for f in fn:
        if f.endswith(".png"):
            samples.append(os.path.join(dp, f))
samples.sort()
# take a spread: a dense one and a sparse one
picks = [samples[len(samples) // 3], samples[len(samples) // 2], samples[-3]]

for p in picks:
    im = Image.open(p).convert("RGB")
    base = os.path.splitext(os.path.basename(p))[0]
    tag = os.path.basename(os.path.dirname(os.path.dirname(p)))
    print(f"\n{os.path.relpath(p, ROOT)}")
    rows = []
    for name, ext, save in [
        ("png24", "png", lambda f: im.save(f, optimize=True)),
        ("png8", "png", lambda f: im.convert("P", palette=Image.ADAPTIVE, colors=256).save(f, optimize=True)),
        ("webp82", "webp", lambda f: im.save(f, "WEBP", quality=82, method=6)),
        ("webp90", "webp", lambda f: im.save(f, "WEBP", quality=90, method=6)),
        ("jpg88", "jpg", lambda f: im.save(f, quality=88, optimize=True, subsampling=0)),
    ]:
        f = os.path.join(OUT, f"{tag}_{base}_{name}.{ext}")
        save(f)
        rows.append((name, ext, f, os.path.getsize(f)))
    for name, ext, f, sz in rows:
        import numpy as np
        a = np.asarray(im, dtype=np.int16)
        b = np.asarray(Image.open(f).convert("RGB"), dtype=np.int16)
        d = np.abs(a - b)
        print(f"   {name:8s} {sz/1024:7.1f} KB   maxdiff {d.max():3d}   meandiff {d.mean():.3f}")
