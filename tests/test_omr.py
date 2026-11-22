"""OMR round trip: draw jianpu with render.py, read it back with omr.py.

Run:  python tests/test_omr.py   (or pytest)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2  # noqa: E402
import numpy as np  # noqa: E402

import jianpu_core as core  # noqa: E402
import omr  # noqa: E402
import render  # noqa: E402


def notes(text):
    return [(n.degree, n.acc, n.octave, n.dur) for m in core.parse(text).measures for n in m.notes()]


def read_back(text, scale=1.0, degrade=False):
    img = np.array(render.Renderer(44).render(text))[:, :, ::-1].copy()
    if degrade:
        img = cv2.GaussianBlur(img, (3, 3), 0)
        img = np.clip(img + np.random.default_rng(1).normal(0, 12, img.shape), 0, 255).astype(np.uint8)
        M = cv2.getRotationMatrix2D((img.shape[1] / 2, img.shape[0] / 2), 1.2, 1)
        img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), borderValue=(255, 255, 255))
    if scale != 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return omr.recognize(img, key=core.parse(text).key).text


ODE = (ROOT / "examples" / "ode_to_joy.txt").read_text(encoding="utf-8")
FEATURES = """key: Bb
5,_ 5,_ |: <1> 1 3{ 2_ 3_ 4_ } 5__ 6__ 5__ 4__ (3_ 2_) | 6,~ 6, 0 #4_. 5__ |
b7, - - 1' | 1'' 7' 6 5,, | 1 2 3 4 | 0 - - - ||
"""


def test_ode_exact():
    assert notes(read_back(ODE)) == notes(ODE)


def test_ode_half_size_and_degraded():
    assert notes(read_back(ODE, scale=0.5)) == notes(ODE)
    assert notes(read_back(ODE, degrade=True)) == notes(ODE)


def test_features():
    got = read_back(FEATURES)
    assert notes(got) == notes(FEATURES)
    assert "3{" in got and "~" in got and "(" in got and "|:" in got and "#4" in got


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
