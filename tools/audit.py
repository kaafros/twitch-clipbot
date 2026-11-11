"""Re-check every posted Twitch clip: title + full transcript against the safety filter."""
import json, re, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import captions, clipbot

LIMITED = re.compile(r"\b(gambl|casino|stake\.com|roobet|case open|bet(ting)?|kill (myself|yourself)|shoot|gun)", re.I)
ids = [i for i in json.loads(Path("posted.json").read_text()) if not i.startswith("batch:")]
tok = clipbot.twitch_token()
for i in range(0, len(ids), 50):
    for c in clipbot.helix("clips", tok, id=ids[i:i + 50]):
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "c.mp4"
            try:
                clipbot.download(c["url"], raw)
                text = captions.text(captions.transcribe(raw, f"{c['broadcaster_name']} stream clip."))
            except Exception as e:
                text = f"(could not check: {e})"
        verdict = "UNSAFE" if clipbot.unsafe(c["title"] + " " + text) else ("LIMITED_ADS" if LIMITED.search(c["title"] + " " + text) else "ok")
        hits = sorted(set(m.group(0).lower() for m in clipbot.BLOCKED.finditer(c["title"] + " " + text)))
        print(f"AUDIT {verdict} | {c['broadcaster_name']} | {c['title']} | {hits}")
