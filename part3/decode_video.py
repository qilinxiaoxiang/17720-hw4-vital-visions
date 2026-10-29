"""Decode alpha-channel BFSK recordings (iPhone .mov) and score BER / data rate.

Usage (from Code/part3):
  python decode_video.py ../../Data/part3/p3_da010_d020.mov --out-root ../../Analysis/Part3/results

For each video: per-frame timestamps + screen-ROI mean luminance (video_io.py),
streaming BFSK receiver (receiver.py), ground truth regenerated from the
transmitter parameters (hilight_plan.py) or read from the transmitter log
(<stem>_txlog.json next to the video, or --txlog). Writes
<out-root>/<stem>/results.json, trace.npz and figures.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

import hilight_plan as hp
import receiver as rxm
import video_io as vio

PLAN_KEYS = ["mode", "profile", "seed", "packetsPerRate", "packets", "payloadBits", "N", "sweepN",
             "p0", "p1", "textChars", "leadFrames", "pilotFrames", "gapFrames"]


def load_txlog(path: Path) -> dict:
    log = json.loads(path.read_text())
    k = np.asarray(log["frames"]["k"], float)
    ts = np.asarray(log["frames"]["tsMs"], float)
    refresh = None
    if k.size > 100:
        refresh = 1000.0 / np.polyfit(k, ts, 1)[0]
    return {"params": log["plan"]["params"], "refresh_fit_hz": refresh,
            "measured_hz": log["refresh"]["measuredHz"], "nominal_hz": log["refresh"]["nominalHz"],
            "dropped_frames": log.get("droppedFrames"), "total_frames": log["plan"]["totalFrames"],
            "aborted": log.get("aborted"), "started_at": log.get("startedAt"), "path": str(path)}


def minutes_apart(a: str | None, b: str | None) -> float | None:
    """Minutes between two ISO timestamps (video creation vs transmitter start)."""
    from datetime import datetime
    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return abs((ta - tb).total_seconds()) / 60
    except (AttributeError, ValueError, TypeError):
        return None


def probe_only(videos):
    for v in videos:
        i = vio.probe(v)
        ok = (i["avg_frame_rate"] or 0) >= 200
        print(f"{v.name}: {i['codec']} {i['width']}x{i['height']}, {i['avg_frame_rate'] or 0:.2f} fps avg, "
              f"{i['nb_frames']} frames, {i['duration']:.1f} s, model {i['model']}, created {i['creation']}"
              + ("" if ok else "  <- NOT a 240 fps original (re-send with AirDrop Options > All Photos Data)"))


def check_fps(stem: str, fps: float, f_hi: float, force: bool):
    if fps < 1.9 * f_hi and not force:
        raise SystemExit(
            f"[{stem}] camera runs at {fps:.1f} fps but the top tone is {f_hi:g} Hz (needs > {2 * f_hi:g} fps). "
            "If this was a Slo-mo recording, AirDrop it with Options > All Photos Data so the 240 fps original "
            "arrives; or decode with --profile cam120 if it was transmitted with that profile.")


def build_trace(video: Path, out: Path, plan: dict, refresh: float, roi_rect, reuse: bool,
                force: bool = False) -> dict:
    tr_path = out / "trace.npz"
    if reuse and tr_path.exists():
        z = np.load(tr_path, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return {"t": z["t"], "v": z["v"], "mask": z["mask"], "score": z["score"], "amp": z["amp"],
                "mean_dn": z["mean_dn"], "preview": z["preview"], "meta": meta}
    t0 = time.time()
    print(f"[{video.name}] decoding video frames ...", flush=True)
    rd = vio.read_cells(video)
    t, tmeta = vio.regularize_timestamps(rd["t_pts"], rd["info"]["time_base"])
    p = plan["params"]
    f0, f1 = refresh / p["p0"], refresh / p["p1"]
    check_fps(video.stem, tmeta["fps_measured"], max(f0, f1), force)
    roi = vio.select_roi(t, rd["cells"], f0, f1, tmeta["fps_measured"])
    if roi_rect is not None:
        mask = vio.roi_from_rect(roi_rect, rd["info"], rd["analysis_size"], rd["cell"], roi["mask"].shape)
        roi_mode = "manual"
    else:
        mask = roi["mask"]
        roi_mode = "auto"
    if not mask.any():
        raise SystemExit(f"[{video.name}] no screen ROI found; pass --roi x,y,w,h (pixels of the video)")
    v = rd["cells"][:, mask].mean(axis=1).astype(float)
    ys, xs = np.nonzero(mask)
    c = rd["cell"]
    sx = rd["info"]["width"] / rd["analysis_size"][0]
    sy = rd["info"]["height"] / rd["analysis_size"][1]
    meta = {"video_info": rd["info"], "timestamps": tmeta, "n_frames": int(t.size),
            "roi": {"mode": roi_mode, "rect": list(roi_rect) if roi_rect else None, "n_cells": int(mask.sum()),
                    "bbox_px": [int(xs.min() * c * sx), int(ys.min() * c * sy),
                                int((xs.max() + 1 - xs.min()) * c * sx), int((ys.max() + 1 - ys.min()) * c * sy)],
                    "mean_dn": float(roi["mean_dn"][mask].mean()),
                    "saturated_cell_frac": float((roi["mean_dn"][mask] > 250).mean())},
            "read_seconds": round(time.time() - t0, 1)}
    out.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(tr_path, t=t, v=v, t_pts=rd["t_pts"], mask=mask, score=roi["score"], amp=roi["amp"],
                        mean_dn=roi["mean_dn"], preview=rd["preview"].astype(np.float32), meta=json.dumps(meta))
    return {"t": t, "v": v, "mask": mask, "score": roi["score"], "amp": roi["amp"],
            "mean_dn": roi["mean_dn"], "preview": rd["preview"], "meta": meta}


def tone_check(dec: dict, refresh: float, plan: dict) -> dict | None:
    """Measured pilot tone frequencies: catches wrong time bases (e.g. a slo-mo
    export with slowed timestamps) and a display not running at the assumed rate."""
    if dec["pilot"] is None:
        return None
    fs = dec["fs"]
    Lp = plan["params"]["pilotFrames"] / refresh
    out = {}
    for name, start, f in (("f0", 0.0, dec["f0"]), ("f1", Lp, dec["f1"])):
        a = int((dec["pilot"]["t"] + start + 0.1 * Lp - dec["t0"]) * fs)
        b = int((dec["pilot"]["t"] + start + 0.9 * Lp - dec["t0"]) * fs)
        x = dec["x"][a:b] * np.hanning(b - a)
        X = np.abs(np.fft.rfft(x, 16 * (b - a)))
        F = np.fft.rfftfreq(16 * (b - a), 1 / fs)
        sel = (F > 0.5 * f) & (F < 1.5 * f)
        fm = float(F[sel][np.argmax(X[sel])])
        out[name] = {"expected_hz": f, "measured_hz": fm, "error_pct": 100 * (fm - f) / f}
    out["ok"] = all(abs(out[k]["error_pct"]) < 2.0 for k in ("f0", "f1"))
    return out


# ------------------------------------------------------------------ figures
def make_figures(out: Path, tr: dict, dec: dict, plan: dict, sc: dict | None, refresh: float, title: str):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from scipy.signal import spectrogram
    import plot_style as ps

    ps.apply()
    t, v = tr["t"], tr["v"]
    p = plan["params"]
    pk_blocks = [b for b in plan["blocks"] if b["type"] == "packet"]
    rates = sorted({refresh / b["N"] for b in pk_blocks})
    color_of = {r: ps.SERIES[i % len(ps.SERIES)] for i, r in enumerate(rates)}

    # 1. luminance trace with packet spans
    fig, axs = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True)
    axs[0].plot(t, v, color=ps.TEXT2, lw=0.6)
    axs[0].set_ylabel("ROI mean luma (DN)")
    axs[1].plot(dec["grid_t"], dec["x"], color=ps.TEXT2, lw=0.4)
    axs[1].set_ylabel("high-passed relative change")
    axs[1].set_xlabel("video time (s)")
    for b, d in zip(pk_blocks, dec["packets"]):
        if d.get("t_start") is None:
            continue
        dur = b["frames"] / refresh * dec["time_scale"]
        r = refresh / b["N"]
        for ax in axs:
            ax.axvspan(d["t_start"], d["t_start"] + dur, color=color_of[r], alpha=0.15, lw=0)
        axs[1].axvline(d["t_start"], color=color_of[r], lw=1.2 if d["synced"] else 0.6,
                       ls="-" if d["synced"] else "--")
    if dec["pilot"]:
        Lp = p["pilotFrames"] / refresh
        for ax in axs:
            ax.axvspan(dec["pilot"]["t"], dec["pilot"]["t"] + 2 * Lp, color=ps.SERIES[7], alpha=0.12, lw=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=color_of[r], alpha=0.35) for r in rates]
    axs[0].legend(handles, [f"{r:g} bps packets" for r in rates], ncol=min(5, len(rates)), frameon=False,
                  loc="upper right", fontsize=8)
    axs[0].set_title(f"{title}: ROI luminance (shaded: pilot in red, packets by symbol rate)")
    fig.tight_layout()
    fig.savefig(out / "trace.png", dpi=140)
    plt.close(fig)

    # 2. zoom on the first payload symbols of each rate
    firsts = {}
    for b, d in zip(pk_blocks, dec["packets"]):
        r = refresh / b["N"]
        if r not in firsts and d.get("t_start") is not None:
            firsts[r] = (b, d)
    if firsts:
        fig, axs = plt.subplots(len(firsts), 1, figsize=(11, 1.9 * len(firsts) + 0.6), squeeze=False)
        for ax, (r, (b, d)) in zip(axs[:, 0], sorted(firsts.items())):
            Ts = b["N"] / refresh * dec["time_scale"]
            nsym = 12
            ta = d["t_start"] + b["payloadStart"] * Ts
            sel = (dec["grid_t"] >= ta) & (dec["grid_t"] <= ta + nsym * Ts)
            ax.plot(dec["grid_t"][sel] - ta, dec["x"][sel], color=ps.TEXT2, lw=0.8)
            truth = b["bits"][b["payloadStart"]:]
            ymax = np.max(np.abs(dec["x"][sel])) if sel.any() else 1
            for j in range(nsym):
                ax.axvline(j * Ts, color=ps.GRID, lw=1)
                if j < len(truth):
                    got = d["bits"][j] if j < len(d["bits"]) else None
                    col = ps.BIT1 if truth[j] else ps.BIT0
                    lab = f"{truth[j]}" + ("" if got == truth[j] else f"/{got}")
                    ax.text((j + 0.5) * Ts, 1.05 * ymax, lab, ha="center", va="bottom", fontsize=9, color=col,
                            fontweight="bold" if got != truth[j] else "normal")
            ax.set_ylim(-1.3 * ymax, 1.35 * ymax)
            ax.set_ylabel(f"{r:g} bps")
            ax.grid(False)
        axs[-1, 0].set_xlabel("time from first payload symbol (s); labels = sent bit (/decoded if wrong)")
        axs[0, 0].set_title(f"{title}: first payload symbols per rate (bit 0 = {refresh / p['p0']:g} Hz, "
                            f"bit 1 = {refresh / p['p1']:g} Hz)")
        fig.tight_layout()
        fig.savefig(out / "symbols.png", dpi=140)
        plt.close(fig)

    # 3. spectrogram
    fs = dec["fs"]
    f, tt, S = spectrogram(dec["x"], fs=fs, nperseg=int(fs * 0.25), noverlap=int(fs * 0.2))
    fmax = min(fs / 8, 2.2 * max(dec["f0"], dec["f1"]))
    sel = f <= fmax
    fig, ax = plt.subplots(figsize=(11, 3.8))
    ax.pcolormesh(tt + dec["grid_t"][0], f[sel], 10 * np.log10(S[sel] + 1e-20), shading="auto", cmap="Blues")
    for fr, col, lab in ((dec["f0"], ps.BIT0, "f0 (bit 0)"), (dec["f1"], ps.BIT1, "f1 (bit 1)")):
        ax.axhline(fr, color=col, lw=0.8, ls="--")
        ax.text(ax.get_xlim()[0], fr, f" {lab} {fr:g} Hz", color=ps.TEXT, va="bottom", fontsize=8)
    ax.set_xlabel("video time (s)")
    ax.set_ylabel("frequency (Hz)")
    ax.set_title(f"{title}: spectrogram of the high-passed ROI signal")
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(out / "spectrogram.png", dpi=140)
    plt.close(fig)

    # 4. BER and data rate vs symbol rate
    if sc:
        segs = sc["segments"]
        r = [s["rate_bps"] for s in segs]
        fig, axs = plt.subplots(1, 2, figsize=(10, 3.6))
        axs[0].plot(r, [s["ber"] for s in segs], marker="o", color=ps.SERIES[0])
        axs[0].set_ylabel("bit error rate")
        axs[0].set_ylim(-0.02, max(0.55, max(s["ber"] for s in segs) + 0.05))
        axs[1].plot(r, r, color=ps.GRID, lw=1.5, ls="--")
        axs[1].text(r[-1], r[-1], " symbol rate", color=ps.TEXT2, fontsize=8, va="center")
        axs[1].plot(r, [s["data_rate_bps"] for s in segs], marker="o", color=ps.SERIES[0])
        axs[1].set_ylabel("data rate (correct bits / s)")
        for ax in axs:
            ax.set_xlabel("symbol rate (bps)")
            ax.set_xticks(r)
        fig.suptitle(f"{title}: BER and data rate per symbol rate", fontsize=11)
        fig.tight_layout()
        fig.savefig(out / "ber_datarate_vs_rate.png", dpi=140)
        plt.close(fig)

    # 5. ROI
    prev = tr["preview"]
    Hc, Wc = tr["mask"].shape
    cell = prev.shape[0] // Hc
    fig, axs = plt.subplots(1, 2, figsize=(11, 3.4))
    axs[0].imshow(prev, cmap="gray", vmin=0, vmax=255)
    m = np.kron(tr["mask"], np.ones((cell, cell)))[: prev.shape[0], : prev.shape[1]]
    axs[0].contour(m, levels=[0.5], colors=[ps.SERIES[1]], linewidths=1.5)
    axs[0].set_title("frame with selected ROI (orange)")
    im = axs[1].imshow(np.log10(np.maximum(tr["score"], 1e-3)), cmap="Blues")
    axs[1].set_title("log10 band-power score per cell")
    fig.colorbar(im, ax=axs[1], fraction=0.03)
    for ax in axs:
        ax.set_xticks([]); ax.set_yticks([]); ax.grid(False)
    fig.tight_layout()
    fig.savefig(out / "roi.png", dpi=120)
    plt.close(fig)


# ------------------------------------------------------------------ main
def decode_one(video: Path, args) -> dict:
    stem = video.stem
    out = args.out_root / stem
    out.mkdir(parents=True, exist_ok=True)
    name = hp.parse_recording_name(stem)
    txlog_path = args.txlog or (video.parent / f"{stem}_txlog.json")
    txlog = load_txlog(txlog_path) if txlog_path.exists() else None
    params = {k: getattr(args, k) for k in PLAN_KEYS if getattr(args, k, None) is not None}
    params["dalpha"] = name.dalpha if name.dalpha else 0.1
    if txlog:
        params = dict(txlog["params"])
    if params.get("mode") == "text" and args.text:
        params["text"] = args.text
    plan = hp.build_plan(params)
    refresh = args.refresh or (txlog["refresh_fit_hz"] if txlog and txlog["refresh_fit_hz"] else 120.0)
    roi_rect = tuple(int(x) for x in args.roi.split(",")) if args.roi else None
    nominal = (txlog["nominal_hz"] if txlog else None) or round(refresh)
    tr = build_trace(video, out, plan, refresh, roi_rect, args.reuse_trace, args.force)
    fps = tr["meta"]["timestamps"]["fps_measured"]
    f_hi = refresh / min(plan["params"]["p0"], plan["params"]["p1"])
    check_fps(stem, fps, f_hi, args.force)
    warnings = []
    if fps < 2.5 * f_hi:
        warnings.append(f"camera {fps:.1f} fps is close to twice the top tone ({f_hi:g} Hz): aliasing risk")
    if txlog:
        gap = minutes_apart(tr["meta"]["video_info"].get("creation"), txlog.get("started_at"))
        if gap is not None and gap > 10:
            warnings.append(f"video was created {gap:.0f} min from the transmitter log start: is this the right txlog?")
        if txlog.get("aborted"):
            warnings.append("transmitter log says the run was aborted")
    if tr["meta"]["roi"]["saturated_cell_frac"] > 0.2:
        warnings.append("more than 20% of ROI cells are clipped (>250 DN): lower the exposure (AE lock, drag sun down)")
    text_mode = plan["params"]["mode"] == "text"
    rx_plan = plan
    if text_mode:
        rx_plan = hp.build_plan(dict(plan["params"], text="X", repeats=1))
    dec = rxm.decode_trace(tr["t"], tr["v"], rx_plan, refresh, fps, open_ended=text_mode)
    tones = tone_check(dec, refresh, plan)
    if dec["pilot"] is None:
        warnings.append("pilot not found: no packets decoded (check ROI image and recording start)")
    elif tones and not tones["ok"]:
        warnings.append("pilot tone frequencies are off by >2%: timestamps or display refresh do not match")
    sc = None
    received_text = None
    if text_mode:
        received_text = [dict(hp.decode_text_bits(p["bits"], plan["params"]["textChars"]),
                              synced=p["synced"], t_start=p["t_start"]) for p in dec["packets"]]
        if args.text or txlog:   # scoring only; the receiver above never saw the words
            sc = rxm.score(plan, dec, refresh, nominal)
    else:
        sc = rxm.score(plan, dec, refresh, nominal)
    res = {
        "recording": stem, "video": str(video.resolve()),
        "synthetic": stem.startswith("synth_"),
        "dalpha": plan["params"]["dalpha"] if txlog else name.dalpha,
        "distance_cm": name.distance_cm,
        "refresh_hz": refresh, "nominal_refresh_hz": nominal, "plan_params": plan["params"],
        "plan_summary": hp.summary(plan, nominal),
        "txlog": txlog, "trace": tr["meta"], "camera_fps": fps,
        "pilot": dec["pilot"], "pilot_tones": tones, "time_scale": dec["time_scale"],
        "packets": [{k: p.get(k) for k in ("index", "start_frame", "N", "t_start", "corr", "synced", "missing")}
                    for p in dec["packets"]],
        "segments": sc["segments"] if sc else None, "overall": sc["overall"] if sc else None,
        "received_text": received_text, "warnings": warnings,
        "definitions": {"ber": "1 - correct payload bits / transmitted payload bits (undecoded bits count as wrong)",
                        "data_rate_bps": "correct payload bits / payload airtime",
                        "data_rate_incl_overhead_bps": "correct payload bits / (packet incl. 13-symbol preamble + 0.5 s gap)"},
    }
    (out / "results.json").write_text(json.dumps(res, indent=1, default=float))
    title = ("SYNTHETIC " if res["synthetic"] else "") + stem
    make_figures(out, tr, dec, plan if sc else rx_plan, sc, refresh, title)
    line = f"[{stem}] fps {fps:.1f}, ROI {tr['meta']['roi']['n_cells']} cells, pilot " + \
        ("missing" if dec["pilot"] is None else f"at {dec['pilot']['t']:.2f} s")
    print(line)
    if sc:
        for s in sc["segments"]:
            print(f"   {s['rate_bps']:5g} bps: BER {s['ber']:.4f} ({s['bit_errors']}/{s['payload_bits']} wrong), "
                  f"data rate {s['data_rate_bps']:.2f} bps, synced {s['synced']}/{s['packets']}")
    if received_text:
        print("   received: " + " ".join(w["text"] + ("" if w["crc_ok"] else "(crc!)") for w in received_text))
    for w in warnings:
        print("   WARNING: " + w)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="+", type=Path)
    ap.add_argument("--out-root", type=Path, default=None, help="results go to <out-root>/<video stem>/")
    ap.add_argument("--probe-only", action="store_true", help="only print fps / duration / creation time per file")
    ap.add_argument("--txlog", type=Path, default=None, help="transmitter log JSON (default: <stem>_txlog.json beside the video)")
    ap.add_argument("--refresh", type=float, default=None, help="display refresh Hz (default: from txlog, else 120)")
    ap.add_argument("--roi", default=None, help="manual ROI x,y,w,h in video pixels")
    ap.add_argument("--reuse-trace", action="store_true", help="reuse <out>/trace.npz instead of re-reading the video")
    ap.add_argument("--force", action="store_true", help="decode even if the camera fps is too low")
    ap.add_argument("--text", default=None, help="text mode: sent text, to score BER")
    for k in PLAN_KEYS:
        ap.add_argument("--" + k, default=None)
    args = ap.parse_args(argv)
    for v in args.videos:
        if not v.exists():
            raise SystemExit(f"missing video {v}")
    if args.probe_only:
        probe_only(args.videos)
        return
    if args.out_root is None:
        raise SystemExit("--out-root is required")
    for v in args.videos:
        decode_one(v, args)


if __name__ == "__main__":
    sys.exit(main())
