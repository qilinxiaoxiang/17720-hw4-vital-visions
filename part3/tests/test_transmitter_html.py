"""transmitter.html loads in headless Chrome without errors, its inlined code is
current, and its schedule (static and the live requestAnimationFrame loop)
matches the Python ground truth."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlencode

PART3 = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PART3))

import build_transmitter  # noqa: E402
import hilight_plan as hp  # noqa: E402

HTML = PART3 / "transmitter.html"


def fnv(s: str) -> str:
    h = 0x811C9DC5
    for ch in s:
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h:08x}"


NODE = os.environ.get("NODE", "/opt/homebrew/opt/node@20/bin/node")


USER_KEYS = """
window.__key = (k) => document.dispatchEvent(new KeyboardEvent('keydown', {key: k, bubbles: true}));
window.__sleep = (ms) => new Promise((r) => setTimeout(r, ms));
window.__visible = (id) => !document.getElementById(id).classList.contains('hidden');
"""

# Clicks Start, waits on the preview screen, presses Space, waits for DONE.
DRIVE_TRANSMIT = USER_KEYS + """
(async () => {
  document.getElementById('start').click();
  await __sleep(500);
  const previewShown = document.getElementById('countdown').textContent.includes('press SPACE');
  __key(' ');
  for (let i = 0; i < 400 && !__visible('done'); i++) await __sleep(200);
  document.getElementById('selftest').textContent = JSON.stringify({previewShown,
    title: document.getElementById('doneTitle').textContent, stats: document.getElementById('doneStats').textContent});
  document.body.dataset.selftest = 'driven';
})();
"""

# Runs the eye check, answering Y for every trial.
DRIVE_EYE = USER_KEYS + """
(async () => {
  document.getElementById('start').click();
  let answered = 0;
  for (let i = 0; i < 1200 && !__visible('done'); i++) {
    await __sleep(150);
    if (!__visible('eyeprompt')) continue;
    const body = document.getElementById('eyeBody').textContent;
    if (body.includes('Press')) __key(' ');
    else if (body.includes('Did you')) { __key('y'); answered++; }
  }
  document.getElementById('selftest').textContent = JSON.stringify({answered,
    title: document.getElementById('doneTitle').textContent, stats: document.getElementById('doneStats').textContent});
  document.body.dataset.selftest = 'driven';
})();
"""


def run_page(query: dict, timeout_ms: int = 30000, driver: str | None = None):
    """Load the page in headless Chrome over CDP; returns (selftest state, data, page errors)."""
    url = HTML.as_uri() + "?" + urlencode(query)
    cmd = [NODE, str(Path(__file__).parent / "run_page_cdp.js"), url, str(timeout_ms)]
    if driver:
        cmd.append(driver)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_ms / 1000 + 60)
    out = json.loads(r.stdout)
    data = json.loads(out["pre"]) if out.get("pre") else None
    errors = list(out.get("consoleErrors") or [])
    if out.get("error"):
        errors.append(out["error"])
    if out.get("fatal"):
        errors.append(out["fatal"])
    return out.get("selftest"), data, errors


class TransmitterHtml(unittest.TestCase):
    def test_inlined_code_is_current(self):
        self.assertTrue(build_transmitter.is_current(HTML.read_text()),
                        "run build_transmitter.py: inlined schedule.js / background.png are stale")

    def test_static_schedule_matches_python(self):
        cases = [{}, {"dalpha": "0.5", "seed": "42"}, {"profile": "cam120"},
                 {"mode": "text", "N": "8", "text": "HELLO VITAL VISIONS"},
                 {"mode": "prbs", "N": "4", "packets": "3", "payloadBits": "100"}]
        for q in cases:
            state, data, errs = run_page(dict(q, selftest="1"))
            with self.subTest(q=q):
                self.assertEqual(state, "ok")
                self.assertEqual(errs, [])
                params = {k: v for k, v in q.items()}
                params.setdefault("text", "HELLO VITAL VISIONS")
                plan = hp.build_plan(params)
                self.assertEqual(data["totalFrames"], plan["totalFrames"])
                self.assertEqual(data["levelsHash"], fnv(hp.level_string(plan)))
                bits = "|".join("".join(map(str, b["bits"])) for b in plan["blocks"] if b["type"] == "packet")
                self.assertEqual(data["bitsHash"], fnv(bits))
                self.assertTrue(data["backgroundLoaded"])

    def test_live_raf_loop_matches_python(self):
        q = {"selftest": "run", "mode": "sweep", "sweepN": "8,4", "packetsPerRate": "1", "payloadBits": "16",
             "dalpha": "0.5"}
        state, data, errs = run_page(q, timeout_ms=60000)
        self.assertEqual(state, "ran", "transmission loop did not finish")
        self.assertEqual(errs, [])
        self.assertFalse(data["aborted"])
        plan = hp.build_plan({"mode": "sweep", "sweepN": "8,4", "packetsPerRate": 1, "payloadBits": 16,
                              "dalpha": 0.5})
        lv = hp.levels(plan)
        k = data["frames"]["k"]
        self.assertGreater(len(k), 100)
        self.assertTrue(all(b > a for a, b in zip(k, k[1:])))
        self.assertEqual(data["frames"]["level"], [float(lv[i]) for i in k])
        self.assertLess(k[-1], plan["totalFrames"])
        self.assertGreaterEqual(k[-1], plan["totalFrames"] - 3)

    def test_user_flow_preview_space_done(self):
        q = {"mode": "prbs", "N": "4", "packets": "1", "payloadBits": "16", "countdownSec": "1"}
        state, data, errs = run_page(q, timeout_ms=90000, driver=DRIVE_TRANSMIT)
        self.assertEqual(state, "driven")
        self.assertEqual(errs, [])
        self.assertTrue(data["previewShown"])
        self.assertEqual(data["title"], "DONE")

    def test_user_flow_eye_check(self):
        q = {"mode": "eyecheck", "trialSec": "2"}
        state, data, errs = run_page(q, timeout_ms=150000, driver=DRIVE_EYE)
        self.assertEqual(state, "driven")
        self.assertEqual(errs, [])
        self.assertEqual(data["title"], "Eye check DONE")
        self.assertEqual(data["answered"], 22)
        self.assertEqual(data["stats"].count("yes"), 22)


if __name__ == "__main__":
    unittest.main()
