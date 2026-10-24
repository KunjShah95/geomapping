"""Upload tiles and derived data to Cloudflare R2 (S3-compatible).

Why R2: 172 MB of tiles on S3 means egress charges every time a user zooms the
map. R2 has no egress fee, which for a tile server is the entire cost argument.

Credentials come from the environment (never from this file):
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET

Usage
    python tools/upload_r2.py --dry-run           # plan + size, uploads nothing
    python tools/upload_r2.py --prefix tiles/     # real upload
    python tools/upload_r2.py --local-out out/    # write the manifest locally

With no credentials the script still produces the manifest and exits cleanly, so
CI can build artefacts before secrets exist.
"""
import argparse, json, os, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# immutable content: name changes whenever bytes change, so cache forever
CACHE_CONTROL = "public, max-age=31536000, immutable"
CONTENT_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                 ".json": "application/json", ".geojson": "application/geo+json",
                 ".pgw": "text/plain", ".webp": "image/webp"}


def have_creds():
    return all(os.environ.get(k) for k in
               ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024


def walk(prefix):
    base = ROOT / prefix
    if not base.exists():
        return
    for p in sorted(base.rglob("*")):
        if p.is_file():
            yield p, p.relative_to(ROOT).as_posix()


def build_manifest(prefixes):
    entries = []
    for prefix in prefixes:
        n = 0
        for _p, rel in walk(prefix):
            entries.append({"key": rel, "size": (ROOT / rel).stat().st_size})
            n += 1
        if n:
            print(f"  {prefix:24s} {n:7d} files")
    total = sum(e["size"] for e in entries)
    return entries, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prefix", action="append", default=[],
                    help="directory to upload (repeatable). default: the DP 2021 tiles and data")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    prefixes = a.prefix or ["tiles_dp2021", "data/dp2021"]
    print("scanning artefacts ...")
    entries, total = build_manifest(prefixes)
    if a.limit:
        entries = entries[: a.limit]
        total = sum(e["size"] for e in entries)
    print(f"\n{len(entries)} files, {human(total)}")

    manifest = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "file_count": len(entries),
        "total_bytes": total,
        "prefixes": prefixes,
        "cache_control": CACHE_CONTROL,
        "note": "tiles are content-addressed by path; a changed tile changes its path",
    }
    out = ROOT / "data" / "dp2021" / "upload_manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(f"wrote {out.relative_to(ROOT)}")

    if a.dry_run or not have_creds():
        if not a.dry_run:
            print("\nno R2 credentials in the environment:")
            for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
                print(f"  {k}=<unset>")
            print("\nmanifest written; nothing uploaded. Set the four vars and re-run.")
        return

    try:
        import boto3  # noqa
    except ImportError:
        sys.exit("boto3 is required for upload: pip install boto3")

    session = boto3.session.Session()
    s3 = session.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    bucket = os.environ["R2_BUCKET"]

    def put(entry):
        path = ROOT / entry["key"]
        ctype = CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        s3.upload_file(str(path), bucket, entry["key"],
                       ExtraArgs={"ContentType": ctype, "CacheControl": CACHE_CONTROL})
        return entry["key"]

    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    sent = failed = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for res in ex.map(lambda e: (put(e) or True), entries):
            if res:
                sent += 1
            else:
                failed += 1
            if sent and sent % 500 == 0:
                el = time.time() - t0
                print(f"  {sent}/{len(entries)}  {el:.0f}s  eta "
                      f"{el/sent*(len(entries)-sent):.0f}s", flush=True)
    print(f"\nuploaded {sent} files to r2://{bucket} in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
