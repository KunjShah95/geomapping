import urllib.request

BUCKET = "https://s3-ap-south-1.amazonaws.com/tpmap-auda"
PREFIX = "AUDAGUDA2023-Final"
UA = {"User-Agent": "Mozilla/5.0"}


def probe(url):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get("Content-Type"), r.headers.get("Content-Length")
    except Exception as e:
        return "ERR", str(e)[:70], None


cx, cy = 2873, 1778  # approx centre at z12
print("== bucket listing ==")
print(probe(BUCKET + "/?list-type=2&max-keys=5"))
print()
for z in (11, 12, 13, 14, 15, 16, 17, 18, 19, 20):
    n = 2 ** z
    sx = cx * (2 ** (z - 12))
    sy = cy * (2 ** (z - 12))
    if sx >= n or sy >= n:
        continue
    variants = {
        "xyz": f"{PREFIX}/{z}/{sx}/{sy}.png",
        "tms": f"{PREFIX}/{z}/{sx}/{n - 1 - sy}.png",
    }
    for name, key in variants.items():
        st, ct, cl = probe(f"{BUCKET}/{key}")
        flag = "  <== HIT" if st == 200 else ""
        print(f"z={z:2d} {name} {key:45s} {st} {ct} {cl}{flag}")
