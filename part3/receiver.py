"""Streaming BFSK receiver for the alpha-channel link (HiLight-style).

Input: ROI luminance samples (t, value) from the camera, in time order, in
chunks of any size. Every step is causal, so the same code can run on live
camera frames; decode_video.py feeds a whole recording through it.

1. FrontEnd: linear interpolation onto a uniform grid (fs = 4x camera fps),
   relative change against a causal 0.5 s moving mean (cancels auto-exposure
   drift), 2nd-order Butterworth high-pass (HiLight drops < 15 Hz).
2. Pilot search: the transmission starts with 1 s of f0 then 1 s of f1.
   Score = tone-power fraction at the right tone minus at the wrong one.
3. Per packet: the 13-symbol Barker preamble is correlated against the soft
   symbol values s = (E1 - E0) / (E1 + E0) over a window around the expected
   start, which is predicted from the transmitter schedule (in display frames)
   and the last sync point. A weak peak falls back to the predicted time.
4. Per symbol: non-coherent energy at f0 and f1 over the symbol window
   (projection onto exp(-j 2 pi f t), computed from running cumulative sums);
   bit = 1 if E1 > E0. Each symbol is decided as soon as its window is complete.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, lfilter

import hilight_plan as hp

BARKER_PM = 2 * np.asarray(hp.BARKER13) - 1


class FrontEnd:
    def __init__(self, fs: float, hp_hz: float, ema_s: float = 0.5):
        self.fs = fs
        self.b, self.a = butter(2, hp_hz / (fs / 2), "high")
        self.zi = np.zeros(2)
        self.ema_a = 1 - np.exp(-1 / (fs * ema_s))
        self.ema_zi = None
        self.t0 = None
        self.n_next = 0
        self.prev = None

    def push(self, t, v):
        t = np.asarray(t, float)
        v = np.asarray(v, float)
        if t.size == 0:
            return np.empty(0), np.empty(0)
        if self.t0 is None:
            self.t0 = t[0]
        if self.prev is not None:
            t = np.concatenate([[self.prev[0]], t])
            v = np.concatenate([[self.prev[1]], v])
        self.prev = (t[-1], v[-1])
        n_last = int(np.floor((t[-1] - self.t0) * self.fs + 1e-9))
        if n_last < self.n_next:
            return np.empty(0), np.empty(0)
        n = np.arange(self.n_next, n_last + 1)
        self.n_next = n_last + 1
        tg = self.t0 + n / self.fs
        vg = np.interp(tg, t, v)
        if self.ema_zi is None:
            self.ema_zi = np.array([(1 - self.ema_a) * vg[0]])
        m, self.ema_zi = lfilter([self.ema_a], [1, -(1 - self.ema_a)], vg, zi=self.ema_zi)
        r = vg / np.maximum(m, 1e-9) - 1.0
        x, self.zi = lfilter(self.b, self.a, r, zi=self.zi)
        return tg, x


def spec_from_plan(plan: dict, refresh_hz: float, open_ended: bool = False) -> dict:
    """Timing skeleton the receiver may know: no payload bits, only structure."""
    p = plan["params"]
    pilot = next(b for b in plan["blocks"] if b.get("label") == "pilot0")
    pk = [b for b in plan["blocks"] if b["type"] == "packet"]
    packets = [{"start": b["startFrame"], "N": b["N"], "nbits": len(b["bits"]),
                "payload_start": b["payloadStart"]} for b in pk]
    period = (pk[1]["startFrame"] - pk[0]["startFrame"]) if len(pk) > 1 else pk[0]["frames"] + p["gapFrames"]
    return {"refresh_hz": float(refresh_hz), "p0": p["p0"], "p1": p["p1"],
            "pilot_start": pilot["startFrame"], "pilot_frames": p["pilotFrames"],
            "packets": packets, "open_ended": open_ended, "period_frames": period}


class _Grow:
    def __init__(self, dtype):
        self.buf = np.zeros(1024, dtype)
        self.n = 1  # buf[0] = 0 (cumulative sums start at zero)

    def append_cumsum(self, z):
        need = self.n + z.size
        if need > self.buf.size:
            nb = np.zeros(max(need, 2 * self.buf.size), self.buf.dtype)
            nb[: self.n] = self.buf[: self.n]
            self.buf = nb
        self.buf[self.n:need] = self.buf[self.n - 1] + np.cumsum(z)
        self.n = need

    @property
    def arr(self):
        return self.buf[: self.n]


class StreamingReceiver:
    def __init__(self, spec: dict, fs: float, pilot_thr: float = 0.2, sync_thr: float = 0.45,
                 search_s: float = 0.1):
        self.spec = spec
        self.fs = fs
        self.refresh = spec["refresh_hz"]
        self.f0 = self.refresh / spec["p0"]
        self.f1 = self.refresh / spec["p1"]
        self.w0 = -2j * np.pi * self.f0 / fs
        self.w1 = -2j * np.pi * self.f1 / fs
        self.c0 = _Grow(np.complex128)
        self.c1 = _Grow(np.complex128)
        self.cx2 = _Grow(np.float64)
        self.n = 0
        self.pilot_thr = pilot_thr
        self.sync_thr = sync_thr
        self.search_s = search_s
        self.state = "pilot"
        self.pilot_next = 0
        self.pilot_best = (-np.inf, None)
        self.pilot = None
        self.scale = 1.0
        self.anchor = None
        self.packets = []
        self.cur = None
        self.fails_in_row = 0
        self.sync_points = []
        self.packet_specs = list(spec["packets"])

    # ---------- signal bookkeeping ----------
    def feed(self, x):
        x = np.asarray(x, float)
        if x.size == 0:
            return
        n = np.arange(self.n, self.n + x.size)
        self.c0.append_cumsum(x * np.exp(self.w0 * n))
        self.c1.append_cumsum(x * np.exp(self.w1 * n))
        self.cx2.append_cumsum(x * x)
        self.n += x.size
        self._run()

    def _E(self, c, a, b):
        return np.abs(c.arr[b] - c.arr[a]) ** 2

    def _rho(self, c, a, b):
        d = (b - a) * (self.cx2.arr[b] - self.cx2.arr[a])
        return 2 * self._E(c, a, b) / np.maximum(d, 1e-30)

    @property
    def spf(self):
        return self.fs / self.refresh * self.scale

    # ---------- state machine ----------
    def _run(self):
        progressed = True
        while progressed:
            progressed = False
            if self.state == "pilot":
                progressed = self._pilot_step()
            elif self.state == "sync":
                progressed = self._sync_step()
            elif self.state == "data":
                progressed = self._data_step()

    def _pilot_step(self):
        Lp = int(round(self.spec["pilot_frames"] * self.spf))
        last = self.n - 1 - 2 * Lp
        if last < self.pilot_next:
            return False
        tau = np.arange(self.pilot_next, last + 1)
        a, m, b = tau, tau + Lp, tau + 2 * Lp
        score = (self._rho(self.c0, a, m) + self._rho(self.c1, m, b)
                 - self._rho(self.c1, a, m) - self._rho(self.c0, m, b))
        i = int(np.argmax(score))
        if score[i] > self.pilot_best[0]:
            self.pilot_best = (float(score[i]), int(tau[i]))
        self.pilot_next = last + 1
        best, bt = self.pilot_best
        if best >= self.pilot_thr and last >= bt + Lp // 2:
            self.pilot = {"tau": bt, "score": best}
            self.anchor = (float(bt), self.spec["pilot_start"])
            self.state = "sync"
            return True
        return False

    def _next_packet_spec(self):
        i = len(self.packets)
        if i < len(self.packet_specs):
            return self.packet_specs[i]
        if self.spec["open_ended"] and self.packet_specs:
            last = self.packet_specs[-1]
            nxt = dict(last, start=last["start"] + self.spec["period_frames"])
            self.packet_specs.append(nxt)
            return nxt
        return None

    def _sync_step(self):
        ps = self._next_packet_spec()
        if ps is None:
            self.state = "done"
            return False
        Ts = ps["N"] * self.spf
        tau_exp = self.anchor[0] + (ps["start"] - self.anchor[1]) * self.spf
        W = int(self.search_s * self.fs + 0.5 * Ts)
        need = int(np.ceil(tau_exp + W + len(BARKER_PM) * Ts)) + 2
        if self.n < need:
            return False
        tau = np.arange(int(round(tau_exp)) - W, int(round(tau_exp)) + W + 1)
        tau = tau[tau >= 0]
        corr = np.zeros(tau.size)
        for j, pj in enumerate(BARKER_PM):
            a = np.round(tau + j * Ts).astype(int)
            b = np.round(tau + (j + 1) * Ts).astype(int)
            e0, e1 = self._E(self.c0, a, b), self._E(self.c1, a, b)
            corr += pj * (e1 - e0) / (e1 + e0 + 1e-30)
        corr /= len(BARKER_PM)
        k = int(np.argmax(corr)) if corr.size else 0
        synced = bool(corr.size and corr[k] >= self.sync_thr)
        t_sync = float(tau[k]) if synced else float(tau_exp)
        if synced:
            self.fails_in_row = 0
            self._update_scale(t_sync, ps["start"])
        else:
            self.fails_in_row += 1
            if self.spec["open_ended"] and len(self.packets) >= len(self.spec["packets"]) and self.fails_in_row >= 2:
                self.packet_specs = self.packet_specs[: len(self.packets) - 1]
                self.packets = self.packets[:-1]
                self.state = "done"
                return False
        self.anchor = (t_sync, ps["start"])
        self.cur = {"index": len(self.packets), "start_frame": ps["start"], "N": ps["N"],
                    "nbits": ps["nbits"], "payload_start": ps["payload_start"], "tau": t_sync,
                    "Ts": Ts, "corr": float(corr[k]) if corr.size else 0.0, "synced": synced,
                    "bits": [], "soft": [], "e0": [], "e1": []}
        self.packets.append(self.cur)
        self.state = "data"
        return True

    def _update_scale(self, tau, start_frame):
        """Camera-vs-display clock ratio from a least-squares fit of preamble sync
        times against their scheduled display frames. The pilot is too coarse a
        time reference, so only packet syncs are used, and only once they span
        at least 1000 frames; ratios beyond +-300 ppm are rejected as outliers."""
        self.sync_points.append((tau, start_frame))
        if len(self.sync_points) < 2:
            return
        tt, ff = np.array(self.sync_points, float).T
        if ff.max() - ff.min() < 1000:
            return
        slope = np.polyfit(ff, tt, 1)[0] / (self.fs / self.refresh)
        if abs(slope - 1) <= 3e-4:
            self.scale = float(slope)

    def _data_step(self):
        c = self.cur
        j0 = c["payload_start"] + len(c["bits"])
        if j0 >= c["nbits"]:
            self.state = "sync"
            return True
        js = np.arange(j0, c["nbits"])
        a = np.round(c["tau"] + js * c["Ts"]).astype(int)
        b = np.round(c["tau"] + (js + 1) * c["Ts"]).astype(int)
        ok = b <= self.n - 1
        if not ok.any():
            return False
        a, b = a[ok], b[ok]
        e0, e1 = self._E(self.c0, a, b), self._E(self.c1, a, b)
        c["e0"] += e0.tolist()
        c["e1"] += e1.tolist()
        c["soft"] += ((e1 - e0) / (e1 + e0 + 1e-30)).tolist()
        c["bits"] += (e1 > e0).astype(int).tolist()
        return True

    def finish(self):
        """End of stream: packets still owed are reported with missing bits."""
        if self.state == "data" and len(self.cur["bits"]) < self.cur["nbits"] - self.cur["payload_start"]:
            self.cur["incomplete"] = True
        if self.spec["open_ended"]:
            # trailing searches past the last real packet are not packets
            while len(self.packets) > len(self.spec["packets"]) and not self.packets[-1]["synced"]:
                self.packets.pop()
        else:
            while len(self.packets) < len(self.packet_specs):
                ps = self.packet_specs[len(self.packets)]
                self.packets.append({"index": len(self.packets), "start_frame": ps["start"], "N": ps["N"],
                                     "nbits": ps["nbits"], "payload_start": ps["payload_start"], "tau": None,
                                     "Ts": ps["N"] * self.spf, "corr": 0.0, "synced": False, "missing": True,
                                     "bits": [], "soft": [], "e0": [], "e1": []})
        self.state = "done"
        return self.packets


def decode_trace(t, v, plan: dict, refresh_hz: float, camera_fps: float, chunk_s: float = 0.25,
                 open_ended: bool = False, hp_hz: float | None = None) -> dict:
    """Run FrontEnd + StreamingReceiver over a whole trace in chunks of chunk_s seconds."""
    fs = 4 * camera_fps
    spec = spec_from_plan(plan, refresh_hz, open_ended=open_ended)
    f_low = min(refresh_hz / spec["p0"], refresh_hz / spec["p1"])
    fe = FrontEnd(fs, hp_hz or 0.4 * f_low)
    rx = StreamingReceiver(spec, fs)
    t = np.asarray(t, float)
    v = np.asarray(v, float)
    step = max(1, int(round(chunk_s * camera_fps)))
    grid_t, grid_x = [], []
    for i in range(0, t.size, step):
        tg, x = fe.push(t[i:i + step], v[i:i + step])
        grid_t.append(tg)
        grid_x.append(x)
        rx.feed(x)
    packets = rx.finish()
    tg = np.concatenate(grid_t)
    t0 = fe.t0
    for p in packets:
        p["t_start"] = None if p["tau"] is None else float(t0 + p["tau"] / fs)
    return {"fs": fs, "t0": t0, "grid_t": tg, "x": np.concatenate(grid_x), "packets": packets,
            "pilot": None if rx.pilot is None else {"t": float(t0 + rx.pilot["tau"] / fs),
                                                   "score": rx.pilot["score"]},
            "time_scale": rx.scale, "spec": spec, "f0": rx.f0, "f1": rx.f1}


def score(plan: dict, dec: dict, refresh_hz: float, nominal_hz: float | None = None) -> dict:
    """BER and data rate per symbol-rate segment, by the assignment's definitions.

    BER = 1 - correct / transmitted; data rate = correct bits per second of
    payload airtime (also reported including preamble and gap overhead).
    Bits of packets never decoded count as not received correctly.
    Airtime uses the actual refresh; rate labels use the nominal refresh
    (e.g. 120 Hz) so recordings group by the same symbol-rate keys.
    """
    p = plan["params"]
    nominal_hz = nominal_hz or round(refresh_hz)
    tx_packets = [b for b in plan["blocks"] if b["type"] == "packet"]
    segs = {}
    for b, d in zip(tx_packets, dec["packets"] + [None] * (len(tx_packets) - len(dec["packets"]))):
        truth = np.asarray(b["bits"][b["payloadStart"]:])
        got = np.asarray(d["bits"] if d else [], int)
        n_ok = int((got[: truth.size] == truth[: got.size]).sum()) if got.size else 0
        s = segs.setdefault(b["segment"], {"segment": b["segment"], "N": b["N"],
                                           "rate_bps": nominal_hz / b["N"],
                                           "rate_bps_actual": refresh_hz / b["N"], "packets": 0,
                                           "payload_bits": 0, "correct_bits": 0, "synced": 0,
                                           "decoded_bits": 0, "airtime_s": 0.0, "segment_time_s": 0.0,
                                           "e_on": [], "e_off": [], "corr": []})
        s["packets"] += 1
        s["payload_bits"] += truth.size
        s["correct_bits"] += n_ok
        s["decoded_bits"] += int(min(got.size, truth.size))
        s["synced"] += int(bool(d and d["synced"]))
        s["corr"].append(float(d["corr"]) if d else 0.0)
        s["airtime_s"] += truth.size * b["N"] / refresh_hz
        s["segment_time_s"] += (b["frames"] + p["gapFrames"]) / refresh_hz
        if d and d["e0"]:
            e0 = np.asarray(d["e0"])
            e1 = np.asarray(d["e1"])
            tb = truth[: e0.size]
            s["e_on"] += np.where(tb == 1, e1, e0).tolist()
            s["e_off"] += np.where(tb == 1, e0, e1).tolist()
    out = []
    for k in sorted(segs):
        s = segs[k]
        e_on, e_off = np.asarray(s.pop("e_on")), np.asarray(s.pop("e_off"))
        corr = s.pop("corr")
        s["bit_errors"] = s["payload_bits"] - s["correct_bits"]
        s["ber"] = 1 - s["correct_bits"] / s["payload_bits"]
        s["data_rate_bps"] = s["correct_bits"] / s["airtime_s"]
        s["refresh_hz"] = refresh_hz
        s["data_rate_incl_overhead_bps"] = s["correct_bits"] / s["segment_time_s"]
        s["f0_hz"] = refresh_hz / p["p0"]
        s["f1_hz"] = refresh_hz / p["p1"]
        s["min_preamble_corr"] = float(min(corr))
        s["tone_snr_db"] = (float(10 * np.log10(np.median(e_on) / np.median(e_off)))
                            if e_on.size and np.median(e_off) > 0 else None)
        out.append(s)
    tot_bits = sum(s["payload_bits"] for s in out)
    tot_ok = sum(s["correct_bits"] for s in out)
    return {"segments": out, "overall": {"payload_bits": tot_bits, "correct_bits": tot_ok,
                                         "ber": 1 - tot_ok / tot_bits if tot_bits else None}}
