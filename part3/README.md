# HW4 Part 3: screen-to-camera BFSK through the alpha channel

A HiLight-style link (Li et al., MobiSys 2015, `https://www.cs.columbia.edu/~xia/publication/mobisys15-hilight/mobisys15-hilight.pdf`). A black layer sits over a static picture. Its alpha switches every display frame. Bit 0 and bit 1 are two flicker frequencies (BFSK). The iPhone records the screen, and Python decodes the recording on the Mac.

How to record: `Recording-Protocol.md`.

## Design

- **Display.** The built-in MacBook Pro display runs at 120 Hz (ProMotion) in Chrome. The schedule is counted in display frames, and `transmitter.html` measures the real refresh at startup.
- **Tones.** With the `cam240` profile, bit 0 has a 4-frame period (30 Hz) and bit 1 has a 2-frame period (60 Hz). These are the highest square waves a 120 Hz display can draw, which keeps flicker as high as possible above the low-frequency ambient changes HiLight filters out (< 15 Hz). Both tones need a camera above 120 fps, so recordings use iPhone Slo-mo at 1080p 240 fps (Nyquist frequency 120 Hz).
- **Symbol rates.** One symbol lasts N frames. N is a multiple of 4, so each symbol holds whole periods of both tones. The tones are then orthogonal (spacing 30 Hz = k/T), and the mean alpha is the same for bit 0, bit 1 and idle, so the bit pattern itself causes no flicker at the symbol rate. The sweep uses N = 24, 16, 12, 8, 4, giving 5, 7.5, 10, 15 and 30 bps. 30 bps (one 30 Hz period per symbol) is the top BFSK rate a 120 Hz display allows.
- **cam120 profile.** Tones of 15 Hz and 30 Hz at 5, 7.5 and 15 bps, for a 120 fps camera. This is a fallback only.
- **Frame structure.**
  - 1 s idle lead.
  - Pilot: 1 s of f0, then 1 s of f1. It is used for sync, ROI detection and a time-base check.
  - Packets: a 13-symbol Barker preamble plus payload, each followed by 0.5 s idle at the mean alpha.
  - Sweep: 2 packets x 64 PRBS bits per rate, so 128 payload bits per rate. One recording covers all rates in 91 s.
- **Ground truth.** The xorshift32 PRBS seed (1720) regenerates every bit. `schedule.js` (inlined into the HTML) and `hilight_plan.py` are mirror implementations, and the tests check that they produce identical plans.
- **Receiver** (`receiver.py`). It is streaming and causal, so it can also run on live frames.
  1. The ROI mean is resampled onto a uniform grid at 4x the camera fps, divided by a causal 0.5 s moving mean, and high-passed at 12 Hz.
  2. The pilot is found by its tone-power fraction.
  3. Each packet is found by correlating the Barker preamble against soft symbols, around the time predicted from the schedule.
  4. Each symbol is decided by non-coherent energy at f0 and f1 (a projection onto e^{-j2πft}), as soon as its window is complete.
  5. The camera/display clock ratio comes from a least-squares fit of the packet sync times.

  Real-time on-phone decoding is not implemented here. The phone records, and the Mac decodes afterwards with the same per-symbol logic.
- **Video input** (`video_io.py`). One ffmpeg pass yields each frame's own timestamp (showinfo filter) and 16-bit cell means on a 48 x 27 grid.
  - iPhone files use 1/600 s timestamp ticks (2.5 ticks per 240 fps frame). Those timestamps are refitted to an even frame grid, and real dropped frames stay as gaps.
  - The ROI is the largest connected group of cells whose 24 to 96 Hz band power is far above their out-of-band noise. You can override it with `--roi x,y,w,h`.
- **Metrics** (assignment definitions):
  - BER = 1 - correct / transmitted payload bits. Bits of packets that were never decoded count as wrong.
  - Data rate = correct payload bits / payload airtime.
  - `data_rate_incl_overhead_bps` also charges the preamble and the gap.

## Commands

Run everything from this folder with the course venv:

```bash
cd part3
PY=python3
```

**Tests.** About 2 minutes, most of it the headless-Chrome checks. They need node 20 and Google Chrome, and run headless.

```bash
$PY -m unittest discover -s tests -v
```

**Transmitter.** Open it in Chrome on the built-in display:

```bash
open -a "Google Chrome" transmitter.html
```

URL parameters preset the form, for example `transmitter.html?mode=sweep&dalpha=0.5&label=p3_da050_d040`. The available parameters are `mode` (sweep, prbs, text, eyecheck), `profile`, `dalpha`, `base`, `N`, `p0`, `p1`, `sweepN`, `seed`, `packetsPerRate`, `packets`, `payloadBits`, `text`, `textChars`, `repeats`, `countdownSec` and `trialSec`.

After editing `schedule.js` or the picture, rebuild the self-contained HTML:

```bash
$PY make_background.py && $PY build_transmitter.py
```

**Check AirDropped files.** Every file must show about 240 fps.

```bash
$PY decode_video.py --probe-only ../../Data/part3/p3_da*.mov
```

**Decode the recordings.** Expect about 1.5 minutes per file. Run them one at a time with low priority:

```bash
nice -n 15 $PY decode_video.py ../../Data/part3/p3_da*.mov --out-root ../../Analysis/Part3/results
```

Each `../../Analysis/Part3/results/<stem>/` gets these files:

- `results.json`: BER and data rate per symbol rate, sync, ROI, timestamps and warnings.
- `trace.png`, `symbols.png`, `spectrogram.png`, `ber_datarate_vs_rate.png`, `roi.png`.
- `trace.npz`. Re-run with `--reuse-trace` to skip video decoding.

The transmit log `<stem>_txlog.json`, placed next to the video, is used automatically. Without it, the plan is rebuilt from the default parameters, and delta alpha and distance come from the file name `p3_daXXX_dYYY`.

**Report figures.** These aggregate the 8 recordings:

```bash
$PY summarize.py --results-root ../../Analysis/Part3/results --out ../../Analysis/Part3/figures --eyecheck ../../Data/part3/p3_eyecheck.json
```

The outputs are:

- `ber_vs_dalpha_by_distance.png` and `datarate_vs_dalpha_by_distance.png`: one panel per symbol rate.
- `ber_vs_rate_by_condition.png`.
- `max_reliable_rate_vs_dalpha.png`: the highest rate with BER <= 1%.
- `summary.csv` and `summary.json`.
- `eye_check.csv`.

**Words demo** (text mode). The receiver does not know the words, and the decoder prints what it received. The log is only used to score BER afterwards.

```bash
nice -n 15 $PY decode_video.py ../../Data/part3/p3_text_demo.mov --out-root ../../Analysis/Part3/results
```

**Synthetic validation.** This checks the code only. It is not measurement data. The first two commands take about 3 minutes of full CPU, so run them on the Mac mini (copy this folder to a scratch folder there and copy the outputs back). Decoding all 9 videos takes about 1.5 minutes.

```bash
nice -n 15 $PY simulate.py signal-sweep --out ../../Checks/Part3-Synthetic
nice -n 15 $PY simulate.py make-videos --stress --out ../../Checks/Part3-Synthetic/videos
nice -n 15 $PY decode_video.py ../../Checks/Part3-Synthetic/videos/synth_p3_da0[15]0_d*.mov --out-root ../../Checks/Part3-Synthetic/results
nice -n 15 $PY decode_video.py ../../Checks/Part3-Synthetic/videos/synth_p3_da002_d120_lowsnr.mov --packetsPerRate 1 --payloadBits 48 --roi 143,70,53,32 --out-root ../../Checks/Part3-Synthetic/stress-results
$PY summarize.py --results-root ../../Checks/Part3-Synthetic/results --out ../../Checks/Part3-Synthetic/figures --synthetic
```

The 8 standard videos use the default sweep plan. The stress clip (delta alpha 0.02, 120 cm, 40x the default frame-wide noise) is shorter, one 48-bit packet per rate, so its decode needs those two plan flags. At that SNR the automatic ROI finds no screen, so it gets a manual ROI (the screen rectangle from `synthetic_manifest.json`, inset by 4 px). It is kept out of `results/` so the summary figures show only the 8 assignment conditions.

## Files

- `transmitter.html`: the self-contained transmitter, with `schedule.js` and `background.png` inlined. `background.png` is a procedurally drawn landscape made by `make_background.py`.
- `schedule.js` and `hilight_plan.py`: the BFSK schedule in JS and its Python mirror (the ground truth).
- `receiver.py`, `video_io.py`, `decode_video.py`: the receiver and the decoder CLI.
- `camera_model.py` and `simulate.py`: the screen-to-camera model and the synthetic validation.
- `summarize.py` and `plot_style.py`: the report figures.
- `tests/`:
  - schedule parity, JS against Python;
  - headless-Chrome transmitter checks: static schedule, the live requestAnimationFrame loop, and the preview, Space, DONE and eye-check user flows;
  - receiver checks on the signal-level model;
  - an end-to-end check on a short synthetic video through the CLIs.
