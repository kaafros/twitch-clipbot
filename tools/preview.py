"""Preview helper. Stage 'frames': download top recent clips and grab a frame from each.
Stage 'render': render reels from the downloaded clips with a given config file."""
import json, subprocess, sys, yaml, datetime as dt
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clipbot

out = Path("preview"); out.mkdir(exist_ok=True)
stage, cfgfile = sys.argv[1], sys.argv[2]
cfg = yaml.safe_load(open(cfgfile))
if stage == "frames":
    tok = clipbot.twitch_token()
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=cfg.get("days", 7))).strftime("%Y-%m-%dT%H:%M:%SZ")
    picked = []
    for s in cfg["streamers"]:
        uid = clipbot.helix("users", tok, login=s["name"])[0]["id"]
        clips = sorted(clipbot.helix("clips", tok, broadcaster_id=uid, started_at=since, first=50),
                       key=lambda c: c["view_count"], reverse=True)[: cfg.get("per_streamer", 3)]
        for c in clips:
            c["broadcaster_login"] = s["name"]
            raw = out / f"{c['id']}.mp4"
            clipbot.download(c["url"], raw)
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "3", "-i", str(raw), "-frames:v", "1",
                            "-vf", "scale=640:-1", str(out / f"{c['id']}.jpg")], check=True)
            picked.append({k: c[k] for k in ("id", "title", "view_count", "broadcaster_name", "broadcaster_login", "duration", "url")})
            print("CLIP", json.dumps(picked[-1]))
    json.dump(picked, open(out / "clips.json", "w"), indent=1)
else:
    for c in json.load(open(out / "clips.json")):
        clipbot.make_vertical(out / f"{c['id']}.mp4", out / f"reel_{c['broadcaster_login']}_{c['id'][:12]}.mp4",
                              c, cfg, clipbot.make_title(c))
        print("RENDERED", c["id"])
