/*
 * HiLight-style BFSK schedule for the alpha-channel transmitter.
 *
 * Everything is counted in DISPLAY FRAMES (vsync periods), never in seconds,
 * so the schedule is exact at whatever refresh the display really runs.
 * Python mirror: hilight_plan.py (tests/test_schedule_parity.py checks they agree).
 *
 * Per-frame "level": 0 -> alpha = base, 1 -> alpha = base + dalpha,
 * 0.5 -> idle (alpha = base + dalpha/2, the mean of every tone).
 * A tone with period P frames is high for the first P/2 frames of each period
 * (phase restarts at each block/symbol start).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.HiLightSchedule = factory();
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var BARKER13 = [1, 1, 1, 1, 1, 0, 0, 1, 1, 0, 1, 0, 1];

  // Tone periods in display frames and the symbol lengths (frames) of the sweep.
  // At a 120 Hz display: cam240 -> f0 = 30 Hz, f1 = 60 Hz, 5/7.5/10/15/30 bps.
  //                      cam120 -> f0 = 15 Hz, f1 = 30 Hz, 5/7.5/15 bps.
  var PROFILES = {
    cam240: { p0: 4, p1: 2, sweepN: [24, 16, 12, 8, 4], N: 12 },
    cam120: { p0: 8, p1: 4, sweepN: [24, 16, 8], N: 16 }
  };

  var DEFAULTS = {
    mode: 'sweep',        // sweep | prbs | text
    profile: 'cam240',
    dalpha: 0.1,
    base: 0.0,
    p0: null,             // null -> profile value
    p1: null,
    N: null,              // symbol length in frames for prbs/text modes
    sweepN: null,         // list of symbol lengths for sweep mode
    seed: 1720,
    packetsPerRate: 2,    // sweep mode
    packets: 4,           // prbs mode
    payloadBits: 64,      // sweep / prbs payload bits per packet
    text: 'HELLO VITAL VISIONS',
    textChars: 10,        // fixed characters per text packet (pads with spaces)
    repeats: 2,
    leadFrames: 120,
    pilotFrames: 120,
    gapFrames: 60
  };

  function lcm(a, b) {
    function gcd(x, y) { return y ? gcd(y, x % y) : x; }
    return a / gcd(a, b) * b;
  }

  function normalizeParams(p) {
    var out = {};
    var k;
    for (k in DEFAULTS) out[k] = DEFAULTS[k];
    for (k in (p || {})) if (p[k] !== undefined && p[k] !== null && p[k] !== '') out[k] = p[k];
    var prof = PROFILES[out.profile];
    if (!prof) throw new Error('unknown profile ' + out.profile);
    out.p0 = toInt(out.p0 === null ? prof.p0 : out.p0);
    out.p1 = toInt(out.p1 === null ? prof.p1 : out.p1);
    out.N = toInt(out.N === null ? prof.N : out.N);
    if (out.sweepN === null) out.sweepN = prof.sweepN.slice();
    if (typeof out.sweepN === 'string') out.sweepN = out.sweepN.split(',').map(toInt);
    out.sweepN = out.sweepN.map(toInt);
    ['seed', 'packetsPerRate', 'packets', 'payloadBits', 'textChars', 'repeats',
      'leadFrames', 'pilotFrames', 'gapFrames'].forEach(function (key) { out[key] = toInt(out[key]); });
    out.dalpha = Number(out.dalpha);
    out.base = Number(out.base);
    out.text = String(out.text);
    if (out.p0 === out.p1) throw new Error('p0 and p1 must differ');
    if (out.p0 < 2 || out.p1 < 2) throw new Error('tone periods must be >= 2 frames');
    var m = lcm(out.p0, out.p1);
    var Ns = out.mode === 'sweep' ? out.sweepN : [out.N];
    Ns.forEach(function (n) {
      if (n % m !== 0) throw new Error('symbol length ' + n + ' frames is not a multiple of lcm(p0,p1)=' + m);
    });
    if (out.base < 0 || out.base + out.dalpha > 1 || out.dalpha <= 0) throw new Error('need 0 <= base, 0 < dalpha, base+dalpha <= 1');
    return out;
  }

  function toInt(v) {
    var n = Number(v);
    if (!isFinite(n) || Math.floor(n) !== n) throw new Error('expected integer, got ' + v);
    return n;
  }

  // xorshift32 PRBS; output bit = top bit of the state after each step.
  function Prbs(seed) {
    var s = (seed >>> 0) || 0x9E3779B9;
    this.next = function () {
      s ^= (s << 13); s >>>= 0;
      s ^= (s >>> 17);
      s ^= (s << 5); s >>>= 0;
      return s >>> 31;
    };
    this.take = function (n) {
      var b = [];
      for (var i = 0; i < n; i++) b.push(this.next());
      return b;
    };
  }

  function crc8(bytes) {
    var c = 0;
    for (var i = 0; i < bytes.length; i++) {
      c ^= bytes[i];
      for (var j = 0; j < 8; j++) c = (c & 0x80) ? (((c << 1) ^ 0x07) & 0xFF) : ((c << 1) & 0xFF);
    }
    return c;
  }

  function byteBits(b) {
    var out = [];
    for (var i = 7; i >= 0; i--) out.push((b >> i) & 1);
    return out;
  }

  // Text packet payload: [length byte][textChars ASCII bytes, space padded][CRC-8 over both]
  function encodeTextChunk(chunk, textChars) {
    var bytes = [chunk.length];
    for (var i = 0; i < textChars; i++) {
      var c = i < chunk.length ? chunk.charCodeAt(i) : 32;
      if (c > 126 || c < 32) c = 63; // '?'
      bytes.push(c);
    }
    bytes.push(crc8(bytes));
    var bits = [];
    bytes.forEach(function (b) { bits = bits.concat(byteBits(b)); });
    return bits;
  }

  function textChunks(text, textChars) {
    var words = String(text).trim().split(/\s+/).filter(function (w) { return w.length > 0; });
    var chunks = [];
    words.forEach(function (w) {
      for (var i = 0; i < w.length; i += textChars) chunks.push(w.slice(i, i + textChars));
    });
    return chunks;
  }

  function buildPlan(params) {
    var p = normalizeParams(params);
    var blocks = [];
    var cursor = 0;
    function add(b) { b.startFrame = cursor; blocks.push(b); cursor += b.frames; }
    function packet(N, bits, extra) {
      var b = { type: 'packet', N: N, frames: N * bits.length, bits: bits, payloadStart: BARKER13.length };
      for (var k in extra) b[k] = extra[k];
      add(b);
      add({ type: 'idle', frames: p.gapFrames });
    }

    add({ type: 'idle', frames: p.leadFrames, label: 'lead' });
    add({ type: 'tone', period: p.p0, frames: p.pilotFrames, label: 'pilot0' });
    add({ type: 'tone', period: p.p1, frames: p.pilotFrames, label: 'pilot1' });
    add({ type: 'idle', frames: p.gapFrames });

    var prbs = new Prbs(p.seed);
    if (p.mode === 'sweep') {
      p.sweepN.forEach(function (N, si) {
        for (var k = 0; k < p.packetsPerRate; k++) {
          packet(N, BARKER13.concat(prbs.take(p.payloadBits)), { segment: si, packet: k });
        }
      });
    } else if (p.mode === 'prbs') {
      for (var k = 0; k < p.packets; k++) {
        packet(p.N, BARKER13.concat(prbs.take(p.payloadBits)), { segment: 0, packet: k });
      }
    } else if (p.mode === 'text') {
      var chunks = textChunks(p.text, p.textChars);
      var idx = 0;
      for (var r = 0; r < p.repeats; r++) {
        chunks.forEach(function (c) {
          packet(p.N, BARKER13.concat(encodeTextChunk(c, p.textChars)), { segment: 0, packet: idx++, text: c });
        });
      }
    } else {
      throw new Error('unknown mode ' + p.mode);
    }
    add({ type: 'idle', frames: p.gapFrames, label: 'tail' });
    return { params: p, blocks: blocks, totalFrames: cursor };
  }

  function toneLevel(f, period) { return (f % period) < period / 2 ? 1 : 0; }

  // Level of display frame k (0 <= k < totalFrames). `hint` is an optional
  // {i: blockIndex} cursor for O(1) sequential access.
  function levelAt(plan, k, hint) {
    var blocks = plan.blocks;
    var i = hint ? hint.i : 0;
    if (i >= blocks.length || blocks[i].startFrame > k) i = 0;
    while (i < blocks.length - 1 && k >= blocks[i].startFrame + blocks[i].frames) i++;
    if (hint) hint.i = i;
    var b = blocks[i];
    if (k < 0 || k >= plan.totalFrames) return 0.5;
    var f = k - b.startFrame;
    if (b.type === 'idle') return 0.5;
    if (b.type === 'tone') return toneLevel(f, b.period);
    var j = Math.floor(f / b.N);
    var period = b.bits[j] ? plan.params.p1 : plan.params.p0;
    return toneLevel(f - j * b.N, period);
  }

  function levels(plan) {
    var out = new Array(plan.totalFrames);
    var hint = { i: 0 };
    for (var k = 0; k < plan.totalFrames; k++) out[k] = levelAt(plan, k, hint);
    return out;
  }

  function alphaOf(plan, level) {
    return plan.params.base + plan.params.dalpha * level;
  }

  // Compact string of levels ('0', '1', 'm') used by the parity tests.
  function levelString(plan) {
    return levels(plan).map(function (v) { return v === 0.5 ? 'm' : String(v); }).join('');
  }

  function summary(plan, refreshHz) {
    var p = plan.params;
    var R = refreshHz || 120;
    var Ns = p.mode === 'sweep' ? p.sweepN : [p.N];
    return {
      f0: R / p.p0,
      f1: R / p.p1,
      ratesBps: Ns.map(function (n) { return R / n; }),
      seconds: plan.totalFrames / R,
      packets: plan.blocks.filter(function (b) { return b.type === 'packet'; }).length
    };
  }

  return {
    BARKER13: BARKER13,
    PROFILES: PROFILES,
    DEFAULTS: DEFAULTS,
    normalizeParams: normalizeParams,
    Prbs: Prbs,
    crc8: crc8,
    encodeTextChunk: encodeTextChunk,
    textChunks: textChunks,
    buildPlan: buildPlan,
    levelAt: levelAt,
    levels: levels,
    alphaOf: alphaOf,
    levelString: levelString,
    summary: summary
  };
});
