"""Plot the raw camera samples of failed and correct 60 Hz (bit 1) symbols at the slowest rate.

Usage: python show_failed_symbol.py RESULTS_DIR OUT_PNG
"""

import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import hilight_plan as hp
import receiver as rxm


def main():
    d, out = Path(sys.argv[1]), Path(sys.argv[2])
    r = json.loads((d / "results.json").read_text())
    z = np.load(d / "trace.npz")
    t, v = z["t"], z["v"]
    plan = hp.build_plan(r["plan_params"])
    refresh = r["refresh_hz"]
    dec = rxm.decode_trace(t, v, plan, refresh, r["camera_fps"])
    fs, t0 = dec["fs"], dec["t0"]
    picks = {True: [], False: []}
    for b, p in zip([b for b in plan["blocks"] if b["type"] == "packet"], dec["packets"]):
        if p["tau"] is None:
            continue
        for j, s in enumerate(b["bits"][b["payloadStart"]:]):
            if s == 1 and j < len(p["bits"]):
                jj = b["payloadStart"] + j
                ta = t0 + (p["tau"] + jj * p["Ts"]) / fs
                picks[p["bits"][j] == 1].append((b["N"], ta, p["Ts"] / fs))
    fig, axs = plt.subplots(4, 1, figsize=(11, 9))
    rows = [("correct", x) for x in picks[True] if x[0] == 24][:2] + [("failed", x) for x in picks[False]][:2]
    for ax, (lab, (N, ta, Ts)) in zip(axs, rows):
        m = (t >= ta - 0.02) & (t <= ta + Ts + 0.02)
        ax.plot(t[m] - ta, v[m], ".-", lw=0.6, ms=3, color="0.25")
        ax.axvspan(0, Ts, color="tab:orange", alpha=0.08)
        for k in range(int(N) + 1):
            ax.axvline(k / refresh, color="0.85", lw=0.5)
        ax.set_title(f"{d.name}: {lab} bit-1 symbol, N={int(N)} frames, starts {ta:.2f} s")
    axs[-1].set_xlabel("time from symbol start (s); grey lines = display frames at 120 Hz")
    fig.tight_layout()
    fig.savefig(out, dpi=130)


if __name__ == "__main__":
    main()
