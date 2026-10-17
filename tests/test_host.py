"""Offline tests for `comet host`. Run: python3 tests/test_host.py  (or pytest)."""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402

from comet_tpl import config, host  # noqa: E402


def _setup():
    d = tempfile.mkdtemp(prefix="comet-host-")
    config.CONFIG_DIR, config.CONFIG_FILE = d, os.path.join(d, "config.json")
    host.init(os.path.join(d, "host"))
    src = os.path.join(d, "src")
    os.makedirs(src)
    return d, src


def test_add_keeps_the_image_name_the_ai_writes():
    # fails if the hosted file name drifts from the source stem (the frame builds HOST + 장면 + ".webp")
    # or if an unchanged source is re-encoded on every add
    d, src = _setup()
    Image.new("RGB", (64, 36), "orange").save(os.path.join(src, "초롱_3.png"))
    assert [r[:2] for r in host.add("호박집", [src])] == [("초롱_3", "new")]
    out = os.path.join(d, "host", "public", "호박집", "초롱_3.webp")
    assert open(out, "rb").read(12)[8:12] == b"WEBP"
    assert [r[1] for r in host.add("호박집", [src])] == ["same"]


def test_add_refuses_names_that_break_a_url():
    # fails if '#' (fragment) or a trailing space slips through: the browser would request another path
    _, src = _setup()
    for bad in ("초롱#3.png", "초롱_3 .png"):
        p = os.path.join(src, bad)
        Image.new("RGB", (8, 8)).save(p)
        try:
            host.add("호박집", [p])
        except SystemExit:
            continue
        raise AssertionError(f"accepted {bad!r}")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("✓", name)
