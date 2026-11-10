"""Viral editing: find the loudest (funniest) moment, trim to <=30s, open with that moment as a hook,
and compute punch-in zoom times on reaction spikes."""
import subprocess

import numpy as np


def loudness(path, step=0.1):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
    n = int(8000 * step)
    if len(a) < n * 5:
        return np.array([]), step
    frames = a[: len(a) // n * n].reshape(-1, n)
    db = 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-6)
    k = 5  # ~0.5 s smoothing
    db = np.convolve(db, np.ones(k) / k, mode="same")
    return db, step


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout
    return float(out or 0)


def auto_plan(path, max_len=30.0, hook_len=2.5):
    """Pick trim window + hook around the loudest moment. Returns (trim, hook) in seconds or (None, None)."""
    db, step = loudness(path)
    dur = duration(path)
    if not len(db) or dur < 8:
        return None, None
    peak = float(np.argmax(db[: max(1, len(db) - 5)]) * step)  # ignore the very end
    if dur > max_len:
        start = max(0.0, min(peak - max_len * 0.7, dur - max_len))
        trim = (start, start + max_len)
    else:
        trim = (0.0, dur)
    hook = None
    if peak - trim[0] > 6:  # only worth a hook if the punchline isn't already at the start
        hook = (max(trim[0], peak - hook_len * 0.6), min(trim[1], peak + hook_len * 0.4))
    return trim, hook


def build_edit(src, dst, trim=None, hook=None):
    """Write dst = [hook] + [trim window]. Returns dst (or src if no edit needed)."""
    if not trim and not hook:
        return src
    trim = trim or (0, duration(src))
    parts, labels = [], []
    segs = ([hook] if hook else []) + [trim]
    for i, (a, b) in enumerate(segs):
        parts.append(f"[0:v]trim={a:.2f}:{b:.2f},setpts=PTS-STARTPTS[v{i}];"
                     f"[0:a]atrim={a:.2f}:{b:.2f},asetpts=PTS-STARTPTS[a{i}];")
        labels.append(f"[v{i}][a{i}]")
    fc = "".join(parts) + "".join(labels) + f"concat=n={len(segs)}:v=1:a=1[v][a]"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", fc, "-map", "[v]", "-map", "[a]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "160k", str(dst)],
                   check=True)
    return dst


def punch_times(path, n=3, min_gap=4.0, length=0.9):
    """Times (start, end) of the loudest reaction spikes, for quick zoom-ins."""
    db, step = loudness(path)
    if not len(db):
        return []
    thresh = np.percentile(db, 75) + 3
    end = len(db) * step
    order = np.argsort(db)[::-1]
    picks = []
    for i in order:
        t = i * step
        if db[i] < thresh or len(picks) >= n:
            break
        if all(abs(t - p) >= min_gap for p in picks) and 1.0 < t < end - 1.5:
            picks.append(t)
    return [(max(0, t - 0.15), t + length) for t in sorted(picks)]


def zoom_expr(punches, amount=0.16):
    if not punches:
        return None
    return "1+" + str(amount) + "*(" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in punches) + ")"


def punch(w, h, z):
    """Filter snippet: scale up by z(t) per frame then crop back to w x h around the centre."""
    if not z:
        return ""
    return f",scale=w='{w}*({z})':h='{h}*({z})':eval=frame,crop={w}:{h}"
