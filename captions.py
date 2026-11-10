"""Auto subtitles in the 'viral' style: short chunks, the word being spoken highlighted and popping."""
import re
import subprocess

import numpy as np

_model = None
FONT = "Montserrat Black"           # installed via fonts-montserrat; falls back to a bold sans if missing
HIGHLIGHTS = ["&H0000FFFF&", "&H0000FF7F&", "&H00FFFF00&"]  # yellow, green, cyan (ASS colours are BGR)


def _load_audio(path):
    raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", str(path), "-f", "s16le", "-ac", "1",
                          "-ar", "16000", "-"], check=True, capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _ts(t):
    t = max(0.0, t)
    return f"{int(t // 3600)}:{int(t % 3600 // 60):02d}:{t % 60:05.2f}"


def _clean(word):
    w = re.sub(r"[^\w'$%#@&-]", "", word.strip()).upper()
    return w.replace("\\", "")


def build(src, out, margin_v=680, font_size=92, max_words=3, prompt=""):
    """Write an .ass subtitle file for src. Returns the path, or None if there's no speech."""
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel("small.en", device="cpu", compute_type="int8")
    segs, _ = _model.transcribe(_load_audio(src), word_timestamps=True, vad_filter=True, beam_size=5,
                                initial_prompt=prompt or None)
    words = [w for s in segs for w in (s.words or []) if _clean(w.word)]
    if not words:
        return None

    # group into short chunks; break on pauses and sentence ends
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > 0.5 or cur[-1].word.strip()[-1:] in ".?!"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    chunks.append(cur)

    style = (f"Style: Default,{FONT},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,"
             f"100,100,1,0,1,9,4,2,50,50,{margin_v},1")
    lines = ["[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920", "WrapStyle: 0", "",
             "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
             "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
             "Alignment, MarginL, MarginR, MarginV, Encoding", style, "",
             "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    for ci, c in enumerate(chunks):
        chunk_end = c[-1].end
        if ci + 1 < len(chunks):
            chunk_end = min(max(chunk_end, c[0].start + 0.5), chunks[ci + 1][0].start)
        colour = HIGHLIGHTS[ci % len(HIGHLIGHTS)] if ci % 4 == 3 else HIGHLIGHTS[0]
        for wi, w in enumerate(c):
            start = c[0].start if wi == 0 else w.start
            end = c[wi + 1].start if wi + 1 < len(c) else chunk_end
            if end - start < 0.05:
                continue
            parts = []
            for k, x in enumerate(c):
                t = _clean(x.word)
                if k == wi:  # the word being spoken: coloured + pops in
                    parts.append(f"{{\\c{colour}\\fscx112\\fscy112\\t(0,90,\\fscx122\\fscy122)"
                                 f"\\t(90,180,\\fscx112\\fscy112)}}{t}{{\\c&H00FFFFFF&\\fscx100\\fscy100}}")
                else:
                    parts.append(t)
            fade = "{\\fad(60,0)}" if wi == 0 else ""
            lines.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Default,,0,0,0,,{fade}{' '.join(parts)}")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return str(out)
