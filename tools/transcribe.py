import subprocess, sys
import numpy as np
from faster_whisper import WhisperModel
raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-i", sys.argv[1], "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                     check=True, capture_output=True).stdout
audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
m = WhisperModel("small.en", device="cpu", compute_type="int8")
segs, _ = m.transcribe(audio, vad_filter=len(sys.argv) < 3)
for s in segs:
    print(f"TX [{s.start:6.1f}-{s.end:6.1f}] {s.text.strip()}")
