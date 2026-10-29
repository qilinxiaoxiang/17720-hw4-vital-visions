"""Synthetic validation of the decoder (code validation only, NOT measurements).

Subcommands
  signal-sweep  Signal-level camera model (ROI mean computed analytically):
                BER vs noise for each symbol rate, camera-rate (Nyquist) cases,
                slow-LCD / rolling-shutter / flicker robustness cases.
  make-videos   Renders synthetic iPhone-like recordings (.mov, H.264 crf 23, lossy like the iPhone,
                with real per-frame timestamps) for the 8 (delta alpha, distance)
                conditions, named like Shawn's files with a synth_ prefix, so
                the real decoder CLI and summarize.py can run on them unchanged.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

import camera_model as cm
import hilight_plan as hp
import receiver as rxm

HERE = Path(__file__).resolve().parent
FFMPEG = "/opt/homebrew/bin/ffmpeg"


# ---------------------------------------------------------------- signal level
def run_signal_case(plan_params=None, display=None, camera=None, distance_cm=40.0,
                    noise_scale=1.0, seed=0, t_start=3.0, open_ended=False, chunk_s=0.25,
                    decode_params=None):
    """Simulate the ROI trace for one recording and decode it. Returns (plan, dec, score)."""
    plan = hp.build_plan(plan_params or {})
    display = display or cm.DisplayModel()
    camera = camera or cm.CameraModel()
    camera = replace(camera, noise_px=camera.noise_px * noise_scale,
                     noise_common=camera.noise_common * noise_scale,
                     ambient=cm.glare_for_distance(distance_cm))
    geo = cm.Geometry()
    tx = cm.Transmission(plan, t_start=t_start, seed=seed)
    roi_px = geo.roi_pixels(distance_cm)
    hfrac = min(1.0, geo.screen_fraction(distance_cm)[1])
    t, v = cm.simulate_roi_trace(tx, display, camera, roi_px, hfrac, rng=np.random.default_rng(seed))
    rx_plan = hp.build_plan(decode_params) if decode_params else plan
    dec = rxm.decode_trace(t, v, rx_plan, display.refresh_hz, camera.fps, chunk_s=chunk_s,
                           open_ended=open_ended)
    sc = rxm.score(plan, dec, display.refresh_hz) if not open_ended else None
    return plan, dec, sc


def signal_sweep(out_dir: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import plot_style as ps

    out_dir.mkdir(parents=True, exist_ok=True)
    log = {"note": "SYNTHETIC code validation with camera_model.py; not measurements.", "cases": []}
    noise_scales = [1, 100, 200, 400, 800]
    curves = {}
    for ns in noise_scales:
        for seed in range(1):
            _, dec, sc = run_signal_case({"dalpha": 0.1}, distance_cm=120, noise_scale=ns, seed=seed)
            for s in sc["segments"]:
                c = curves.setdefault(s["rate_bps"], {})
                c.setdefault(ns, []).append(s["ber"])
            log["cases"].append({"case": "noise_sweep", "noise_scale": ns, "seed": seed,
                                 "pilot": dec["pilot"], "segments": sc["segments"]})
    cases = [
        ("cam240 profile, 240 fps camera (baseline)", {}, {}, {}),
        ("cam240 profile, slow LCD tau=10 ms, readout 8 ms", {}, {"tau_s": 0.010}, {"readout_s": 0.008}),
        ("cam240 profile, 60 Hz ambient flicker 20% of glare", {}, {}, {"flicker_amp": 0.2, "flicker_hz": 60.0}),
        ("cam240 profile, 120 fps camera (f1 = Nyquist)", {}, {}, {"fps": 120.0, "exposure_s": 1 / 250}),
        ("cam240 profile, 100 fps camera (f1 above Nyquist)", {}, {}, {"fps": 100.0, "exposure_s": 1 / 250}),
        ("cam240 profile, 60 fps camera (both tones >= Nyquist)", {}, {}, {"fps": 60.0, "exposure_s": 1 / 120}),
        ("cam120 profile, 120 fps camera", {"profile": "cam120"}, {}, {"fps": 120.0, "exposure_s": 1 / 250}),
        ("cam240 profile, display stuck at 60 Hz (Safari-like), decoded as 120 Hz", {}, {"refresh_hz": 60.0}, {}),
    ]
    rows = []
    for name, pp, dkw, ckw in cases:
        pp = dict({"dalpha": 0.1}, **pp)
        disp = replace(cm.DisplayModel(), **dkw)
        cam = replace(cm.CameraModel(), **ckw)
        plan = hp.build_plan(pp)
        tx = cm.Transmission(plan, t_start=3.0, seed=1)
        geo = cm.Geometry()
        cam2 = replace(cam, ambient=cm.glare_for_distance(80))
        t, v = cm.simulate_roi_trace(tx, disp, cam2, geo.roi_pixels(80), 0.33, rng=np.random.default_rng(1))
        dec = rxm.decode_trace(t, v, plan, 120.0, cam.fps)
        sc = rxm.score(plan, dec, 120.0)
        rows.append((name, sc))
        log["cases"].append({"case": name, "pilot": dec["pilot"], "segments": sc["segments"],
                             "overall": sc["overall"]})
    # text demo without knowing the words
    _, dec, _ = run_signal_case({"mode": "text", "N": 8, "text": "HELLO VITAL VISIONS", "repeats": 1, "dalpha": 0.1},
                                distance_cm=80, open_ended=True,
                                decode_params={"mode": "text", "N": 8, "text": "X", "repeats": 1})
    words = [hp.decode_text_bits(p["bits"], 10) for p in dec["packets"]]
    log["text_demo"] = {"sent": "HELLO VITAL VISIONS", "received": words}

    (out_dir / "signal_level_results.json").write_text(json.dumps(log, indent=1, default=float))
    ps.apply()
    fig, ax = plt.subplots(figsize=(7, 4.2))
    for i, (rate, c) in enumerate(sorted(curves.items())):
        xs = sorted(c)
        ax.plot(xs, [np.mean(c[x]) for x in xs], marker="o", color=ps.SERIES[i], label=f"{rate:g} bps")
    ax.set_xscale("log")
    ax.set_xlabel("noise scale (x default camera noise), delta alpha 0.1, 120 cm")
    ax.set_ylabel("bit error rate")
    ax.set_ylim(-0.02, 1.05)
    ax.set_title("SYNTHETIC: BER vs camera noise (signal-level model)")
    ax.legend(title="symbol rate", frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / "ber_vs_noise_signal_level.png", dpi=150)
    plt.close(fig)

    lines = ["SYNTHETIC signal-level validation (camera_model.py); code check only, not measurements.", ""]
    lines.append("BER vs noise scale (one seed), delta alpha 0.1, 120 cm:")
    for rate, c in sorted(curves.items()):
        lines.append(f"  {rate:5g} bps: " + ", ".join(f"x{x}: {np.mean(c[x]):.3f}" for x in sorted(c)))
    lines.append("")
    lines.append("Model variants (delta alpha 0.1, 80 cm, default noise):")
    for name, sc in rows:
        segs = ", ".join(f"{s['rate_bps']:g} bps BER {s['ber']:.3f}" for s in sc["segments"])
        lines.append(f"  {name}: {segs}")
    lines.append("")
    lines.append("Text demo (receiver does not know the words): " +
                 " | ".join(f"{w['text']} (crc {'ok' if w['crc_ok'] else 'BAD'})" for w in words))
    (out_dir / "signal_level_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


# ---------------------------------------------------------------- video level
def condition_camera(distance_cm: float, base: cm.CameraModel) -> cm.CameraModel:
    return replace(base, ambient=cm.glare_for_distance(distance_cm))


def render_video(path: Path, plan: dict, distance_cm: float, display: cm.DisplayModel,
                 camera: cm.CameraModel, size=(320, 180), t_start=3.0, seed=0, crf=23, tail_s=2.0):
    """Render a synthetic recording of the transmitter screen (H.264 ultrafast, light on the CPU)."""
    from PIL import Image

    W, H = size
    rng = np.random.default_rng(seed)
    geo = cm.Geometry(frame_w_px=W, frame_h_px=H)
    wf, hf = geo.screen_fraction(distance_cm)
    sw, sh = int(round(wf * W)), int(round(hf * H))
    # screen rectangle (may overfill the frame at 20 cm), slightly off-center
    cx, cy = W / 2 + 0.03 * W, H / 2 - 0.02 * H
    x0, y0 = int(round(cx - sw / 2)), int(round(cy - sh / 2))
    pic = Image.open(HERE / "background.png").convert("L").resize((max(sw, 2), max(sh, 2)), Image.BILINEAR)
    B = (np.asarray(pic, float) / 255.0) ** 2.2
    bez = max(1, int(0.03 * sw))
    B[:bez, :] = B[-bez:, :] = 0.002
    B[:, :bez] = B[:, -bez:] = 0.002
    # composite onto camera frame
    ys, xs = np.mgrid[0:H, 0:W]
    room = 0.06 + 0.05 * np.sin(xs / 37.0) * np.cos(ys / 23.0) + 0.04 * (ys / H)
    scr = np.zeros((H, W), bool)
    Bf = np.zeros((H, W))
    fx0, fy0 = max(x0, 0), max(y0, 0)
    fx1, fy1 = min(x0 + sw, W), min(y0 + sh, H)
    scr[fy0:fy1, fx0:fx1] = True
    Bf[fy0:fy1, fx0:fx1] = B[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    row_frac = np.arange(H) / H

    tx = cm.Transmission(plan, t_start=t_start, seed=seed)
    integ = cm.DisplayIntegrator(tx, display)
    duration = t_start + plan["totalFrames"] / display.refresh_hz + tail_s
    times = cm.capture_times(camera, duration)
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}",
           # settb first: otherwise setpts rounds to the 1/fps input time base
           "-r", str(camera.fps), "-i", "-", "-vf", f"settb=1/240000,setpts='{cm.setpts_expr(camera)}'",
           "-enc_time_base:v", "1/240000",
           "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "ultrafast", "-crf", str(crf),
           "-pix_fmt", "yuv420p",
           "-video_track_timescale", "240000", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    batch = 240
    for i in range(0, times.size, batch):
        t = times[i:i + batch]
        L = cm.row_luminance(integ, camera, t, row_frac)                # (b, H)
        g = cm.gain_drift(camera, t)[:, None, None]
        amb = cm.ambient_term(camera, t)[:, None, None]
        lin = np.where(scr[None], Bf[None] * L[:, :, None], room[None] * 0.3) * g + amb * scr[None]
        dn = cm.to_dn(lin, camera)
        dn += rng.normal(0, camera.noise_px, dn.shape)
        dn += rng.normal(0, camera.noise_common, (t.size, 1, 1))
        proc.stdin.write(np.clip(np.round(dn), 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg failed for {path}")
    return {"frames": int(times.size), "screen_px": [sw, sh], "screen_origin": [x0, y0],
            "frame": [W, H], "duration_s": float(duration)}


def video_name(cond) -> str:
    da, d, extra = cond
    return f"synth_p3_da{int(round(da * 100)):03d}_d{int(d):03d}{extra.get('suffix', '')}.mov"


def make_videos(out_dir: Path, conditions, size=(320, 180)):
    """Render each condition; seeds are fixed per condition, and the manifest keeps
    entries of videos rendered in earlier runs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    mpath = out_dir / "synthetic_manifest.json"
    note = "SYNTHETIC recordings from camera_model.py for decoder validation; not measurements."
    old = json.loads(mpath.read_text()).get("videos", []) if mpath.exists() else []
    entries = {v["file"]: v for v in old if (out_dir / v["file"]).exists()}
    for cond in conditions:
        da, d, extra = cond
        name = video_name(cond)
        plan_params = dict({"dalpha": da}, **extra.get("plan", {}))
        plan = hp.build_plan(plan_params)
        cam = condition_camera(d, replace(cm.CameraModel(), **extra.get("camera", {})))
        disp = replace(cm.DisplayModel(), **extra.get("display", {}))
        t0 = time.time()
        info = render_video(out_dir / name, plan, d, disp, cam, size=size, seed=100 + ALL_CONDITIONS.index(cond))
        info.update({"file": name, "dalpha": da, "distance_cm": d, "plan_params": plan_params,
                     "render_s": round(time.time() - t0, 1), "model": cm.describe(disp, cam)})
        entries[name] = info
        print(f"{name}: {info['frames']} frames, screen {info['screen_px']} px, {info['render_s']} s", flush=True)
    order = [video_name(c) for c in ALL_CONDITIONS]
    videos = sorted(entries.values(), key=lambda v: order.index(v["file"]) if v["file"] in order else 99)
    mpath.write_text(json.dumps({"note": note, "videos": videos}, indent=1))


STANDARD_CONDITIONS = [(da, d, {}) for da in (0.1, 0.5) for d in (20, 40, 80, 120)]
# One short low-SNR clip (one packet of 48 bits per rate, about 40 s): it proves the
# real CLI reports degradation. The BER-vs-noise curve comes from signal-sweep.
STRESS_PLAN = {"packetsPerRate": 1, "payloadBits": 48}
STRESS_CONDITIONS = [(0.02, 120, {"suffix": "_lowsnr", "plan": STRESS_PLAN,
                                  "camera": {"noise_px": 4.0, "noise_common": 2.0}})]
ALL_CONDITIONS = STANDARD_CONDITIONS + STRESS_CONDITIONS


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("signal-sweep")
    a.add_argument("--out", type=Path, required=True)
    b = sub.add_parser("make-videos")
    b.add_argument("--out", type=Path, required=True)
    b.add_argument("--stress", action="store_true", help="also render the low-SNR stress recording")
    b.add_argument("--only", default=None, help="comma list of names like da010_d020 to render")
    args = ap.parse_args(argv)
    if args.cmd == "signal-sweep":
        signal_sweep(args.out)
    else:
        conds = STANDARD_CONDITIONS + (STRESS_CONDITIONS if args.stress else [])
        if args.only:
            keep = set(args.only.split(","))
            conds = [c for c in conds if video_name(c)[len("synth_p3_"):-len(".mov")] in keep]
        make_videos(args.out, conds)


if __name__ == "__main__":
    sys.exit(main())
