"""Dry-run: render a batch item exactly like the bot would (incl. subtitles) into preview/."""
import sys, yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import clipbot
cfg = yaml.safe_load(open("config.yaml"))
Path("preview").mkdir(exist_ok=True)
for it in [b for b in cfg["batches"] if b["id"] in sys.argv[1:]]:
    fake = {"id": it["id"], "broadcaster_name": it.get("streamer", ""), "broadcaster_login": it.get("streamer", "").lower(),
            "tag_text": it.get("tag_text"), "prompt": it.get("prompt")}
    clipbot.make_vertical(Path(it["source"]), Path(f"preview/{it['id']}.mp4"), fake,
                          dict(cfg, layout=it.get("layout", "zoom")), it.get("overlay_title", it["title"]))
    print("RENDERED", it["id"])
