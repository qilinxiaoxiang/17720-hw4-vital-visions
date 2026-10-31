"""HW4 Part 1: heart rate from a fingertip video recorded with the phone torch on.

Pipeline (follows the assignment steps and HemaApp [4] steps 1-3):
  1. per-frame grayscale mean brightness
  2. subtract the DC value, high-pass filter
  3. FFT, strongest bin inside 60-100 bpm -> heart-rate estimate
  4. peak picking with a minimum peak distance derived from that estimate

Usage:
  python heart_rate.py VIDEO --out OUTDIR [--label NAME] [--trim 1.0]
"""

import argparse
import json
import subprocess
from pathlib import Path

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, filtfilt, find_peaks

HR_BAND_BPM = (60.0, 100.0)


def frame_brightness(video_path):
    """Mean grayscale value of every frame, plus per-frame timestamps in seconds."""
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video_path}")
    values, times, rgb = [], [], []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        times.append(cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)  # same weights as MATLAB rgb2gray
        values.append(float(gray.mean()))
        b, g, r = frame.reshape(-1, 3).mean(axis=0)
        rgb.append((float(r), float(g), float(b)))
    cap.release()
    if len(values) < 10:
        raise RuntimeError(f"only {len(values)} frames decoded from {video_path}")
    return np.array(times), np.array(values), np.array(rgb)


def probe_fps(video_path):
    """Nominal frame rate from ffprobe, used to check the decoded timestamps."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=avg_frame_rate", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, check=True).stdout.strip().strip(",")
        num, den = out.split("/")
        return float(num) / float(den)
    except Exception:
        return None


def analyze(times, values, fs, trim_s=1.0, hp_cutoff=0.5, lp_cutoff=4.0):
    # Drop the first/last seconds where the finger settles and exposure adapts.
    n_trim = int(round(trim_s * fs))
    sl = slice(n_trim, len(values) - n_trim if n_trim else None)
    t, x = times[sl] - times[sl][0], values[sl]

    # Step 2: subtract DC, then high-pass to remove slow drift from pressure and exposure.
    x_ac = x - x.mean()
    b, a = butter(2, hp_cutoff / (fs / 2), btype="highpass")
    x_hp = filtfilt(b, a, x_ac)

    # Step 3: FFT; the strongest bin between 60 and 100 bpm is the heart-rate estimate.
    n = len(x_hp)
    spectrum = np.abs(np.fft.rfft(x_hp * np.hanning(n))) / n
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    lo, hi = HR_BAND_BPM[0] / 60.0, HR_BAND_BPM[1] / 60.0
    band = np.where((freqs >= lo) & (freqs <= hi))[0]
    k = band[np.argmax(spectrum[band])]
    hr_hz = float(freqs[k])

    # Step 4: peaks. More blood absorbs more light, so each pulse is a brightness dip;
    # inverting makes the systolic pulse a peak. A light low-pass removes frame noise.
    b, a = butter(2, lp_cutoff / (fs / 2), btype="lowpass")
    ppg = -filtfilt(b, a, x_hp)
    period_samples = fs / hr_hz
    # Use 70% of the FFT period so normal beat-to-beat variation is not suppressed.
    min_dist = max(1, int(np.floor(0.7 * period_samples)))
    peaks, _ = find_peaks(ppg, distance=min_dist, prominence=0.3 * np.std(ppg))
    ibi = np.diff(t[peaks])
    # Mean interval = beats per unit time. The median snaps to whole-frame intervals
    # (1/30 s steps), so it is kept only as a secondary figure.
    hr_peaks_bpm = float(60.0 / np.mean(ibi)) if len(ibi) else float("nan")
    hr_peaks_median_bpm = float(60.0 / np.median(ibi)) if len(ibi) else float("nan")

    return {
        "t": t, "raw": x, "hp": x_hp, "ppg": ppg, "freqs": freqs, "spectrum": spectrum,
        "band_idx": band, "fft_bin": int(k), "peaks": peaks,
        "results": {
            "fs_hz": fs,
            "samples_used": int(n),
            "duration_used_s": float(n / fs),
            "fft_bin_spacing_hz": float(fs / n),
            "fft_bin_spacing_bpm": float(60 * fs / n),
            "hr_fft_hz": hr_hz,
            "hr_fft_bpm": hr_hz * 60.0,
            "period_samples_at_fft_hr": float(period_samples),
            "min_peak_distance_samples": int(min_dist),
            "n_peaks": int(len(peaks)),
            "hr_peaks_bpm_mean_ibi": hr_peaks_bpm,
            "hr_peaks_bpm_median_ibi": hr_peaks_median_bpm,
            "ibi_mean_s": float(np.mean(ibi)) if len(ibi) else None,
            "ibi_std_s": float(np.std(ibi)) if len(ibi) else None,
            "hp_cutoff_hz": hp_cutoff,
            "lp_cutoff_hz_for_peaks": lp_cutoff,
            "trim_s": trim_s,
        },
    }


def plot(res, out_dir, label):
    r = res["results"]
    out_dir.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 3.6))
    f, s = res["freqs"], res["spectrum"]
    show = f <= 5
    ax.plot(f[show], s[show], color="0.3", lw=1)
    lo, hi = HR_BAND_BPM[0] / 60, HR_BAND_BPM[1] / 60
    ax.axvspan(lo, hi, color="tab:blue", alpha=0.08, label="60-100 bpm search band")
    ax.plot(f[res["fft_bin"]], s[res["fft_bin"]], "o", color="tab:red",
            label=f"peak {r['hr_fft_hz']:.3f} Hz")
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("Amplitude (gray levels)")
    ax.set_title(f"{label}: FFT of filtered signal, estimated heart rate "
                 f"{r['hr_fft_hz']:.3f} Hz ({r['hr_fft_bpm']:.1f} bpm)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out_dir / f"{label}-FFT.png", dpi=200)
    plt.close(fig)

    fig, axes = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True)
    t = res["t"]
    axes[0].plot(t, res["raw"], color="0.3", lw=1)
    axes[0].set_ylabel("Mean gray level")
    axes[0].set_title(f"{label}: raw frame brightness ({r['fs_hz']:.2f} fps)")
    axes[1].plot(t, res["ppg"], color="tab:blue", lw=1, label="filtered, inverted")
    axes[1].plot(t[res["peaks"]], res["ppg"][res["peaks"]], "v", color="tab:red",
                 label=f"{r['n_peaks']} detected beats")
    axes[1].set_xlabel("Time (s)")
    axes[1].set_ylabel("Pulse signal (a.u.)")
    axes[1].set_title(f"Heart rate from peaks: {r['hr_peaks_bpm_mean_ibi']:.1f} bpm "
                      f"(min peak distance {r['min_peak_distance_samples']} samples)")
    lo_y, hi_y = res["ppg"].min(), res["ppg"].max()
    axes[1].set_ylim(lo_y, hi_y + 0.45 * (hi_y - lo_y))
    axes[1].legend(frameon=False, loc="upper right", ncol=2)
    fig.tight_layout()
    fig.savefig(out_dir / f"{label}-Peaks.png", dpi=200)
    plt.close(fig)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("video")
    p.add_argument("--out", required=True)
    p.add_argument("--label", default=None)
    p.add_argument("--trim", type=float, default=1.0, help="seconds dropped at each end")
    args = p.parse_args()

    video = Path(args.video)
    label = args.label or video.stem
    times, values, rgb = frame_brightness(video)
    # Frame rate from the decoded timestamps; checked against the container's nominal rate.
    dt = np.diff(times)
    fs = float(1.0 / np.median(dt[dt > 0]))
    nominal = probe_fps(video)

    res = analyze(times, values, fs, trim_s=args.trim)
    out_dir = Path(args.out)
    plot(res, out_dir, label)
    summary = {
        "video": str(video),
        "frames_decoded": int(len(values)),
        "nominal_fps_ffprobe": nominal,
        "timestamp_fps_median": fs,
        "frame_interval_jitter_ms_std": float(np.std(dt) * 1000),
        "mean_rgb": [float(v) for v in rgb.mean(axis=0)],
        **res["results"],
    }
    (out_dir / f"{label}-Results.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
