"""End-to-end on a short synthetic H.264 recording through the real CLIs
(decode_video.py, summarize.py). Synthetic data: code validation only."""
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

PART3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART3))

import camera_model as cm  # noqa: E402
import hilight_plan as hp  # noqa: E402
import simulate as sim  # noqa: E402
import video_io as vio  # noqa: E402

PY = sys.executable
SHORT = {"mode": "sweep", "sweepN": "8,4", "packetsPerRate": 1, "payloadBits": 48, "dalpha": 0.5, "seed": 11}


def fake_txlog(plan, refresh=120.0):
    k = np.arange(plan["totalFrames"])
    lv = hp.levels(plan)
    return {"schema": "hilight-txlog-v1", "label": "x", "aborted": False,
            "refresh": {"measuredHz": refresh, "nominalHz": 120, "startupFrameIntervalsMs": []},
            "plan": plan, "frames": {"tsMs": (1000 * k / refresh + 5000).tolist(), "k": k.tolist(),
                                     "level": lv.tolist()}, "droppedFrames": 0}


class VideoPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.video = cls.root / "data" / "synth_p3_da050_d040.mov"
        plan = hp.build_plan(SHORT)
        cam = sim.condition_camera(40, cm.CameraModel())
        cls.render = sim.render_video(cls.video, plan, 40, cm.DisplayModel(), cam, seed=3)
        (cls.root / "data" / "synth_p3_da050_d040_txlog.json").write_text(json.dumps(fake_txlog(plan)))
        cls.out = cls.root / "results"
        r = subprocess.run([PY, str(PART3 / "decode_video.py"), str(cls.video), "--out-root", str(cls.out)],
                           capture_output=True, text=True, cwd=PART3)
        cls.cli = r
        cls.res_path = cls.out / "synth_p3_da050_d040" / "results.json"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_cli_decodes_with_zero_ber(self):
        self.assertEqual(self.cli.returncode, 0, self.cli.stderr[-2000:])
        res = json.loads(self.res_path.read_text())
        self.assertTrue(res["synthetic"])
        self.assertEqual(res["txlog"]["path"].endswith("_txlog.json"), True)
        self.assertEqual(res["overall"]["ber"], 0.0, res["segments"])
        self.assertEqual([s["rate_bps"] for s in res["segments"]], [15.0, 30.0])
        self.assertTrue(res["pilot_tones"]["ok"], res["pilot_tones"])
        self.assertEqual(res["trace"]["timestamps"]["mode"], "pts")
        self.assertEqual(res["trace"]["n_frames"], self.render["frames"])
        # the file must carry the simulated capture times (jitter, dropped slots)
        z = np.load(self.out / "synth_p3_da050_d040" / "trace.npz")
        truth = cm.capture_times(sim.condition_camera(40, cm.CameraModel()), self.render["duration_s"])
        self.assertEqual(z["t_pts"].size, truth.size)
        self.assertLess(np.abs(z["t_pts"] - truth).max(), 2e-5)
        for f in ("trace.png", "symbols.png", "spectrogram.png", "ber_datarate_vs_rate.png", "roi.png", "trace.npz"):
            self.assertTrue((self.out / "synth_p3_da050_d040" / f).exists(), f)

    def test_manual_roi_and_reuse(self):
        x0, y0 = self.render["screen_origin"]
        w, h = self.render["screen_px"]
        sx = 1.0  # synthetic video pixels are the analysis pixels (384 wide)
        roi = f"{int((x0 + 0.2 * w) * sx)},{int((y0 + 0.2 * h) * sx)},{int(0.6 * w)},{int(0.6 * h)}"
        out2 = self.root / "results_manual"
        r = subprocess.run([PY, str(PART3 / "decode_video.py"), str(self.video), "--out-root", str(out2),
                            "--roi", roi], capture_output=True, text=True, cwd=PART3)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        res = json.loads((out2 / "synth_p3_da050_d040" / "results.json").read_text())
        self.assertEqual(res["trace"]["roi"]["mode"], "manual")
        self.assertEqual(res["overall"]["ber"], 0.0)
        r2 = subprocess.run([PY, str(PART3 / "decode_video.py"), str(self.video), "--out-root", str(out2),
                             "--reuse-trace"], capture_output=True, text=True, cwd=PART3)
        self.assertEqual(r2.returncode, 0, r2.stderr[-2000:])

    def test_summarize_cli(self):
        figs = self.root / "figs"
        r = subprocess.run([PY, str(PART3 / "summarize.py"), "--results-root", str(self.out), "--out", str(figs),
                            "--synthetic"], capture_output=True, text=True, cwd=PART3)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        for f in ("ber_vs_dalpha_by_distance.png", "datarate_vs_dalpha_by_distance.png",
                  "ber_vs_rate_by_condition.png", "max_reliable_rate_vs_dalpha.png", "summary.csv", "summary.json"):
            self.assertTrue((figs / f).exists(), f)
        s = json.loads((figs / "summary.json").read_text())
        self.assertTrue(s["synthetic"])
        # real-data mode must not pick up synthetic results
        r2 = subprocess.run([PY, str(PART3 / "summarize.py"), "--results-root", str(self.out), "--out",
                             str(self.root / "figs_real")], capture_output=True, text=True, cwd=PART3)
        self.assertNotEqual(r2.returncode, 0)

    def test_low_fps_file_is_rejected(self):
        clip = self.root / "data" / "synth_p3_da050_d040_30fps.mov"
        subprocess.run(["/opt/homebrew/bin/ffmpeg", "-y", "-loglevel", "error", "-i", str(self.video), "-r", "30",
                        "-c:v", "libx264", str(clip)], check=True)
        r = subprocess.run([PY, str(PART3 / "decode_video.py"), str(clip), "--out-root", str(self.root / "r30"),
                            "--txlog", str(self.root / "data" / "synth_p3_da050_d040_txlog.json")],
                           capture_output=True, text=True, cwd=PART3)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("All Photos Data", r.stdout + r.stderr)


class Timestamps(unittest.TestCase):
    def test_coarse_iphone_ticks_are_regularized(self):
        true_t = 1.234 + np.arange(2400) / 240.0
        true_t = np.delete(true_t, [500, 1500])            # two genuinely dropped frames
        pts = np.round(true_t * 600) / 600                  # 1/600 s container ticks
        t, meta = vio.regularize_timestamps(pts, 1 / 600)
        self.assertEqual(meta["mode"], "regularized")
        self.assertEqual(meta["gaps_over_1p5_frames"], 2)
        err = t - true_t
        self.assertLess(np.abs(err - err.mean()).max(), 1e-5)   # a constant offset is harmless
        self.assertLess(abs(err.mean()), 1 / 1200)
        self.assertAlmostEqual(meta["fps_measured"], 240.0, places=3)

    def test_fine_pts_kept(self):
        cam = replace(cm.CameraModel(), jitter_s=0.0003)
        t_true = cm.capture_times(cam, 5.0)
        t, meta = vio.regularize_timestamps(t_true, 1 / 240000)
        self.assertEqual(meta["mode"], "pts")
        self.assertTrue(np.allclose(t, t_true))


if __name__ == "__main__":
    unittest.main()
