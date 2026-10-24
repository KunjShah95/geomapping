"""Diagnose the text structure of the DP sheets: what is a label and what is noise."""
import collections
import re
import sys

import fitz

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    r"NEW DP BOOK AUDA\AUDA FULL DP 4 PART\DP 2021 AUDA Final-2.pdf"

PLOT = re.compile(r"^\d{1,5}(?:[/+][A-Za-z0-9]{1,6})*$")

doc = fitz.open(PATH)
page = doc[0]
d = page.get_text("dict")

bylen = collections.Counter()
bysize = collections.Counter()
ex = collections.defaultdict(list)
nospan = 0
for b in d["blocks"]:
    if b.get("type") != 0:
        continue
    for l in b.get("lines", []):
        for s in l.get("spans", []):
            t = s["text"].strip()
            if not t:
                continue
            nospan += 1
            if PLOT.match(t):
                bylen[len(t)] += 1
                bysize[round(s.get("size", 0), 1)] += 1
                if len(ex[len(t)]) < 5:
                    ex[len(t)].append((t, round(s["bbox"][0]), round(s["bbox"][1]),
                                      round(s.get("size", 0), 1)))

print("total spans:", nospan)
print("plot-like by text length:", sorted(bylen.items()))
print("plot-like by font size  :", sorted(bysize.items()))
for k in sorted(ex):
    print("  len", k, ex[k][:4])

# also: how many spans are NOT plot-like, and what do they look like?
other = collections.Counter()
bysize_text = collections.defaultdict(list)
for b in d["blocks"]:
    if b.get("type") != 0:
        continue
    for l in b.get("lines", []):
        for s in l.get("spans", []):
            t = s["text"].strip()
            if t and not PLOT.match(t):
                sz = round(s.get("size", 0), 1)
                other[sz] += 1
                if len(bysize_text[sz]) < 14:
                    bysize_text[sz].append(t)
print("non-plot spans by size:", sorted(other.items(), key=lambda kv: -kv[1])[:10])
for sz in sorted(bysize_text, key=lambda s: -other[s]):
    print(f"\n  size {sz}  ({other[sz]} spans)")
    print("     ", bysize_text[sz])

# what the plot-like spans look like at each size
print("\nplot-like samples by size:")
seen = collections.defaultdict(list)
for b in d["blocks"]:
    if b.get("type") != 0:
        continue
    for l in b.get("lines", []):
        for s in l.get("spans", []):
            t = s["text"].strip()
            if t and PLOT.match(t):
                sz = round(s.get("size", 0), 1)
                if len(seen[sz]) < 10:
                    seen[sz].append(t)
for sz in sorted(seen, key=lambda s: -len(seen[s])):
    print(f"   size {sz}: {seen[sz]}")
