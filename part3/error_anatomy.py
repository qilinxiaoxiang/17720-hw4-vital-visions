"""Where do the bit errors in a decoded recording come from?

Re-runs the receiver on the saved ROI trace and, for every payload bit, records the sent bit,
the previous sent bit, the position in the packet and whether it was decoded correctly.
Prints error rates split by sent bit (0 = 30 Hz, 1 = 60 Hz), by whether the symbol follows
a tone change, and by first versus second half of the packet (timing drift).

Usage: python error_anatomy.py RESULTS_DIR DATA_DIR
"""

import json
import sys
from pathlib import Path

import numpy as np

import hilight_plan as hp
import receiver as rxm


def main():
    res_dir, data_dir = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for d in sorted(res_dir.glob("p3_da*")):
        r = json.loads((d / "results.json").read_text())
        z = np.load(d / "trace.npz")
        plan = hp.build_plan(r["plan_params"])
        refresh = r["refresh_hz"]
        dec = rxm.decode_trace(z["t"], z["v"], plan, refresh, r["camera_fps"])
        sent = [b for b in plan["blocks"] if b["type"] == "packet"]
        for b, p in zip(sent, dec["packets"]):
            truth = b["bits"][b["payloadStart"]:]
            got = p["bits"]
            for j, s in enumerate(truth):
                prev = b["bits"][b["payloadStart"] + j - 1]
                ok = j < len(got) and got[j] == s
                rows.append((d.name, b["N"], s, int(prev != s), int(j >= len(truth) // 2), int(ok),
                             p["soft"][j] if j < len(p["soft"]) else np.nan))
    a = np.array([x[1:] for x in rows], float)
    N, s, chg, late, ok, soft = a.T
    err = 1 - ok

    def rate(m):
        return f"{err[m].mean() * 100:5.1f}% of {int(m.sum())}"

    print("all bits          ", rate(np.ones_like(err, bool)))
    print("sent 0 (30 Hz)    ", rate(s == 0))
    print("sent 1 (60 Hz)    ", rate(s == 1))
    print("after tone change ", rate(chg == 1))
    print("same tone as prev ", rate(chg == 0))
    print("first half packet ", rate(late == 0))
    print("second half packet", rate(late == 1))
    for n in sorted(set(N)):
        m = N == n
        print(f"N={int(n):2d}: sent0 {rate(m & (s == 0))}, sent1 {rate(m & (s == 1))}, "
              f"after change {rate(m & (chg == 1))}, no change {rate(m & (chg == 0))}")


if __name__ == "__main__":
    main()
