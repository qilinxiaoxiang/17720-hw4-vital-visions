"""Ground-truth BFSK schedule, a line-for-line mirror of schedule.js.

The transmitter (transmitter.html, which inlines schedule.js) and this module
build the same plan from the same parameters, so the decoder can regenerate
the transmitted bits and the per-frame alpha levels without the transmitter
log. tests/test_schedule_parity.py checks that both implementations agree.

All durations are in display frames. Levels: 0 -> alpha = base,
1 -> alpha = base + dalpha, 0.5 -> idle (alpha = base + dalpha / 2).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

BARKER13 = [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 1, 0, 1]

PROFILES = {
    "cam240": {"p0": 4, "p1": 2, "sweepN": [24, 16, 12, 8, 4], "N": 12},
    "cam120": {"p0": 8, "p1": 4, "sweepN": [24, 16, 8], "N": 16},
}

DEFAULTS = {
    "mode": "sweep",
    "profile": "cam240",
    "dalpha": 0.1,
    "base": 0.0,
    "p0": None,
    "p1": None,
    "N": None,
    "sweepN": None,
    "seed": 1720,
    "packetsPerRate": 2,
    "packets": 4,
    "payloadBits": 64,
    "text": "HELLO VITAL VISIONS",
    "textChars": 10,
    "repeats": 2,
    "leadFrames": 120,
    "pilotFrames": 120,
    "gapFrames": 60,
}

INT_KEYS = ["seed", "packetsPerRate", "packets", "payloadBits", "textChars", "repeats",
            "leadFrames", "pilotFrames", "gapFrames"]


def _to_int(v) -> int:
    n = float(v)
    if not np.isfinite(n) or int(n) != n:
        raise ValueError(f"expected integer, got {v}")
    return int(n)


def normalize_params(p: dict | None = None) -> dict:
    out = dict(DEFAULTS)
    for k, v in (p or {}).items():
        if v is not None and v != "":
            out[k] = v
    prof = PROFILES.get(out["profile"])
    if prof is None:
        raise ValueError(f"unknown profile {out['profile']}")
    out["p0"] = _to_int(prof["p0"] if out["p0"] is None else out["p0"])
    out["p1"] = _to_int(prof["p1"] if out["p1"] is None else out["p1"])
    out["N"] = _to_int(prof["N"] if out["N"] is None else out["N"])
    if out["sweepN"] is None:
        out["sweepN"] = list(prof["sweepN"])
    if isinstance(out["sweepN"], str):
        out["sweepN"] = out["sweepN"].split(",")
    out["sweepN"] = [_to_int(n) for n in out["sweepN"]]
    for k in INT_KEYS:
        out[k] = _to_int(out[k])
    out["dalpha"] = float(out["dalpha"])
    out["base"] = float(out["base"])
    out["text"] = str(out["text"])
    if out["p0"] == out["p1"]:
        raise ValueError("p0 and p1 must differ")
    if out["p0"] < 2 or out["p1"] < 2:
        raise ValueError("tone periods must be >= 2 frames")
    m = np.lcm(out["p0"], out["p1"])
    for n in (out["sweepN"] if out["mode"] == "sweep" else [out["N"]]):
        if n % m:
            raise ValueError(f"symbol length {n} frames is not a multiple of lcm(p0,p1)={m}")
    if out["base"] < 0 or out["base"] + out["dalpha"] > 1 or out["dalpha"] <= 0:
        raise ValueError("need 0 <= base, 0 < dalpha, base+dalpha <= 1")
    return out


class Prbs:
    """xorshift32; output bit is the top bit of the state after each step."""

    def __init__(self, seed: int):
        self.s = (int(seed) & 0xFFFFFFFF) or 0x9E3779B9

    def next(self) -> int:
        s = self.s
        s ^= (s << 13) & 0xFFFFFFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFFFFFF
        self.s = s
        return s >> 31

    def take(self, n: int) -> list[int]:
        return [self.next() for _ in range(n)]


def crc8(data) -> int:
    c = 0
    for b in data:
        c ^= b
        for _ in range(8):
            c = ((c << 1) ^ 0x07) & 0xFF if c & 0x80 else (c << 1) & 0xFF
    return c


def byte_bits(b: int) -> list[int]:
    return [(b >> i) & 1 for i in range(7, -1, -1)]


def encode_text_chunk(chunk: str, text_chars: int) -> list[int]:
    data = [len(chunk)]
    for i in range(text_chars):
        c = ord(chunk[i]) if i < len(chunk) else 32
        if c > 126 or c < 32:
            c = 63
        data.append(c)
    data.append(crc8(data))
    bits: list[int] = []
    for b in data:
        bits += byte_bits(b)
    return bits


def decode_text_bits(bits, text_chars: int) -> dict:
    """Inverse of encode_text_chunk; works on decoded (possibly wrong) bits."""
    bits = list(bits)
    nbytes = text_chars + 2
    if len(bits) < 8 * nbytes:
        bits = bits + [0] * (8 * nbytes - len(bits))
    data = [int("".join(str(int(b)) for b in bits[8 * i:8 * i + 8]), 2) for i in range(nbytes)]
    length = min(data[0], text_chars)
    text = "".join(chr(c) if 32 <= c <= 126 else "?" for c in data[1:1 + length])
    return {"text": text, "length_byte": data[0], "crc_ok": crc8(data[:-1]) == data[-1]}


def text_chunks(text: str, text_chars: int) -> list[str]:
    words = [w for w in re.split(r"\s+", str(text).strip()) if w]
    chunks = []
    for w in words:
        for i in range(0, len(w), text_chars):
            chunks.append(w[i:i + text_chars])
    return chunks


def build_plan(params: dict | None = None) -> dict:
    p = normalize_params(params)
    blocks: list[dict] = []
    cursor = 0

    def add(b):
        nonlocal cursor
        b["startFrame"] = cursor
        blocks.append(b)
        cursor += b["frames"]

    def packet(N, bits, extra):
        b = {"type": "packet", "N": N, "frames": N * len(bits), "bits": bits,
             "payloadStart": len(BARKER13)}
        b.update(extra)
        add(b)
        add({"type": "idle", "frames": p["gapFrames"]})

    add({"type": "idle", "frames": p["leadFrames"], "label": "lead"})
    add({"type": "tone", "period": p["p0"], "frames": p["pilotFrames"], "label": "pilot0"})
    add({"type": "tone", "period": p["p1"], "frames": p["pilotFrames"], "label": "pilot1"})
    add({"type": "idle", "frames": p["gapFrames"]})

    prbs = Prbs(p["seed"])
    if p["mode"] == "sweep":
        for si, N in enumerate(p["sweepN"]):
            for k in range(p["packetsPerRate"]):
                packet(N, BARKER13 + prbs.take(p["payloadBits"]), {"segment": si, "packet": k})
    elif p["mode"] == "prbs":
        for k in range(p["packets"]):
            packet(p["N"], BARKER13 + prbs.take(p["payloadBits"]), {"segment": 0, "packet": k})
    elif p["mode"] == "text":
        idx = 0
        chunks = text_chunks(p["text"], p["textChars"])
        for _ in range(p["repeats"]):
            for c in chunks:
                packet(p["N"], BARKER13 + encode_text_chunk(c, p["textChars"]),
                       {"segment": 0, "packet": idx, "text": c})
                idx += 1
    else:
        raise ValueError(f"unknown mode {p['mode']}")
    add({"type": "idle", "frames": p["gapFrames"], "label": "tail"})
    return {"params": p, "blocks": blocks, "totalFrames": cursor}


def levels(plan: dict) -> np.ndarray:
    """Per-display-frame level array (float: 0, 1 or 0.5)."""
    out = np.full(plan["totalFrames"], 0.5)
    p0, p1 = plan["params"]["p0"], plan["params"]["p1"]
    for b in plan["blocks"]:
        s, n = b["startFrame"], b["frames"]
        f = np.arange(n)
        if b["type"] == "tone":
            out[s:s + n] = ((f % b["period"]) < b["period"] / 2).astype(float)
        elif b["type"] == "packet":
            N = b["N"]
            per = np.where(np.repeat(np.asarray(b["bits"]), N) == 1, p1, p0)
            local = f - (f // N) * N
            out[s:s + n] = ((local % per) < per / 2).astype(float)
    return out


def level_string(plan: dict) -> str:
    return "".join("m" if v == 0.5 else str(int(v)) for v in levels(plan))


def alpha_of(plan: dict, lv):
    return plan["params"]["base"] + plan["params"]["dalpha"] * np.asarray(lv)


def summary(plan: dict, refresh_hz: float = 120.0) -> dict:
    p = plan["params"]
    Ns = p["sweepN"] if p["mode"] == "sweep" else [p["N"]]
    return {
        "f0": refresh_hz / p["p0"],
        "f1": refresh_hz / p["p1"],
        "ratesBps": [refresh_hz / n for n in Ns],
        "seconds": plan["totalFrames"] / refresh_hz,
        "packets": sum(1 for b in plan["blocks"] if b["type"] == "packet"),
    }


@dataclass
class RecordingName:
    dalpha: float | None
    distance_cm: int | None


NAME_RE = re.compile(r"p3_da(\d{3})_d(\d{3})")


def parse_recording_name(name: str) -> RecordingName:
    """p3_da010_d020 -> dalpha 0.10, distance 20 cm. Unknown names give None."""
    m = NAME_RE.search(Path(name).stem)
    if not m:
        return RecordingName(None, None)
    return RecordingName(int(m.group(1)) / 100.0, int(m.group(2)))


def load_txlog(path: str | Path) -> dict:
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Print the plan summary for given parameters.")
    ap.add_argument("--mode", default="sweep")
    ap.add_argument("--profile", default="cam240")
    ap.add_argument("--refresh", type=float, default=120.0)
    a = ap.parse_args()
    pl = build_plan({"mode": a.mode, "profile": a.profile})
    print(json.dumps(summary(pl, a.refresh), indent=1))
