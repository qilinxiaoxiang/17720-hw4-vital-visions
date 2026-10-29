"""Video reading for the receiver: true per-frame timestamps and per-cell luminance.

One ffmpeg decode pass gives, for every decoded frame, its presentation time
(from the showinfo filter, so timestamps and frames correspond one-to-one even
with variable frame timing or dropped frames) and the mean luma of each cell
of a coarse grid (area downscale to 16-bit gray, so averages keep sub-DN
precision). The screen ROI is then chosen from the cells.
"""
from __future__ import annotations

import json
import re
import subprocess
import threading
from fractions import Fraction
from pathlib import Path

import numpy as np

FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def probe(path: str | Path) -> dict:
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_format",
                        "-of", "json", str(path)], capture_output=True, text=True, check=True)
    j = json.loads(r.stdout)
    s = j["streams"][0]
    rot = 0
    for sd in s.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = int(round(float(sd["rotation"])))
    w, h = int(s["width"]), int(s["height"])
    if abs(rot) % 180 == 90:
        w, h = h, w
    tags = j.get("format", {}).get("tags", {})
    return {"codec": s.get("codec_name"), "width": w, "height": h, "rotation": rot,
            "r_frame_rate": float(Fraction(s.get("r_frame_rate", "0/1"))) if s.get("r_frame_rate") != "0/0" else None,
            "avg_frame_rate": float(Fraction(s["avg_frame_rate"])) if s.get("avg_frame_rate") not in (None, "0/0") else None,
            "time_base": float(Fraction(s["time_base"])), "nb_frames": int(s.get("nb_frames", 0) or 0),
            "duration": float(j["format"].get("duration", 0) or 0),
            "model": tags.get("com.apple.quicktime.model"), "software": tags.get("com.apple.quicktime.software"),
            "creation": tags.get("com.apple.quicktime.creationdate") or tags.get("creation_time")}


SHOWINFO_RE = re.compile(r"\bn:\s*(\d+)\s+pts:\s*(-?\d+)\s+pts_time:\s*(-?[\d.eE+-]+)")


def read_cells(path: str | Path, analysis_width: int = 384, cell: int = 8, preview_at: float = 0.3,
               progress: bool = True) -> dict:
    """Decode the video once; return timestamps (s) and per-cell mean luma (n, Hc, Wc) in 8-bit DN units."""
    info = probe(path)
    Wa = analysis_width - analysis_width % cell
    Ha = int(round(Wa * info["height"] / info["width"] / cell)) * cell
    Hc, Wc = Ha // cell, Wa // cell
    vf = f"showinfo,scale={Wa}:{Ha}:flags=area,format=gray16le"
    cmd = [FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "info", "-i", str(path), "-map", "0:v:0",
           "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    times: list[float] = []
    err_tail: list[str] = []

    def read_err():
        for line in iter(proc.stderr.readline, b""):
            s = line.decode("utf-8", "replace")
            m = SHOWINFO_RE.search(s)
            if m:
                times.append(float(m.group(3)))
            else:
                err_tail.append(s)
                del err_tail[:-30]

    th = threading.Thread(target=read_err, daemon=True)
    th.start()
    fsize = Wa * Ha * 2
    expect = info["nb_frames"] or None
    preview_idx = int(preview_at * expect) if expect else 120
    chunks = []
    preview = None
    n = 0
    batch = 256
    while True:
        buf = proc.stdout.read(fsize * batch)
        if not buf:
            break
        k = len(buf) // fsize
        fr = np.frombuffer(buf[: k * fsize], dtype="<u2").reshape(k, Ha, Wa).astype(np.float32) / 257.0
        if preview is None and n + k > preview_idx:
            preview = fr[preview_idx - n].copy()
        chunks.append(fr.reshape(k, Hc, cell, Wc, cell).mean(axis=(2, 4)))
        n += k
        if progress and (n // batch) % 40 == 0:
            print(f"  decoded {n} frames", flush=True)
    proc.wait()
    th.join()
    if proc.returncode != 0 or n == 0:
        raise RuntimeError("ffmpeg failed:\n" + "".join(err_tail))
    cells = np.concatenate(chunks)
    if preview is None:
        preview = (cells[n // 2].repeat(cell, 0).repeat(cell, 1))
    if len(times) != n:
        raise RuntimeError(f"frame/timestamp mismatch: {n} frames, {len(times)} showinfo timestamps")
    return {"t_pts": np.asarray(times), "cells": cells, "info": info, "analysis_size": [Wa, Ha],
            "cell": cell, "preview": preview}


def regularize_timestamps(t_pts: np.ndarray, time_base: float) -> tuple[np.ndarray, dict]:
    """Use raw pts unless the container's time base is coarse relative to the
    frame interval (iPhone files use 1/600 s ticks, 2.5 ticks per 240 fps frame);
    then fit t = a + b * slot, where slot counts frame intervals (so real
    dropped frames stay gaps) and the tick rounding is removed."""
    d = np.diff(t_pts)
    k = 8 if t_pts.size > 16 else 1
    dt = float(np.median((t_pts[k:] - t_pts[:-k]) / k))   # averages out tick rounding
    inc = np.maximum(1, np.round(d / dt))
    meta = {"median_dt": dt, "fps_measured": 1 / dt, "time_base": time_base,
            "gaps_over_1p5_frames": int((inc > 1).sum()),
            "nonincreasing": int((d <= 0).sum())}
    if time_base <= dt / 4:
        meta["mode"] = "pts"
        return t_pts.astype(float), meta
    slots = np.concatenate([[0.0], np.cumsum(inc)])
    a, b = np.polyfit(slots, t_pts, 1)[::-1]
    t = a + b * slots
    meta.update({"mode": "regularized", "fit_dt": float(b), "fps_measured": float(1 / b),
                 "residual_ms_rms": float(1000 * np.sqrt(np.mean((t - t_pts) ** 2)))})
    return t, meta


def uniform_resample(t: np.ndarray, Y: np.ndarray, fs: float) -> tuple[np.ndarray, np.ndarray]:
    """Linear interpolation of every column of Y (n, m) onto a uniform grid."""
    tg = np.arange(t[0], t[-1], 1 / fs)
    i = np.clip(np.searchsorted(t, tg, side="right") - 1, 0, t.size - 2)
    w = ((tg - t[i]) / (t[i + 1] - t[i]))[:, None].astype(Y.dtype)
    return tg, Y[i] * (1 - w) + Y[i + 1] * w


def select_roi(t: np.ndarray, cells: np.ndarray, f0: float, f1: float, fps: float,
               score_thr: float = 6.0, amp_frac: float = 0.2) -> dict:
    """Screen ROI = largest connected group of cells whose luminance carries the
    BFSK band (between the two tones) far above its out-of-band noise."""
    import cv2
    from scipy.ndimage import uniform_filter1d

    n, Hc, Wc = cells.shape
    Yall = cells.reshape(n, -1)
    flo, fhi, nyq = min(f0, f1), max(f0, f1), fps / 2
    p_in = np.zeros(Yall.shape[1])
    p_out = np.zeros(Yall.shape[1])
    for c0 in range(0, Yall.shape[1], 128):            # column chunks keep memory small
        Y = Yall[:, c0:c0 + 128].astype(np.float32)
        tg, Yu = uniform_resample(t, Y, fps)
        X = Yu / np.maximum(Yu.mean(axis=0), 1e-3) - 1
        X = X - uniform_filter1d(X, size=max(3, int(fps / 8)), axis=0, mode="nearest")
        F = np.fft.rfftfreq(X.shape[0], 1 / fps)
        P = np.abs(np.fft.rfft(X, axis=0)) ** 2
        inb = (F >= 0.8 * flo) & (F <= min(1.6 * fhi, 0.95 * nyq))
        outb = ((F >= 0.25 * flo) & (F <= 0.6 * flo)) | ((F >= 1.7 * fhi) & (F <= 0.95 * nyq))
        p_in[c0:c0 + 128] = P[inb].mean(axis=0)
        p_out[c0:c0 + 128] = P[outb].mean(axis=0) if outb.any() else np.median(P, axis=0)
    score = (p_in / np.maximum(p_out, 1e-30)).reshape(Hc, Wc)
    amp = np.sqrt(p_in).reshape(Hc, Wc)
    ref = np.percentile(amp, 99)
    mask = (score > score_thr) & (amp > amp_frac * ref)
    if mask.any():
        k, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
        sizes = [(lab == i).sum() for i in range(1, k)]
        mask = lab == (1 + int(np.argmax(sizes)))
    mean_dn = cells.mean(axis=0)
    return {"mask": mask, "score": score, "amp": amp, "mean_dn": mean_dn,
            "n_cells": int(mask.sum()),
            "saturated_frac": float((mean_dn[mask] > 250).mean()) if mask.any() else None}


def roi_from_rect(rect, info: dict, analysis_size, cell: int, grid_shape) -> np.ndarray:
    """Manual ROI x,y,w,h in original (display-oriented) video pixels -> cell mask."""
    x, y, w, h = rect
    sx = analysis_size[0] / info["width"]
    sy = analysis_size[1] / info["height"]
    Hc, Wc = grid_shape
    cy = (np.arange(Hc) + 0.5) * cell / sy
    cx = (np.arange(Wc) + 0.5) * cell / sx
    return ((cy[:, None] >= y) & (cy[:, None] < y + h)) & ((cx[None, :] >= x) & (cx[None, :] < x + w))
