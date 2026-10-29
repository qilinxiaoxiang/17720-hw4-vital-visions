"""Aggregate decoded recordings into the report figures.

Usage (from Code/part3):
  python summarize.py --results-root ../../Analysis/Part3/results --out ../../Analysis/Part3/figures
  python summarize.py --results-root ../../Checks/Part3-Synthetic/results --out ../../Checks/Part3-Synthetic/figures --synthetic

Reads <results-root>/*/results.json written by decode_video.py. Real and
synthetic recordings are never mixed: by default only real files (no synth_
prefix) are used; --synthetic selects only synthetic ones and labels every
figure as synthetic.

Outputs: ber_vs_dalpha_by_distance.png, datarate_vs_dalpha_by_distance.png,
ber_vs_rate_by_condition.png, max_reliable_rate_vs_dalpha.png,
summary.csv, summary.json (and eye_check.csv if --eyecheck is given).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np

import plot_style as ps

RELIABLE_BER = 0.01


def load(root: Path, synthetic: bool) -> list[dict]:
    rows = []
    for f in sorted(root.glob("*/results.json")):
        r = json.loads(f.read_text())
        if bool(r.get("synthetic")) != synthetic or not r.get("segments"):
            continue
        if r.get("dalpha") is None or r.get("distance_cm") is None:
            print(f"skip {f.parent.name}: name does not encode delta alpha / distance")
            continue
        rows.append(r)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-root", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--eyecheck", type=Path, default=None, help="eye-check JSON downloaded from the transmitter")
    args = ap.parse_args(argv)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    recs = load(args.results_root, args.synthetic)
    if not recs:
        raise SystemExit(f"no {'synthetic' if args.synthetic else 'real'} results under {args.results_root}")
    args.out.mkdir(parents=True, exist_ok=True)
    tag = "SYNTHETIC (code validation, not measurements): " if args.synthetic else ""

    flat = []
    for r in recs:
        for s in r["segments"]:
            flat.append({"recording": r["recording"], "dalpha": r["dalpha"], "distance_cm": r["distance_cm"],
                         "rate_bps": s["rate_bps"], "ber": s["ber"], "bit_errors": s["bit_errors"],
                         "payload_bits": s["payload_bits"], "correct_bits": s["correct_bits"],
                         "data_rate_bps": s["data_rate_bps"],
                         "data_rate_incl_overhead_bps": s["data_rate_incl_overhead_bps"],
                         "packets_synced": s["synced"], "packets": s["packets"], "tone_snr_db": s["tone_snr_db"]})
    das = sorted({x["dalpha"] for x in flat})
    dists = sorted({x["distance_cm"] for x in flat})
    rates = sorted({x["rate_bps"] for x in flat})
    get = {(x["dalpha"], x["distance_cm"], x["rate_bps"]): x for x in flat}
    dcol = {d: ps.SERIES[i] for i, d in enumerate(dists)}
    dmark = {d: ps.MARKERS[i] for i, d in enumerate(dists)}
    ps.apply()

    def small_multiples(key, ylabel, fname, ylim=None):
        fig, axs = plt.subplots(1, len(rates), figsize=(2.6 * len(rates) + 1, 3.4), sharey=True, squeeze=False)
        for ax, rate in zip(axs[0], rates):
            for d in dists:
                xs = [da for da in das if (da, d, rate) in get]
                ax.plot(xs, [get[(da, d, rate)][key] for da in xs], marker=dmark[d], color=dcol[d],
                        label=f"{d} cm")
            ax.set_title(f"{rate:g} bps")
            ax.set_xticks(das)
            ax.set_xlim(min(das) - 0.08, max(das) + 0.08)
            ax.set_xlabel("delta alpha")
            if key == "data_rate_bps":
                ax.axhline(rate, color=ps.GRID, lw=1.5, ls="--")
            if ylim:
                ax.set_ylim(*ylim)
        axs[0, 0].set_ylabel(ylabel)
        axs[0, -1].legend(title="distance", frameon=False, loc="best")
        fig.suptitle(tag + f"{ylabel} vs delta alpha, one panel per symbol rate", fontsize=11)
        fig.tight_layout()
        fig.savefig(args.out / fname, dpi=150)
        plt.close(fig)

    small_multiples("ber", "bit error rate", "ber_vs_dalpha_by_distance.png", (-0.02, 0.55))
    small_multiples("data_rate_bps", "data rate (correct bits/s)", "datarate_vs_dalpha_by_distance.png")

    fig, axs = plt.subplots(1, len(das), figsize=(5.2 * len(das), 3.6), sharey=True, squeeze=False)
    for ax, da in zip(axs[0], das):
        for d in dists:
            xs = [r for r in rates if (da, d, r) in get]
            ax.plot(xs, [get[(da, d, r)]["ber"] for r in xs], marker=dmark[d], color=dcol[d], label=f"{d} cm")
        ax.set_title(f"delta alpha {da:g}")
        ax.set_xlabel("symbol rate (bps)")
        ax.set_xticks(rates)
        ax.set_ylim(-0.02, 0.55)
    axs[0, 0].set_ylabel("bit error rate")
    axs[0, -1].legend(title="distance", frameon=False)
    fig.suptitle(tag + "BER vs symbol rate", fontsize=11)
    fig.tight_layout()
    fig.savefig(args.out / "ber_vs_rate_by_condition.png", dpi=150)
    plt.close(fig)

    best = []
    for da in das:
        for d in dists:
            ok = [get[(da, d, r)] for r in rates if (da, d, r) in get and get[(da, d, r)]["ber"] <= RELIABLE_BER]
            b = max(ok, key=lambda x: x["rate_bps"]) if ok else None
            best.append({"dalpha": da, "distance_cm": d,
                         "max_reliable_rate_bps": b["rate_bps"] if b else 0.0,
                         "data_rate_at_that_rate_bps": b["data_rate_bps"] if b else 0.0,
                         "ber_at_that_rate": b["ber"] if b else None})
    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for d in dists:
        pts = [x for x in best if x["distance_cm"] == d]
        ax.plot([x["dalpha"] for x in pts], [x["data_rate_at_that_rate_bps"] for x in pts], marker=dmark[d],
                color=dcol[d], label=f"{d} cm")
    ax.set_xticks(das)
    ax.set_xlim(min(das) - 0.08, max(das) + 0.08)
    ax.set_xlabel("delta alpha")
    ax.set_ylabel("data rate (correct bits/s)")
    ax.set_title(tag + f"highest rate with BER <= {RELIABLE_BER:g}", fontsize=10)
    ax.legend(title="distance", frameon=False)
    fig.tight_layout()
    fig.savefig(args.out / "max_reliable_rate_vs_dalpha.png", dpi=150)
    plt.close(fig)

    with open(args.out / "summary.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(flat[0].keys()))
        w.writeheader()
        for x in sorted(flat, key=lambda x: (x["dalpha"], x["distance_cm"], x["rate_bps"])):
            w.writerow(x)
    summary = {"synthetic": args.synthetic, "recordings": [r["recording"] for r in recs],
               "reliable_ber_threshold": RELIABLE_BER, "rows": flat, "max_reliable": best,
               "warnings": {r["recording"]: r["warnings"] for r in recs if r.get("warnings")}}
    missing = [(da, d) for da in (0.1, 0.5) for d in (20, 40, 80, 120)
               if not any(x["dalpha"] == da and x["distance_cm"] == d for x in flat)]
    summary["missing_conditions"] = missing

    if args.eyecheck:
        eye = json.loads(args.eyecheck.read_text())
        res = sorted(eye["results"], key=lambda r: (r["dalpha"], r.get("rateBps") or 0, r.get("toneHz") or 0))
        with open(args.out / "eye_check.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["dalpha", "stimulus", "rate_bps", "tone_hz", "response", "replays"])
            for r in res:
                w.writerow([r["dalpha"], r["stimulus"], r.get("rateBps", ""), r.get("toneHz", ""), r["response"],
                            r.get("replays", 0)])
        summary["eye_check"] = res
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(f"{len(recs)} recordings -> {args.out}")
    for x in best:
        print(f"  delta alpha {x['dalpha']:g}, {x['distance_cm']} cm: highest rate with BER <= {RELIABLE_BER:g} = "
              f"{x['max_reliable_rate_bps']:g} bps")
    if missing:
        print("  missing conditions:", ", ".join(f"da {a:g} / {d} cm" for a, d in missing))


if __name__ == "__main__":
    sys.exit(main())
