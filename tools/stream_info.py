"""Print a summary of a YouTube video: title, description, chapters and the 'most replayed' peaks."""
import json, subprocess, sys

url, start = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 0
info = json.loads(subprocess.run(["yt-dlp", "-J", "--no-warnings", url], check=True,
                                 capture_output=True, text=True).stdout)
print("TITLE:", info.get("title"))
print("DURATION:", info.get("duration"))
print("DESCRIPTION:", (info.get("description") or "")[:1500])
for ch in info.get("chapters") or []:
    print(f"CHAPTER {int(ch['start_time'])}-{int(ch['end_time'])}: {ch['title']}")
heat = [h for h in (info.get("heatmap") or []) if h["start_time"] >= start]
for h in sorted(heat, key=lambda h: h["value"], reverse=True)[:40]:
    print(f"PEAK {int(h['start_time'])}-{int(h['end_time'])} value={h['value']:.3f}")
