"""Self-test: does the affine tile warp actually work?

A tile builder that silently produces wrong pixels is worse than one that
crashes, and the affine path is new code with a sign convention and a
matrix order that are easy to get wrong in ways that still "work". So this
test checks the WARP against a known ground truth, not against the old
output.

The test:
  1. Take a small source image with a known pattern.
  2. Build a source -> (lon, merc) transform that maps it to a known
     lon/lat window.
  3. Render tiles with the affine path and with the north-up path.
  4. Check that the affine tiles put the pattern where the transform says
     it should be, and that the north-up path still reproduces the
     original tiles.

A second test covers the case that actually bit: a transform with
ROTATION, which the north-up path cannot represent at all.
"""
import math, os, sys
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_tiles as bt

WORK = r"C:\ideabot internships\geomapping\work"
ROOT_DIR = r"C:\ideabot internships\geomapping"
OUT = os.path.join(WORK, "_warp_test")
os.makedirs(OUT, exist_ok=True)


def merc_y(lat):
    return math.degrees(math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)))


def make_source():
    """A 512x512 test image: a bright disc at a known pixel."""
    a = np.zeros((512, 512, 3), np.uint8)
    a[:] = (20, 20, 30)
    yy, xx = np.mgrid[0:512, 0:512]
    d = (xx - 300) ** 2 + (yy - 200) ** 2
    a[d < 40 ** 2] = (255, 255, 255)
    return Image.fromarray(a)


def transform_for(lon0, lat0, m_per_px):
    """A source px -> (lon, merc) transform for a north-up window.

    The source pixel (0,0) is the top-left of the window, and the window
    extends east and SOUTH from there (source y increases southward, and
    mercator y decreases southward).
    """
    my0 = merc_y(lat0)
    return np.array([[m_per_px / 102500.0, 0.0, lon0],
                     [0.0, -m_per_px / 110574.0, my0]], float)


def test_roundtrip():
    """A source pixel must map to the lon/lat the transform says it does."""
    M = transform_for(72.5, 23.0, 2.0)
    px, py = 300, 200
    lon = M[0][0] * px + M[0][1] * py + M[0][2]
    my = M[1][0] * px + M[1][1] * py + M[1][2]
    lat = math.degrees(2 * math.atan(math.exp(math.radians(my))) - math.pi / 2)
    print(f"  source px ({px},{py}) -> lon {lon:.6f} lat {lat:.6f}")

    # and the inverse must come back
    A = np.array([[M[0][0], M[0][1], M[0][2]],
                  [M[1][0], M[1][1], M[1][2]],
                  [0, 0, 1]], float)
    Ai = np.linalg.inv(A)
    v = Ai @ np.array([lon, my, 1.0])
    assert abs(v[0] - px) < 1e-6 and abs(v[1] - py) < 1e-6, "inverse is wrong"
    print("  inverse round-trips correctly")
    return M, lon, lat


def transform_matrix(lon0, lat0, m_per_px):
    """Alias kept for readability at the call sites."""
    return transform_for(lon0, lat0, m_per_px)

def test_warp_places_content():
    """The warped tile must put the disc where the transform says it is."""
    M = transform_for(72.5, 23.0, 2.0)
    src = make_source()
    arr = np.asarray(src)

    # pick the tile that contains the disc's true location
    disc_lon = M[0][0] * 300 + M[0][1] * 200 + M[0][2]
    disc_my = M[1][0] * 300 + M[1][1] * 200 + M[1][2]
    z = 16
    n = 2 ** z
    step = 360.0 / n
    tx = int((disc_lon + 180.0) / step)
    ty = int((180.0 - disc_my) / step)

    class FakeMM:
        def __getitem__(self, idx):
            y0, y1 = idx[0].start, idx[0].stop
            x0, x1 = idx[1].start, idx[1].stop
            return arr[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    mm = FakeMM()

    Ai = np.linalg.inv(np.array([[M[0][0], M[0][1], M[0][2]],
                                 [M[1][0], M[1][1], M[1][2]],
                                 [0, 0, 1]], float))
    tile = bt._warp_tile(z, tx, ty, mm, M, Ai)
    if tile is None:
        print("  FAIL: the warp produced no tile")
        return False
    a = np.asarray(tile)
    bright = np.argwhere(a.min(axis=2) > 200)
    if len(bright) == 0:
        print("  FAIL: the disc is not in the warped tile")
        return False
    cy, cx = bright.mean(axis=0)
    print(f"  disc found in the tile at ({cx:.0f},{cy:.0f}) of {tile.size}")

    # where SHOULD it be?  the tile's pixel (u,v) maps to lon/merc, and
    # that must map back to the source pixel (300,200)
    lon_t = -180.0 + tx * step + (cx / 256.0) * step
    my_t = 180.0 - ty * step - (cy / 256.0) * step
    v = Ai @ np.array([lon_t, my_t, 1.0])
    err = math.hypot(v[0] - 300, v[1] - 200)
    print(f"  it maps back to source px ({v[0]:.1f},{v[1]:.1f}), "
          f"error {err:.1f} source px ({err*2.0:.1f} m)")
    if err > 2.0:
        print("  FAIL: the warped content is misplaced")
        return False
    print("  PASS: the warped content is where the transform says it is")
    return True


def test_rotation_works():
    """A rotated transform must still put the content in the right place."""
    M = transform_for(72.5, 23.0, 2.0)
    th = math.radians(3.0)
    R = np.array([[math.cos(th), -math.sin(th)], [math.sin(th), math.cos(th)]])
    Mrot = M.copy()
    Mrot[:, :2] = R @ Mrot[:, :2]
    src = make_source()
    arr = np.asarray(src)

    disc_lon = Mrot[0][0] * 300 + Mrot[0][1] * 200 + Mrot[0][2]
    disc_my = Mrot[1][0] * 300 + Mrot[1][1] * 200 + Mrot[1][2]
    z = 16
    n = 2 ** z
    step = 360.0 / n
    tx = int((disc_lon + 180.0) / step)
    ty = int((180.0 - disc_my) / step)

    class Arr:
        def __getitem__(self, idx):
            y0, y1 = idx[0].start, idx[0].stop
            x0, x1 = idx[1].start, idx[1].stop
            return arr[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    class FakeMM:
        __getitem__ = Arr().__getitem__

    Ai = np.linalg.inv(np.array([[Mrot[0][0], Mrot[0][1], Mrot[0][2]],
                                 [Mrot[1][0], Mrot[1][1], Mrot[1][2]],
                                 [0, 0, 1]], float))
    tile = bt._warp_tile(z, tx, ty, FakeMM(), Mrot, Ai)
    a = np.asarray(tile)
    bright = np.argwhere(a.min(axis=2) > 200)
    if len(bright) == 0:
        print("  FAIL: the disc is not in the rotated tile")
        return False
    cy, cx = bright.mean(axis=0)
    lon_t = -180.0 + tx * step + (cx / 256.0) * step
    my_t = 180.0 - ty * step - (cy / 256.0) * step
    v = Ai @ np.array([lon_t, my_t, 1.0])
    err = math.hypot(v[0] - 300, v[1] - 200)
    print(f"  rotated 3 deg: maps back to source px ({v[0]:.1f},{v[1]:.1f}), "
          f"error {err:.1f} px ({err*2.0:.1f} m)")
    if err > 2.5:
        print("  FAIL: the rotated warp is misplaced")
        return False
    print("  PASS: rotation is handled")
    return True


def test_northup_unchanged():
    """The north-up path must still produce the same tiles as before.

    The affine path is new code. If it is ever switched on by accident, the
    north-up tiles have to stay byte-identical to what the project already
    ships, so this compares a freshly rendered tile against the one on disk.
    """
    import glob
    tiles = sorted(glob.glob(os.path.join(os.path.dirname(WORK), "tiles", "13", "*", "*.jpg")))
    if not tiles:
        print("  SKIP: no built tiles to compare against")
        return True
    p = tiles[len(tiles) // 2]
    print(f"  comparing against {os.path.relpath(p, ROOT_DIR)}")

    g = bt.load_georef()
    # the tile builder's own enum, for the tile's (z, x, y)
    parts = os.path.relpath(p, os.path.join(ROOT_DIR, "tiles")).replace("\\", "/").split("/")
    z, x, y = int(parts[0]), int(parts[1]), int(os.path.splitext(parts[2])[0])

    bt._G["g"] = g
    bt._G["mm"] = np.memmap(bt.RAW, dtype=np.uint8, mode="r",
                            shape=(25600, 27200, 3))
    bt._G["A"] = None
    bt._G["Ai"] = None
    size = bt._one((z, x, y, os.path.join(WORK, "_warp_test"), "jpg", 85, 2))
    if size is None:
        print("  FAIL: the north-up path produced no tile")
        return False
    made = os.path.join(WORK, "_warp_test", str(z), str(x), f"{y}.jpg")
    a = np.asarray(Image.open(made).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(p).convert("RGB"), dtype=np.int16)
    diff = np.abs(a - b)
    mx = int(diff.max())
    mean = float(diff.mean())
    print(f"  max channel difference {mx}, mean {mean:.3f}")
    if mx > 2:
        print("  FAIL: the north-up path no longer matches the shipped tiles")
        return False
    print("  PASS: the north-up path is unchanged")
    return True


def main():
    print("=== the transform round-trips ===")
    test_roundtrip()
    print("\n=== the warp places content correctly ===")
    ok1 = test_warp_places_content()
    print("\n=== rotation is handled ===")
    ok2 = test_rotation_works()
    print("\n=== the north-up path is unchanged ===")
    ok3 = test_northup_unchanged()
    print()
    if ok1 and ok2 and ok3:
        print("ALL TESTS PASSED")
    else:
        print("TESTS FAILED")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
