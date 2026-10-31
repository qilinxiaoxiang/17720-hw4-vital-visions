"""Did the camera's exposure or white balance drift during each recording?

For each recording: the screen-region brightness averaged per second (drift and largest
one-second step), the share of region cells near clipping, and the bit error rate split by
sent bit. Auto-exposure or white-balance changes show as steps or trends in the per-second
level; the receiver divides by a 0.5 s moving mean and high-passes at 12 Hz, so only changes
faster than about a tenth of a second can reach the 30/60 Hz detector.

Usage: python exposure_check.py RESULTS_DIR...
"""

import json
import sys
from pathlib import Path

import numpy as np

import hilight_plan as hp
import receiver as rxm


def main():
    for d in map(Path, sys.argv[1:]):
        r = json.loads((d / "results.json").read_text())
        z = np.load(d / "trace.npz")
        t, v = z["t"], z["v"]
        p0 = r["pilot"]["t"]
        m = (t > p0) & (t < p0 + 88)
        sec = np.floor(t[m] - p0).astype(int)
        per_s = np.array([v[m][sec == k].mean() for k in np.unique(sec)])
        steps = np.abs(np.diff(per_s))
        mean_dn = z["mean_dn"][z["mask"]]
        sat = float((mean_dn > 240).mean())
        plan = hp.build_plan(r["plan_params"])
        dec = rxm.decode_trace(t, v, plan, r["refresh_hz"], r["camera_fps"])
        e = {0: [0, 0], 1: [0, 0]}
        for b, p in zip([b for b in plan["blocks"] if b["type"] == "packet"], dec["packets"]):
            for j, s in enumerate(b["bits"][b["payloadStart"]:]):
                e[s][1] += 1
                e[s][0] += int(not (j < len(p["bits"]) and p["bits"][j] == s))
        print(f"{d.name}: level {per_s.min():.1f}-{per_s.max():.1f} DN (drift {per_s.max() - per_s.min():.1f}), "
              f"largest 1 s step {steps.max():.1f} DN, cells >240 DN {sat * 100:.0f}%, "
              f"BER sent0 {e[0][0] / e[0][1] * 100:.1f}%, sent1 {e[1][0] / e[1][1] * 100:.1f}%")


if __name__ == "__main__":
    main()
