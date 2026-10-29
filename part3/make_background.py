"""Generate the static background picture (background.png) for the transmitter.

A procedurally drawn landscape (sky, sun, hills, lake), so there is no
copyright question. It is kept mostly mid-to-bright: the black alpha overlay
dims every pixel by the same fraction, so dark areas carry little absolute
signal (HiLight section 3.3).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

HERE = Path(__file__).resolve().parent
W, H = 1600, 1000


def smoothstep(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def make() -> Image.Image:
    y, x = np.mgrid[0:H, 0:W].astype(float)
    u, v = x / W, y / H
    sky_top = np.array([120, 170, 225.0])
    sky_bot = np.array([235, 220, 200.0])
    img = sky_top * (1 - v[..., None] / 0.6) + sky_bot * (v[..., None] / 0.6)
    # sun glow
    d = np.hypot(u - 0.72, (v - 0.28) * H / W)
    img += (np.exp(-(d / 0.18) ** 2) * 40)[..., None] * np.array([1.0, 0.85, 0.6])
    img = np.where((d < 0.045)[..., None], np.array([255, 245, 215.0]), img)
    # far and near hills
    far = 0.55 + 0.05 * np.sin(u * 7.0 + 0.6) + 0.025 * np.sin(u * 19 + 1.3)
    near = 0.66 + 0.06 * np.sin(u * 4.3 + 2.1) + 0.02 * np.sin(u * 23 + 0.2)
    m_far = smoothstep(-0.003, 0.003, v - far)[..., None]
    img = img * (1 - m_far) + np.array([140, 165, 170.0]) * m_far
    m_near = smoothstep(-0.003, 0.003, v - near)[..., None]
    grass = np.array([150, 185, 110.0]) * (1 - 0.25 * (v[..., None] - 0.66))
    img = img * (1 - m_near) + grass * m_near
    # lake reflecting the sky
    lake = (np.hypot((u - 0.35) / 0.28, (v - 0.84) / 0.08) < 1)[..., None]
    water = np.array([150, 190, 225.0]) + 15 * np.sin(v * 220)[..., None]
    img = np.where(lake, water, img)
    # a few simple trees
    for cx, cy, s in [(0.12, 0.7, 1.0), (0.18, 0.72, 0.8), (0.86, 0.69, 1.1), (0.92, 0.71, 0.9)]:
        crown = np.hypot((u - cx) / (0.035 * s), (v - cy + 0.06 * s) / (0.07 * s)) < 1
        trunk = (abs(u - cx) < 0.005 * s) & (v > cy - 0.02) & (v < cy + 0.03 * s)
        img = np.where(crown[..., None], np.array([95, 140, 85.0]), img)
        img = np.where(trunk[..., None], np.array([120, 95, 70.0]), img)
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    return im.filter(ImageFilter.GaussianBlur(1.2))


if __name__ == "__main__":
    out = HERE / "background.png"
    im = make()
    im.save(out, optimize=True)
    g = np.asarray(im.convert("L"), dtype=float)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB), mean gray {g.mean():.1f}, "
          f"5th pct {np.percentile(g, 5):.1f}")
