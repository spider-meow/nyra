"""Soundtrack: 120 BPM (beat 0.5 s), D major, I-vi-IV-V; effects in key, on the same grid."""
import numpy as np, wave

SR, DUR, BEAT = 48000, 20.0, 0.5
N = int(SR * DUR)
L = np.zeros(N); R = np.zeros(N)
rng = np.random.default_rng(7)
hz = lambda m: 440 * 2 ** ((m - 69) / 12)

def add(sig, t0, gain=1.0, pan=0.0):
    i = int(t0 * SR); sig = sig[: max(0, N - i)]
    L[i:i + len(sig)] += sig * gain * (1 - max(pan, 0))
    R[i:i + len(sig)] += sig * gain * (1 + min(pan, 0))

def env(n, a, d):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)

def lowpass(x, cut):
    a = np.exp(-2 * np.pi * cut / SR); y = np.zeros_like(x); acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc; y[i] = acc
    return y

def kick(t0, g=1.0):
    n = int(0.35 * SR); t = np.arange(n) / SR
    f = 48 + 90 * np.exp(-t * 30)
    add(np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9), t0, 0.55 * g)

def hat(t0, g=1.0, pan=0.25):
    n = int(0.06 * SR); x = rng.standard_normal(n); x = x - lowpass(x, 7000)
    add(x * env(n, 0.001, 0.015), t0, 0.05 * g, pan)

def snap(t0, g=1.0):
    n = int(0.2 * SR); x = rng.standard_normal(n); x = x - lowpass(x, 1500)
    add(x * env(n, 0.001, 0.05), t0, 0.16 * g)

def pluck(m, t0, d=0.35, g=1.0, pan=0.0):
    n = int(d * 3 * SR); t = np.arange(n) / SR; f = hz(m)
    x = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) * np.exp(-t * 8) + 0.12 * np.sin(6 * np.pi * f * t) * np.exp(-t * 14)
    add(x * env(n, 0.003, d), t0, 0.09 * g, pan)

def pad(ms, t0, dur, g=1.0):
    n = int((dur + 0.6) * SR); t = np.arange(n) / SR
    x = sum(np.sin(2 * np.pi * hz(m) * (1 + det) * t) for m in ms for det in (-0.002, 0.002))
    e = np.minimum(1, t / 0.25) * np.clip((dur + 0.6 - t) / 0.6, 0, 1)
    add(x * e, t0, 0.018 * g, -0.2); add(x * e, t0, 0.018 * g, 0.2)

def bass(m, t0, dur, g=1.0):
    n = int(dur * SR); t = np.arange(n) / SR; f = hz(m)
    x = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(4 * np.pi * f * t)
    add(x * np.minimum(1, t / 0.01) * np.clip((dur - t) / 0.05, 0, 1) * np.exp(-t * 1.2), t0, 0.22 * g)

def whoosh(t_end, length=0.5, g=1.0):
    n = int(length * SR); x = lowpass(rng.standard_normal(n), 2500)
    e = (np.arange(n) / n) ** 2.5
    add(x * e, t_end - length, 0.07 * g, -0.3); add(x * e, t_end - length, 0.07 * g, 0.3)

def brush(t0, length=0.35):
    n = int(length * SR); x = rng.standard_normal(n); x = lowpass(x - lowpass(x, 1800), 6000)
    add(x * np.sin(np.pi * np.arange(n) / n) ** 2, t0, 0.05, 0.15)

# I-vi-IV-V in D: D, Bm, G, A (one chord per bar of 2 s)
CHORDS = [(50, [62, 66, 69]), (47, [59, 62, 66]), (43, [55, 59, 62]), (45, [57, 61, 64])]
for bar in range(10):
    t0 = bar * 2.0; root, tones = CHORDS[bar % 4]
    if bar == 8 or bar == 9: root, tones = CHORDS[0] if bar == 9 else CHORDS[3]
    full = 1.5 <= bar < 8.5          # groove from the reveal (3.0 s) to the outro
    pad(tones, t0, 2.0, 1.0 if bar else 0.7)
    for b in range(4):
        tb = t0 + b * BEAT
        if tb < 3.0: continue
        if full or tb < 17.0: kick(tb, 1.0 if b % 2 == 0 else 0.7)
        if b % 2 == 1 and tb < 17.0: snap(tb, 0.8)
        hat(tb + BEAT / 2, 1.0); hat(tb + BEAT / 4, 0.4, -0.25) if tb >= 6.0 and tb < 17.0 else None
    for s in range(4 if bar < 1 else 2):  # bass on half-notes, from the reveal
        tb = t0 + s * 1.0
        if tb >= 3.0 and tb < 19.0: bass(root, tb, 0.95)
    # arpeggio in 8ths
    seq = tones + [tones[1] + 12, tones[2], tones[1], tones[0] + 12, tones[2]]
    for k in range(8):
        tk = t0 + k * BEAT / 2
        if tk < 19.0: pluck(seq[k % len(seq)] + 12, tk, 0.22, 0.7 if tk < 3.0 else 1.0, 0.3 if k % 2 else -0.3)
# final chord rings out
for m in (62, 66, 69, 74): pluck(m + 12, 18.0, 1.2, 1.4)
pad([62, 66, 69, 74], 18.0, 1.6, 1.3)

# Effects, all on the grid of the picture
for c in (3.0, 6.0, 9.5, 13.5, 17.0): whoosh(c)
pluck(81, 1.0, 0.3, 1.6, 0.0); pluck(74, 1.0, 0.3, 1.0)            # expired badge
for t in (0.5, 1.5, 3.75, 8.25, 9.75, 10.25, 13.875, 17.5): brush(t)  # marker strokes
pluck(86, 9.0, 0.12, 1.3, 0.2); pluck(86, 14.5, 0.12, 1.3, 0.2)     # clicks
pluck(81, 15.5, 0.12, 1.2, 0.2)                                     # C key
pluck(78, 11.5, 0.5, 1.4); pluck(74, 11.5, 0.5, 1.0)                # "Confirmé"
KP = [(120,110),(150,300),(205,105),(200,180),(190,255),(262,330),(300,120),(320,250),(230,300),(110,220),(280,190),(175,335)]
scale = [74, 76, 78, 81, 83, 86]
for i, (x, y) in enumerate(KP):
    u, v = 1 - (x - 60) / 280, (y - 40) / 280
    if 0 <= u <= 1 and 0 <= v <= 0.84: pluck(scale[i % 6] + 12, 10.5 + i * 0.125 + 0.25, 0.08, 0.5, 0.4)

mix = np.stack([L, R], 1)
mix *= 0.85 / np.max(np.abs(mix))
mix = np.tanh(mix * 1.2) / np.tanh(1.2)
fade = np.ones(N); k = int(0.4 * SR); fade[-k:] = np.linspace(1, 0, k); mix *= fade[:, None]
with wave.open("music.wav", "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767 * 0.95).astype("<i2").tobytes())
print("ok", mix.shape)
