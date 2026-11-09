"""Fetch top Twitch clips from approved streamers, make them vertical, upload as YouTube Shorts."""
import datetime as dt
import json
import os
import random
import re
import subprocess
import textwrap
import tempfile
from pathlib import Path

import requests
import yaml
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import tiktok

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
                c["broadcaster_login"] = s["name"].lower()
                clips.append(c)
    prio = {s["name"].lower(): s.get("priority", 0) for s in cfg["streamers"]}
    return sorted(clips, key=lambda c: (prio.get(c["broadcaster_login"], 0), c["view_count"]), reverse=True)


def download(url, out):
    subprocess.run(["yt-dlp", "-q", "-f", "best[ext=mp4]/best", "-o", str(out), url], check=True)


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1080, 1920


def overlay_text(text):
    """Keep only characters the font can draw (drops emoji)."""
    return "".join(ch for ch in text if ord(ch) < 0x2000).strip()


def drawtext(textfile, size, y_expr, color="white"):
    return (f"drawtext=fontfile={FONT}:textfile={textfile}:fontsize={size}:fontcolor={color}:"
            f"borderw=6:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y={y_expr}")


def make_vertical(src, dst, clip, cfg, nice_title):
    layout = cfg.get("layout", "zoom")
    streamer_cfg = next((s for s in cfg["streamers"] if s["name"].lower() == clip["broadcaster_login"]), {})
    tmp = Path(dst).parent
    title_file, name_file = tmp / f"{clip['id']}_t.txt", tmp / f"{clip['id']}_n.txt"
    title_file.write_text("\n".join(textwrap.wrap(overlay_text(nice_title), 22)[:3]))
    name_file.write_text(clip.get("tag_text") or f"twitch.tv/{clip['broadcaster_login']}")

    if layout == "split" and streamer_cfg.get("facecam"):
        x, y, w, h = streamer_cfg["facecam"]  # fractions of the frame
        cam_h = 640
        vf = (f"[0:v]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
              f"crop={W}:{cam_h}[cam];"
              f"[0:v]scale=-2:{H - cam_h},crop={W}:{H - cam_h}[game];"
              f"[cam][game]vstack,{drawtext(title_file, 60, cam_h + 40)}")
    elif layout == "fill":
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"{drawtext(title_file, 64, 220)}")
    else:  # "zoom" (default) or "blur": blurred background, video in the middle, title on top
        zoom = cfg.get("zoom", 1.0) if layout == "zoom" else 1.0
        fg_w = int(W * zoom) // 2 * 2
        fg_h = int(fg_w * 9 / 16) // 2 * 2
        top = (H - fg_h) // 2
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5,"
              f"eq=brightness=-0.12[bg];"
              f"[0:v]scale={fg_w}:{fg_h},crop={W}:{fg_h}[fg];"
              f"[bg][fg]overlay=0:{top},"
              f"{drawtext(title_file, 68, f'({top}-text_h)/2')},"
              f"{drawtext(name_file, 44, f'{top + fg_h}+({top}-text_h)/2', '#bf94ff')}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-filter_complex", vf,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-c:a", "aac", "-b:a", "128k", "-t", "180", str(dst)], check=True)


FALLBACK_TITLES = ["{s} can't believe this", "{s} had chat going crazy", "Wait for it... ({s})",
                   "{s} lost it", "This {s} moment is insane", "{s} was NOT ready"]


def make_title(clip):
    s = clip["broadcaster_name"]
    t = re.sub(r"(.)\1{3,}", r"\1\1\1", clip["title"].replace("<", "").replace(">", "")).strip()
    letters = {c for c in t.lower() if c.isalpha()}
    if len(letters) < 5 or len(t) < 6:  # junk titles like "HHHHHQ" or "yoooo"
        t = random.Random(clip["id"]).choice(FALLBACK_TITLES).format(s=s)
    elif s.lower() not in t.lower():
        t = f"{s}: {t}"
    return t[:85].rstrip()


def youtube():
    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"],
                        token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"],
                        client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def streamer_cfg(clip, cfg):
    return next((s for s in cfg["streamers"] if s["name"].lower() == clip["broadcaster_login"]), {})


def upload(yt, path, clip, cfg):
    sc = streamer_cfg(clip, cfg)
    tags_line = " ".join(sc.get("hashtags", []))
    title = f"{make_title(clip)} {tags_line} #shorts".replace("  ", " ")
    desc = (f"🎮 Streamer: {clip['broadcaster_name']} — https://twitch.tv/{clip['broadcaster_name'].lower()}\n"
            f"📎 Original clip: {clip['url']}\n\n"
            f"Shared with the streamer's permission. Go follow them!\n{tags_line} #shorts #twitch".strip())
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


def notify(clip, vid, cfg):
    """For paid campaigns: open a GitHub issue so the owner gets an instant notification to submit the link."""
    campaign = streamer_cfg(clip, cfg).get("campaign")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (campaign and token and repo):
        return
    url = f"https://youtube.com/shorts/{vid}"
    body = (f"New **{clip['broadcaster_name']}** Short is live: {url}\n\n"
            f"Submit it in Content Rewards now (deadline 30 min): {campaign}\n\nClose this issue once submitted.")
    try:
        requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"title": f"Submit now: {clip['broadcaster_name']} – {url}", "body": body})
    except Exception as e:
        print(f"Notify failed: {e}")


def upload_batch_item(yt, item, cfg):
    body = {
        "snippet": {"title": item["title"][:100], "description": item["description"], "tags": item.get("tags", []),
                    "categoryId": "20"},
        "status": {"privacyStatus": cfg["privacy"], "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(ROOT / item["file"]), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def notify_batch(item, vid):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (item.get("campaign") and token and repo):
        return
    url = f"https://youtube.com/shorts/{vid}"
    requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=30,
                  headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                  json={"title": f"Submit now: {item['id']} – {url}",
                        "body": f"New campaign Short is live: {url}\n\nSubmit it in Content Rewards now "
                                f"(deadline 30 min): {item['campaign']}\n\nClose this issue once submitted."})


def crosspost(path, caption, name, cfg):
    if not cfg.get("tiktok", True):
        return
    try:
        pid = tiktok.post(str(path), caption.replace("#shorts", "").strip(), f"{name}.mp4")
        if pid:
            print(f"Posted to TikTok via Buffer: {pid}")
    except Exception as e:
        print(f"TikTok post failed: {e}")


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    posted = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    done = 0
    pending = [b for b in cfg.get("batches", []) if f"batch:{b['id']}" not in posted]
    if pending:
        yt = youtube()
        for item in pending[: cfg.get("batch_per_run", 1)]:
            try:
                vid = upload_batch_item(yt, item, cfg)
                print(f"Uploaded batch '{item['id']}' -> https://youtube.com/shorts/{vid}")
                posted.add(f"batch:{item['id']}")
                STATE.write_text(json.dumps(sorted(posted), indent=1))
                notify_batch(item, vid)
                crosspost(ROOT / item["file"], item.get("tiktok_caption") or item["description"], item["id"], cfg)
                done += 1
            except Exception as e:
                print(f"Failed batch {item['id']}: {e}")
    new = [c for c in get_clips(cfg, twitch_token()) if c["id"] not in posted]
    print(f"{len(new)} new clips found")
    if not new:
        return
    yt = youtube()
    with tempfile.TemporaryDirectory() as tmp:
        for clip in new:
            if done >= cfg["max_uploads_per_run"]:
                break
            try:
                raw, vert = Path(tmp, f"{clip['id']}.mp4"), Path(tmp, f"{clip['id']}_v.mp4")
                download(clip["url"], raw)
                make_vertical(raw, vert, clip, cfg, make_title(clip))
                vid = upload(yt, vert, clip, cfg)
                print(f"Uploaded '{clip['title']}' -> https://youtube.com/shorts/{vid}")
                notify(clip, vid, cfg)
                crosspost(vert, f"{make_title(clip)} {' '.join(streamer_cfg(clip, cfg).get('hashtags', []))} "
                                f"#{clip['broadcaster_name']} #twitch #fyp", clip["id"], cfg)
                posted.add(clip["id"])
                STATE.write_text(json.dumps(sorted(posted), indent=1))
                done += 1
            except Exception as e:  # keep going with the next clip
                print(f"Failed on {clip['url']}: {e}")


if __name__ == "__main__":
    main()
