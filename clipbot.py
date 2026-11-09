"""Fetch top Twitch clips from approved streamers, make them vertical, upload as YouTube Shorts."""
import datetime as dt
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
import yaml
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).parent
STATE = ROOT / "posted.json"


def twitch_token():
    r = requests.post("https://id.twitch.tv/oauth2/token", params={
        "client_id": os.environ["TWITCH_CLIENT_ID"],
        "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
        "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    return r.json()["access_token"]


def helix(path, token, **params):
    r = requests.get(f"https://api.twitch.tv/helix/{path}", params=params, headers={
        "Client-Id": os.environ["TWITCH_CLIENT_ID"],
        "Authorization": f"Bearer {token}"}, timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def get_clips(cfg, token):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=cfg["lookback_hours"]))
    since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    clips = []
    for s in cfg["streamers"]:
        users = helix("users", token, login=s["name"])
        if not users:
            print(f"Streamer not found: {s['name']}")
            continue
        for c in helix("clips", token, broadcaster_id=users[0]["id"], started_at=since, first=50):
            if c["view_count"] >= cfg["min_views"]:
                clips.append(c)
    return sorted(clips, key=lambda c: c["view_count"], reverse=True)


def download(url, out):
    subprocess.run(["yt-dlp", "-q", "-f", "best[ext=mp4]/best", "-o", str(out), url], check=True)


def make_vertical(src, dst):
    # Blurred full-screen background with the original clip centered on top (1080x1920).
    vf = ("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg];"
          "[0:v]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-t", "180", str(dst)], check=True)


def youtube():
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"],
                        client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(yt, path, clip, cfg):
    title = clip["title"].replace("<", "").replace(">", "").strip() or "Twitch clip"
    title = f"{title[:85]} #shorts"
    desc = (f"🎮 Streamer: {clip['broadcaster_name']} — https://twitch.tv/{clip['broadcaster_name'].lower()}\n"
            f"📎 Original clip: {clip['url']}\n\n"
            f"Shared with the streamer's permission. Go follow them!\n#shorts #twitch")
    body = {
        "snippet": {"title": title, "description": desc, "tags": cfg["tags"] + [clip["broadcaster_name"]],
                    "categoryId": "20"},  # 20 = Gaming
        "status": {"privacyStatus": cfg["privacy"], "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(path), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    posted = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    new = [c for c in get_clips(cfg, twitch_token()) if c["id"] not in posted]
    print(f"{len(new)} new clips found")
    if not new:
        return
    yt = youtube()
    done = 0
    with tempfile.TemporaryDirectory() as tmp:
        for clip in new:
            if done >= cfg["max_uploads_per_run"]:
                break
            try:
                raw, vert = Path(tmp, f"{clip['id']}.mp4"), Path(tmp, f"{clip['id']}_v.mp4")
                download(clip["url"], raw)
                make_vertical(raw, vert)
                vid = upload(yt, vert, clip, cfg)
                print(f"Uploaded '{clip['title']}' -> https://youtube.com/shorts/{vid}")
                posted.add(clip["id"])
                STATE.write_text(json.dumps(sorted(posted), indent=1))
                done += 1
            except Exception as e:  # keep going with the next clip
                print(f"Failed on {clip['url']}: {e}")


if __name__ == "__main__":
    main()
