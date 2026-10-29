"""The transmitter's JS schedule (schedule.js) must equal the Python ground truth."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PART3 = HERE.parent
sys.path.insert(0, str(PART3))

import hilight_plan as hp  # noqa: E402

NODE = os.environ.get("NODE", "/opt/homebrew/opt/node@20/bin/node")

CASES = [
    {},
    {"dalpha": 0.5},
    {"profile": "cam120", "dalpha": 0.5, "seed": 7},
    {"mode": "prbs", "N": 8, "packets": 3, "payloadBits": 100, "seed": 99},
    {"mode": "text", "N": 8, "text": "hi there  supercalifragilistic", "repeats": 1},
    {"mode": "text", "profile": "cam120", "N": 16, "text": "ALPHA beta", "textChars": 6},
    {"mode": "sweep", "sweepN": "8,4", "packetsPerRate": 1, "payloadBits": 20, "seed": 0},
    {"mode": "prbs", "p0": 6, "p1": 2, "N": 6, "base": 0.2, "dalpha": 0.3},
]


def js_dump(cases):
    r = subprocess.run([NODE, str(HERE / "dump_schedule.js"), json.dumps(cases)],
                       capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


class ScheduleParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = js_dump(CASES)

    def test_plans_identical(self):
        for params, js in zip(CASES, self.js):
            py = hp.build_plan(params)
            with self.subTest(params=params):
                self.assertEqual(json.loads(json.dumps(py)), js["plan"])

    def test_levels_identical(self):
        for params, js in zip(CASES, self.js):
            py = hp.build_plan(params)
            with self.subTest(params=params):
                self.assertEqual(hp.level_string(py), js["levels"])

    def test_summary_identical(self):
        for params, js in zip(CASES, self.js):
            py = hp.summary(hp.build_plan(params), 120)
            with self.subTest(params=params):
                for k in py:
                    a, b = py[k], js["summary"][k]
                    a, b = (a, b) if isinstance(a, list) else ([a], [b])
                    self.assertEqual(len(a), len(b))
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, places=9)

    def test_default_sweep_design(self):
        plan = hp.build_plan({})
        s = hp.summary(plan, 120)
        self.assertEqual((s["f0"], s["f1"]), (30.0, 60.0))
        self.assertEqual(s["ratesBps"], [5.0, 7.5, 10.0, 15.0, 30.0])
        self.assertLess(s["seconds"], 120)
        pk = [b for b in plan["blocks"] if b["type"] == "packet"]
        for si in range(5):
            payload = sum(len(b["bits"]) - b["payloadStart"] for b in pk if b["segment"] == si)
            self.assertGreaterEqual(payload, 100)

    def test_tone_means_constant_per_symbol(self):
        """Each symbol holds an integer number of tone periods, so the mean alpha
        is identical for bit 0, bit 1 and idle (no flicker at the symbol rate)."""
        plan = hp.build_plan({})
        lv = hp.levels(plan)
        for b in plan["blocks"]:
            if b["type"] != "packet":
                continue
            seg = lv[b["startFrame"]:b["startFrame"] + b["frames"]].reshape(-1, b["N"])
            self.assertTrue(abs(seg.mean(axis=1) - 0.5).max() < 1e-12)

    def test_text_roundtrip(self):
        bits = hp.encode_text_chunk("VITAL", 10)
        d = hp.decode_text_bits(bits, 10)
        self.assertEqual(d["text"], "VITAL")
        self.assertTrue(d["crc_ok"])
        bits[12] ^= 1
        self.assertFalse(hp.decode_text_bits(bits, 10)["crc_ok"])

    def test_recording_name(self):
        r = hp.parse_recording_name("/x/synth_p3_da050_d120.mov")
        self.assertEqual((r.dalpha, r.distance_cm), (0.5, 120))


if __name__ == "__main__":
    unittest.main()
