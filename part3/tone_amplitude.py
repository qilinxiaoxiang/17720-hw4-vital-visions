"""Measure the 30 Hz and 60 Hz tone amplitudes in each recording's pilot.

The pilot is 1 s of pure f0 (30 Hz) followed by 1 s of pure f1 (60 Hz). Each second is fit
by least squares on the real frame timestamps (no resampling), so missing frames do not bias
the amplitude. The ratio A(60 Hz) / A(30 Hz) shows how much the 60 Hz tone loses between the
display and the recorded video.

Usage: python tone_amplitude.py RESULTS_DIR...
"""

import json
import sys
from pathlib import Path

import numpy as np


def fit_amp(t, v, f):
    X = np.column_stack([np.ones_like(t), t - t.mean(), np.cos(2 * np.pi * f * t), np.sin(2 * np.pi * f * t)])
    coef, *_ = np.linalg.lstsq(X, v, rcond=None)
    return float(np.hypot(coef[2], coef[3]) / coef[0])


def main():
    for d in map(Path, sys.argv[1:]):
        r = json.loads((d / "results.json").read_text())
        z = np.load(d / "trace.npz")
        t, v = z["t"], z["v"]
        p0, f0, f1 = r["pilot"]["t"], r["plan_summary"]["f0"], r["plan_summary"]["f1"]
        m0 = (t > p0 + 0.1) & (t < p0 + 0.9)
        m1 = (t > p0 + 1.1) & (t < p0 + 1.9)
        a0, a1 = fit_amp(t[m0], v[m0], f0), fit_amp(t[m1], v[m1], f1)
        # Same 60 Hz fit using only frames whose neighbours are both 1/240 s away,
        # to check whether the gaps themselves change the estimate.
        dt = np.diff(t)
        reg = np.zeros(t.size, bool)
        reg[1:-1] = (np.abs(dt[:-1] - 1 / 240) < 1e-3) & (np.abs(dt[1:] - 1 / 240) < 1e-3)
        a1r = fit_amp(t[m1 & reg], v[m1 & reg], f1) if (m1 & reg).sum() > 20 else float("nan")
        print(f"{d.name}: A30 {a0 * 100:.2f}%  A60 {a1 * 100:.2f}%  ratio {a1 / a0:.2f}  "
              f"(A60 from regular frames only {a1r * 100:.2f}%)")


if __name__ == "__main__":
    main()
