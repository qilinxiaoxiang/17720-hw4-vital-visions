"""Is the camera's frame pattern alone enough to cause the 60 Hz bit errors?

For each recording, an ideal screen signal is rebuilt from the transmit plan (every display
frame shown exactly on time, no pixel response, no noise) and sampled at the video's real
frame timestamps, with a 1/240 s exposure. The receiver then decodes this ideal trace.
Errors here come only from the camera sampling pattern; errors that appear only in the real
video come from the screen or the optics.

Usage: python sampling_test.py RESULTS_DIR...
"""

import json
import sys
from pathlib import Path

import numpy as np

import hilight_plan as hp
import receiver as rxm


def ideal_trace(t, plan, refresh, t_plan0, exposure=1 / 240, sub=16):
    lv = hp.levels(plan)
    alpha = hp.alpha_of(plan, lv)
    out = np.empty(t.size)
    offs = (np.arange(sub) + 0.5) / sub * exposure
    for i, ti in enumerate(t):
        k = np.floor((ti - exposure + offs - t_plan0) * refresh).astype(int)
        a = np.where((k >= 0) & (k < alpha.size), alpha[np.clip(k, 0, alpha.size - 1)], plan["params"]["base"])
        out[i] = 100.0 * (1 - a).mean()
    return out


def main():
    for d in map(Path, sys.argv[1:]):
        r = json.loads((d / "results.json").read_text())
        z = np.load(d / "trace.npz")
        t = z["t"]
        plan = hp.build_plan(r["plan_params"])
        refresh = r["refresh_hz"]
        pilot_frame = next(b["startFrame"] for b in plan["blocks"] if b.get("label") == "pilot0")
        t_plan0 = r["pilot"]["t"] - pilot_frame / refresh
        for name, tt in (("real timestamps", t), ("uniform 240 fps", np.arange(t[0], t[-1], 1 / 240))):
            v = ideal_trace(tt, plan, refresh, t_plan0)
            fps = 1 / np.median(np.diff(tt)) if name.startswith("uniform") else r["camera_fps"]
            dec = rxm.decode_trace(tt, v, plan, refresh, fps)
            sc = rxm.score(plan, dec, refresh)
            o = sc["overall"]
            per = ", ".join(f"{s['rate_bps']:g}:{s['bit_errors']}" for s in sc["segments"])
            print(f"{d.name} [{name}]: BER {o['ber']:.4f}  errors per rate {per}")


if __name__ == "__main__":
    main()
