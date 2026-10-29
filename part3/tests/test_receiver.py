"""Signal-level synthetic checks of the receiver (camera_model.py; code validation only)."""
import sys
import unittest
from dataclasses import replace
from pathlib import Path

PART3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART3))

import camera_model as cm  # noqa: E402
import hilight_plan as hp  # noqa: E402
import simulate as sim  # noqa: E402


def bers(sc):
    return {s["rate_bps"]: s["ber"] for s in sc["segments"]}


class Receiver(unittest.TestCase):
    def test_high_snr_zero_ber_all_rates(self):
        for da in (0.1, 0.5):
            for d in (20, 120):
                _, dec, sc = sim.run_signal_case({"dalpha": da}, distance_cm=d, seed=3)
                with self.subTest(dalpha=da, distance=d):
                    self.assertIsNotNone(dec["pilot"])
                    self.assertEqual(sc["overall"]["ber"], 0.0, bers(sc))
                    self.assertTrue(all(s["synced"] == s["packets"] for s in sc["segments"]))

    def test_streaming_equals_batch(self):
        params = {"sweepN": "8,4", "packetsPerRate": 1, "payloadBits": 40, "dalpha": 0.1}
        _, d1, _ = sim.run_signal_case(params, noise_scale=150, chunk_s=1 / 240, seed=5)
        _, d2, _ = sim.run_signal_case(params, noise_scale=150, chunk_s=7.0, seed=5)
        self.assertEqual([p["bits"] for p in d1["packets"]], [p["bits"] for p in d2["packets"]])
        self.assertEqual([p["tau"] for p in d1["packets"]], [p["tau"] for p in d2["packets"]])

    def test_low_snr_degrades(self):
        res = []
        for ns in (1, 200, 800):
            _, _, sc = sim.run_signal_case({"dalpha": 0.1}, distance_cm=120, noise_scale=ns, seed=2)
            res.append(sc["overall"]["ber"])
        self.assertEqual(res[0], 0.0)
        self.assertGreater(res[1], 0.02)
        self.assertGreater(res[2], res[1])
        self.assertGreater(res[2], 0.25)

    def test_above_camera_nyquist_fails(self):
        cam = replace(cm.CameraModel(), fps=60.0, exposure_s=1 / 120)
        _, _, sc = sim.run_signal_case({"dalpha": 0.5}, camera=cam, seed=1)
        self.assertGreater(sc["overall"]["ber"], 0.3)

    def test_cam120_profile_at_120fps(self):
        cam = replace(cm.CameraModel(), fps=120.0, exposure_s=1 / 250)
        _, _, sc = sim.run_signal_case({"profile": "cam120", "dalpha": 0.1}, camera=cam, seed=4)
        self.assertEqual(sc["overall"]["ber"], 0.0, bers(sc))

    def test_slow_lcd_and_rolling_shutter(self):
        disp = replace(cm.DisplayModel(), tau_s=0.010, drop_prob=0.0)
        cam = replace(cm.CameraModel(), readout_s=0.008, jitter_s=0.0005)
        _, _, sc = sim.run_signal_case({"dalpha": 0.1}, display=disp, camera=cam, distance_cm=80, seed=6)
        self.assertEqual(sc["overall"]["ber"], 0.0, bers(sc))

    def test_transmitter_frame_drops_cost_few_bits(self):
        """A dropped browser frame corrupts at most the symbol it falls in;
        with a slow LCD (weak 60 Hz tone) that symbol can flip."""
        disp = replace(cm.DisplayModel(), tau_s=0.010, drop_prob=0.002)
        _, _, sc = sim.run_signal_case({"dalpha": 0.1}, display=disp, distance_cm=80, seed=6)
        self.assertLess(sc["overall"]["ber"], 0.02, bers(sc))
        self.assertTrue(all(s["synced"] == s["packets"] for s in sc["segments"]))

    def test_text_mode_without_knowing_words(self):
        sent = "HELLO VITAL VISIONS 17720"
        _, dec, _ = sim.run_signal_case(
            {"mode": "text", "N": 8, "text": sent, "repeats": 2}, distance_cm=80, open_ended=True,
            decode_params={"mode": "text", "N": 8, "text": "X", "repeats": 1}, seed=8)
        words = [hp.decode_text_bits(p["bits"], 10) for p in dec["packets"]]
        self.assertEqual([w["text"] for w in words], sent.split() * 2)
        self.assertTrue(all(w["crc_ok"] for w in words))


if __name__ == "__main__":
    unittest.main()
