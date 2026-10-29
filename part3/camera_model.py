"""Physical model of screen -> camera for synthetic validation (NOT measurements).

Display: per-frame alpha from the plan, black overlay so linear luminance is
(1 - alpha)^gamma times the picture; LCD pixel response modeled as a
first-order lag; optional dropped transmitter frames (previous level held);
display clock offset in ppm against the camera clock.

Camera: frame capture times (fps, jitter, dropped slots), exposure-time
integration, optional rolling-shutter readout across rows, linear gain with
slow auto-exposure drift, ambient light (constant + optional mains flicker),
sensor gamma and 8-bit clipping, per-pixel and common-mode noise.

Distance: the screen's share of the camera frame follows the iPhone main
camera field of view, so the screen ROI shrinks as 1/d^2 (fewer pixels to
average), and veiling glare (ambient term) grows with distance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

import numpy as np

import hilight_plan as hp


@dataclass
class DisplayModel:
    refresh_hz: float = 120.0
    gamma: float = 2.2
    tau_s: float = 0.003          # LCD first-order response time constant
    clock_ppm: float = 40.0       # display clock error vs camera clock
    drop_prob: float = 0.0005     # transmitter frames that repeat the previous level
    oversample: int = 16          # sub-steps per display frame for the integrals


@dataclass
class CameraModel:
    fps: float = 240.0
    exposure_s: float = 1 / 500
    readout_s: float = 0.005      # rolling-shutter readout of the full frame height
    jitter_s: float = 0.0002      # capture time jitter amplitude
    drop_every: int = 997         # one capture slot skipped every N frames (0 = never)
    gain: float = 0.8             # linear exposure gain: screen white -> 80% full scale
    ae_drift: float = 0.004       # relative amplitude of slow gain wander
    ae_drift_hz: float = 0.13
    ambient: float = 0.01         # additive ambient / glare (fraction of full scale, linear)
    flicker_amp: float = 0.0      # mains flicker of the ambient term (relative)
    flicker_hz: float = 120.0
    out_gamma: float = 1 / 2.2
    noise_px: float = 2.0         # DN, per pixel per frame
    noise_common: float = 0.05    # DN, per frame, shared by all pixels of the ROI


@dataclass
class Geometry:
    screen_w_cm: float = 34.5     # 16-inch MacBook Pro active area
    screen_h_cm: float = 22.3
    hfov_deg: float = 73.7        # iPhone main camera (24 mm equiv.)
    frame_w_px: int = 1920
    frame_h_px: int = 1080

    def screen_fraction(self, distance_cm: float) -> tuple[float, float]:
        fw = 2 * distance_cm * np.tan(np.radians(self.hfov_deg / 2))
        fh = fw * self.frame_h_px / self.frame_w_px
        return self.screen_w_cm / fw, self.screen_h_cm / fh

    def roi_pixels(self, distance_cm: float) -> float:
        wf, hf = self.screen_fraction(distance_cm)
        return min(wf, 1) * self.frame_w_px * min(hf, 1) * self.frame_h_px


def glare_for_distance(distance_cm: float) -> float:
    """Ambient / veiling glare term (linear fraction of full scale) vs distance."""
    return 0.01 + 0.006 * (distance_cm / 20.0)


@dataclass
class Transmission:
    plan: dict
    t_start: float = 3.0          # camera time when display frame 0 is shown
    seed: int = 0

    def displayed_levels(self, display: DisplayModel) -> np.ndarray:
        lv = hp.levels(self.plan)
        rng = np.random.default_rng(self.seed + 17)
        drops = np.flatnonzero(rng.random(lv.size) < display.drop_prob)
        drops = drops[drops > 0]
        lv = lv.copy()
        lv[drops] = lv[drops - 1]
        return lv


class DisplayIntegrator:
    """Cumulative integral of the display's relative luminance L(t) in camera time.

    L = (1 - alpha)^gamma after the LCD first-order response; before and after
    the transmission the overlay sits at the idle alpha.
    """

    def __init__(self, tx: Transmission, display: DisplayModel, pad_s: float = 5.0):
        p = tx.plan["params"]
        lv = tx.displayed_levels(display)
        alpha = p["base"] + p["dalpha"] * lv
        idle = (1 - (p["base"] + p["dalpha"] * 0.5)) ** display.gamma
        target = (1 - alpha) ** display.gamma
        frame_dt = (1.0 / display.refresh_hz) * (1 - display.clock_ppm * 1e-6)
        os_ = display.oversample
        dt = frame_dt / os_
        npad = int(np.ceil(pad_s / dt))
        tgt = np.concatenate([np.full(npad, idle), np.repeat(target, os_), np.full(npad, idle)])
        if display.tau_s > 0:
            a = np.exp(-dt / display.tau_s)
            from scipy.signal import lfilter
            y = lfilter([1 - a], [1, -a], tgt, zi=[a * idle])[0]
        else:
            y = tgt
        self.t0 = tx.t_start - npad * dt
        self.dt = dt
        self.cum = np.concatenate([[0.0], np.cumsum(y) * dt])
        self.idle = idle

    def mean(self, a, b):
        """Average luminance over [a, b] (arrays, camera seconds)."""
        a = np.asarray(a, float)
        b = np.asarray(b, float)

        def C(t):
            x = (t - self.t0) / self.dt
            n = self.cum.size - 1
            below = x <= 0
            above = x >= n
            xc = np.clip(x, 0, n)
            i = np.minimum(np.floor(xc).astype(int), n - 1)
            frac = xc - i
            val = self.cum[i] + frac * (self.cum[i + 1] - self.cum[i])
            val = np.where(below, x * self.dt * self.idle, val)
            val = np.where(above, self.cum[-1] + (x - n) * self.dt * self.idle, val)
            return val

        return (C(b) - C(a)) / (b - a)


def capture_times(camera: CameraModel, duration_s: float) -> np.ndarray:
    """Capture start times; formula mirrored in the ffmpeg setpts expression."""
    n_slots = int(duration_s * camera.fps)
    N = np.arange(n_slots)
    if camera.drop_every:
        N = N[: int(n_slots * camera.drop_every / (camera.drop_every + 1))]
        slots = N + np.floor(N / camera.drop_every)
    else:
        slots = N.astype(float)
    return slots / camera.fps + camera.jitter_s * np.sin(2.399963 * N)


def setpts_expr(camera: CameraModel) -> str:
    """ffmpeg setpts expression reproducing capture_times() for frame index N."""
    slots = f"(N+floor(N/{camera.drop_every}))" if camera.drop_every else "N"
    return f"({slots}/{camera.fps}+{camera.jitter_s}*sin(2.399963*N))/TB"


def gain_drift(camera: CameraModel, t: np.ndarray) -> np.ndarray:
    return camera.gain * (1 + camera.ae_drift * np.sin(2 * np.pi * camera.ae_drift_hz * t + 0.7))


def ambient_term(camera: CameraModel, t: np.ndarray) -> np.ndarray:
    return camera.ambient * (1 + camera.flicker_amp * np.sin(2 * np.pi * camera.flicker_hz * t))


def row_luminance(integ: DisplayIntegrator, camera: CameraModel, t: np.ndarray,
                  row_frac: np.ndarray) -> np.ndarray:
    """Exposure-averaged display luminance for each (frame, row): shape (len(t), len(row_frac))."""
    a = t[:, None] + row_frac[None, :] * camera.readout_s
    return integ.mean(a, a + camera.exposure_s)


def to_dn(lin: np.ndarray, camera: CameraModel) -> np.ndarray:
    return 255.0 * np.clip(lin, 0, 1) ** camera.out_gamma


def picture_linear_samples(n: int = 64) -> np.ndarray:
    """Quantiles of the background picture's linear brightness (for ROI-mean sims)."""
    from PIL import Image
    from pathlib import Path
    g = np.asarray(Image.open(Path(__file__).resolve().parent / "background.png").convert("L"), float)
    q = np.quantile(g, (np.arange(n) + 0.5) / n)
    return (q / 255.0) ** 2.2


def simulate_roi_trace(tx: Transmission, display: DisplayModel, camera: CameraModel,
                       roi_pixels: float, roi_height_frac: float = 0.5,
                       duration_s: float | None = None, rng=None) -> tuple[np.ndarray, np.ndarray]:
    """Signal-level simulation: mean ROI value (DN) per captured frame."""
    rng = rng or np.random.default_rng(tx.seed)
    if duration_s is None:
        duration_s = tx.t_start + tx.plan["totalFrames"] / display.refresh_hz + 2.0
    integ = DisplayIntegrator(tx, display)
    t = capture_times(camera, duration_s)
    bands = np.linspace(0, roi_height_frac, 8)
    L = row_luminance(integ, camera, t, bands)                      # (n, 8)
    B = picture_linear_samples()                                    # (64,)
    g = gain_drift(camera, t)[:, None, None]
    amb = ambient_term(camera, t)[:, None, None]
    lin = g * (B[None, None, :] * L[:, :, None] + amb)
    dn = to_dn(lin, camera).mean(axis=(1, 2))
    noise = rng.normal(0, camera.noise_px / np.sqrt(max(roi_pixels, 1)), t.size)
    noise += rng.normal(0, camera.noise_common, t.size)
    return t, dn + noise


def describe(*objs) -> dict:
    out = {}
    for o in objs:
        out[type(o).__name__] = asdict(o)
    return out
