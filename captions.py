"""Auto subtitles: transcribe a clip with Whisper and write a bold, short-chunk .ass subtitle file."""
import subprocess

import numpy as np

_model = None


def _load_audio(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _ts(t):
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def build(src, out, margin_v=680, font_size=80, max_words=3):
    """Write an .ass file for src; returns the path, or None if no speech was found."""
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segs, _ = _model.transcribe(_load_audio(src), word_timestamps=True, vad_filter=True)
    words = [w for s in segs for w in (s.words or []) if w.word.strip()]
    if not words:
        return None
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > 0.6):
            chunks.append(cur)
            cur = []
        cur.append(w)
    chunks.append(cur)

    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding",
             f"Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,"
             f"100,100,0,0,1,7,3,2,60,60,{margin_v},1", "",
             "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for i, c in enumerate(chunks):
        end = c[-1].end
        if i + 1 < len(chunks):
            end = min(max(end, c[0].start + 0.4), chunks[i + 1][0].start)
        text = " ".join(w.word.strip() for w in c).upper()
        text = text.replace("\\", "").replace("{", "(").replace("}", ")")
        # pop-in effect: start slightly bigger, settle to normal size
        lines.append(f"Dialogue: 0,{_ts(c[0].start)},{_ts(end)},Default,,0,0,0,,"
                     f"{{\\fscx115\\fscy115\\t(0,120,\\fscx100\\fscy100)}}{text}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return str(out)
