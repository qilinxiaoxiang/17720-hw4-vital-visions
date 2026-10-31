"""Synthetic end-to-end check of heart_rate.py.

Renders a 30 s fingertip-like video (red frames whose brightness dips with each pulse,
plus slow drift and sensor noise), encodes it as HEVC .mov like an iPhone recording,
and runs the real command-line pipeline on it. This validates the code only; it is not
a measurement.

Usage: python test_heart_rate.py OUTDIR
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent


def render(path, bpm, fs=30.0, seconds=30.0, seed=0):
    rng = np.random.default_rng(seed)
    n = int(fs * seconds)
    t = np.arange(n) / fs
    f = bpm / 60.0
    # Beat-to-beat variation: integrate a slightly wandering instantaneous rate.
    inst = f * (1 + 0.03 * np.sin(2 * np.pi * 0.1 * t))
    phase = 2 * np.pi * np.cumsum(inst) / fs
    pulse = np.maximum(0, np.sin(phase)) ** 3 + 0.25 * np.maximum(0, np.sin(phase - 1.2)) ** 2
    level = 150 - 3.0 * pulse - 4.0 * t / seconds + 1.5 * np.sin(2 * np.pi * 0.07 * t)
    w, h = 320, 240
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{w}x{h}", "-r", f"{fs}", "-i", "-", "-c:v", "libx265", "-tag:v", "hvc1",
           "-x265-params", "log-level=error", "-crf", "18", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for v in level:
        frame = np.empty((h, w, 3), np.float32)
        frame[..., 0] = v + 60
        frame[..., 1] = v * 0.25
        frame[..., 2] = v * 0.15
        frame += rng.normal(0, 4, frame.shape)
        proc.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    proc.stdin.close()
    if proc.wait():
        raise RuntimeError("ffmpeg failed")


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    for bpm in (62.0, 72.0, 95.0):
        video = out / f"synthetic-{int(bpm)}bpm.mov"
        render(video, bpm)
        res = subprocess.run([sys.executable, str(HERE / "heart_rate.py"), str(video),
                              "--out", str(out), "--label", f"Synthetic-{int(bpm)}bpm"],
                             capture_output=True, text=True, check=True)
        r = json.loads(res.stdout)
        expected_beats = bpm / 60 * r["duration_used_s"]
        ok_fft = abs(r["hr_fft_bpm"] - bpm) <= r["fft_bin_spacing_bpm"]
        ok_peaks = abs(r["hr_peaks_bpm_mean_ibi"] - bpm) <= 2.0
        ok_count = abs(r["n_peaks"] - expected_beats) <= 1.5
        line = (f"{bpm:.0f} bpm: fft {r['hr_fft_bpm']:.1f} (bin {r['fft_bin_spacing_bpm']:.2f}), "
                f"peaks {r['hr_peaks_bpm_mean_ibi']:.1f}, beats {r['n_peaks']} vs {expected_beats:.1f}, "
                f"fps {r['timestamp_fps_median']:.2f}")
        print(("PASS " if ok_fft and ok_peaks and ok_count else "FAIL ") + line)
        if not (ok_fft and ok_peaks and ok_count):
            failures.append(line)
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
