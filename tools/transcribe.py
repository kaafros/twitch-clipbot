import sys
from faster_whisper import WhisperModel
m = WhisperModel("small.en", device="cpu", compute_type="int8")
segs, _ = m.transcribe(sys.argv[1], vad_filter=True)
for s in segs:
    print(f"[{s.start:6.1f}-{s.end:6.1f}] {s.text.strip()}")
