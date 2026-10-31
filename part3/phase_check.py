"""Do the 60 Hz symbols that fail lose their tone, or does the tone flip phase inside the symbol?

For every sent-1 (60 Hz) payload symbol, the window is split into quarters and the 60 Hz
projection of each quarter is taken. Coherence = |sum of quarters|^2 / (4 * sum |quarter|^2):
1 for a steady tone, near 0 when the phase flips by 180 degrees mid-symbol (one repeated or
skipped display frame inverts a tone that alternates every frame). Strength = mean quarter
power relative to the median over all sent-1 symbols in the recording.

Usage: python phase_check.py RESULTS_DIR...
"""

import json
import sys
from pathlib import Path

import numpy as np

import hilight_plan as hp
import receiver as rxm


def main():
    allc, alls, allok = [], [], []
    for d in map(Path, sys.argv[1:]):
        r = json.loads((d / "results.json").read_text())
        z = np.load(d / "trace.npz")
        plan = hp.build_plan(r["plan_params"])
        refresh = r["refresh_hz"]
        dec = rxm.decode_trace(z["t"], z["v"], plan, refresh, r["camera_fps"])
        x, fs, f1 = dec["x"], dec["fs"], dec["f1"]
        sent = [b for b in plan["blocks"] if b["type"] == "packet"]
        coh, strength, ok = [], [], []
        for b, p in zip(sent, dec["packets"]):
            if p["tau"] is None:
                continue
            for j, s in enumerate(b["bits"][b["payloadStart"]:]):
                if s != 1 or j >= len(p["bits"]):
                    continue
                jj = b["payloadStart"] + j
                a = int(round(p["tau"] + jj * p["Ts"]))
                e = int(round(p["tau"] + (jj + 1) * p["Ts"]))
                q = np.array_split(np.arange(a, e), 4)
                S = np.array([np.sum(x[i] * np.exp(-2j * np.pi * f1 * i / fs)) for i in q])
                coh.append(abs(S.sum()) ** 2 / (4 * np.sum(abs(S) ** 2) + 1e-30))
                strength.append(np.mean(abs(S) ** 2))
                ok.append(p["bits"][j] == 1)
        strength = np.array(strength) / np.median(strength)
        allc += coh
        alls += strength.tolist()
        allok += ok
    c, s, ok = map(np.array, (allc, alls, allok))
    for name, m in (("decoded right", ok), ("decoded wrong", ~ok)):
        print(f"{name}: n={m.sum()}, coherence median {np.median(c[m]):.2f} "
              f"(share < 0.5: {np.mean(c[m] < 0.5) * 100:.0f}%), quarter power median {np.median(s[m]):.2f}")


if __name__ == "__main__":
    main()
