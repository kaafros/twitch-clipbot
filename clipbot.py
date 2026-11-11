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

import captions
import editing
import facecam
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
    clips = []
    for s in cfg["streamers"]:
        if s.get("paused"):
            continue
        since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=s.get("lookback_hours", cfg["lookback_hours"])))
        since = since.strftime("%Y-%m-%dT%H:%M:%SZ")
        users = helix("users", token, login=s["name"])
        if not users:
            print(f"Streamer not found: {s['name']}")
            continue
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for c in helix("clips", token, broadcaster_id=users[0]["id"], started_at=since, ended_at=now, first=100):
            if unsafe(c["title"]):
                print(f"Skipped (blocked words in title): {c['title']}")
                continue
            if c["view_count"] >= cfg["min_views"] and c["duration"] >= s.get("min_duration", 0):
                c["broadcaster_login"] = s["name"].lower()
                c["broadcaster_name"] = s.get("display", c["broadcaster_name"])
                clips.append(c)
    prio = {s["name"].lower(): s.get("priority", 0) for s in cfg["streamers"]}
    now = dt.datetime.now(dt.timezone.utc)

    def velocity(c):  # views per hour, so fresh clips that are blowing up win over old ones
        age = (now - dt.datetime.fromisoformat(c["created_at"].replace("Z", "+00:00"))).total_seconds() / 3600
        return c["view_count"] / max(age, 1.0) ** 0.8

    return sorted(clips, key=lambda c: (prio.get(c["broadcaster_login"], 0), velocity(c)), reverse=True)


def download(url, out):
    subprocess.run(["yt-dlp", "-q", "-f", "best[ext=mp4]/best", "-o", str(out), url], check=True)


FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
W, H = 1080, 1920


def overlay_text(text):
    """Keep only characters the font can draw (drops emoji and invisible/odd symbols)."""
    import unicodedata
    keep = []
    for ch in text:
        cat = unicodedata.category(ch)
        if ord(ch) < 0x2000 and (cat[0] in "LNP" or ch == " " or cat in ("Sm", "Sc")):
            keep.append(ch)
    return re.sub(r"\s+", " ", "".join(keep)).strip()


def title_text(lines, size, center_y, tmpdir, key):
    """Draw each title line separately (a newline inside drawtext shows up as a box on some ffmpeg builds)."""
    line_h = int(size * 1.25)
    y0 = int(center_y - line_h * len(lines) / 2)
    parts = []
    for i, line in enumerate(lines):
        f = Path(tmpdir) / f"{key}_t{i}.txt"
        f.write_text(line)
        parts.append(drawtext(f, size, y0 + i * line_h))
    return ",".join(parts)


def drawtext(textfile, size, y_expr, color="white"):
    return (f"drawtext=fontfile={FONT}:textfile={textfile}:fontsize={size}:fontcolor={color}:"
            f"borderw=6:bordercolor=black:line_spacing=12:x=(w-text_w)/2:y={y_expr}")


class UnsafeClip(Exception):
    pass


def make_vertical(src, dst, clip, cfg, nice_title):
    if cfg.get("viral_edit", True):
        try:
            trim, hook = clip.get("trim"), clip.get("hook")
            if not trim and not hook:
                trim, hook = editing.auto_plan(src, cfg.get("max_length", 30))
                clip["hook"] = hook
            src = editing.build_edit(src, Path(dst).parent / f"{clip['id']}_edit.mp4",
                                     tuple(trim) if trim else None, tuple(hook) if hook else None)
        except Exception as e:
            print(f"Viral edit skipped: {e}")
    Z = editing.zoom_expr(editing.punch_times(src)) if cfg.get("punch_in", True) else None
    words = []
    if cfg.get("subtitles", True) or cfg.get("quote_titles", True):
        try:
            prompt = clip.get("prompt") or f"{clip.get('broadcaster_name', '')} stream clip. Twitch chat, bro, bruh, W, L."
            words = captions.transcribe(src, prompt)
        except Exception as e:
            print(f"Transcription failed: {e}")
    if words and unsafe(captions.text(words)):
        raise UnsafeClip("transcript contains blocked words")
    if cfg.get("quote_titles", True) and words and not clip.get("keep_title"):
        weak = len(re.findall(r"[A-Za-z]{2,}", clip.get("title", ""))) < 4
        hook = clip.get("hook")
        q = quote_title(clip, words, (hook[1] - hook[0]) if hook else 3.0) if weak else None
        if q:
            nice_title = q.rsplit(" ", 1)[0]  # same text without the emoji (the font can't draw emoji)
            clip["final_title"] = q
    layout = cfg.get("layout", "zoom")
    streamer_cfg = next((s for s in cfg["streamers"] if s["name"].lower() == clip["broadcaster_login"]), {})
    tmp = Path(dst).parent
    title_file, name_file = tmp / f"{clip['id']}_t.txt", tmp / f"{clip['id']}_n.txt"
    title_lines = textwrap.wrap(overlay_text(nice_title), 22)[:3] or [" "]
    T = lambda size, cy: title_text(title_lines, size, cy, tmp, clip["id"])
    name_file.write_text(clip.get("tag_text") or f"twitch.tv/{clip['broadcaster_login']}")

    layout = streamer_cfg.get("layout", layout)
    extra_inputs = []
    if layout == "facecam":
        face = None
        try:
            face = facecam.find_face_box(src)
        except Exception as e:
            print(f"Face detection failed: {e}")
        if face and face[2] > cfg.get("fullcam_face_width", 0.16):
            face = None  # face fills the screen = no separate gameplay -> runner split-screen instead
        if face:
            fw, fh = (int(v) for v in subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                 "-of", "csv=p=0:s=x", str(src)], capture_output=True, text=True).stdout.strip().split("x"))
            band, cam_h, game_h = 200, 760, H // 2
            # cut off the bottom strip where the stream's own captions sit (so subtitles don't show twice)
            usable_h = int(fh * (1 - streamer_cfg.get("crop_bottom", 0.0))) // 2 * 2
            cx, cy, cw, ch = facecam.cam_crop(face, fw, usable_h, W / cam_h)
            gw = min(fw, int(usable_h * W / game_h)) // 2 * 2
            top, fg_h = 0, H // 2  # subtitles go on the seam
            # gameplay crop: centred, but pushed away from the face-cam side so it doesn't show twice
            gx = (fw - gw) // 2
            f_left, f_right = face[0] * fw, (face[0] + face[2]) * fw
            if f_right < fw / 2:
                gx = max(gx, int(f_right + face[2] * fw * 1.5))
            elif f_left > fw / 2:
                gx = min(gx, int(f_left - face[2] * fw * 1.5) - gw)
            gx = max(0, min(fw - gw, gx)) // 2 * 2
            vf = (f"[0:v]crop={cw}:{ch}:{cx}:{cy},scale={W}:{cam_h}{editing.punch(W, cam_h, Z)},setsar=1[cam];"
                  f"[0:v]crop={gw}:{usable_h}:{gx}:0,scale={W}:{game_h},setsar=1[game];"
                  f"color=c=black:s={W}x{band}:r=30[band];"
                  f"[band][cam][game]vstack=inputs=3:shortest=1,"
                  f"{T(58, band // 2)}")
        else:
            layout = "gameplay"  # no face-cam found: use the runner split-screen instead
    if layout == "facecam":
        pass
    elif layout == "gameplay":
        # split screen: clip on top, original endless-runner gameplay below (keeps viewers watching)
        bg = ROOT / cfg.get("gameplay_bg", "assets/bg/runner.mp4")
        bg_start = random.Random(clip["id"]).uniform(0, max(0.0, editing.duration(bg) - 35))
        extra_inputs = ["-stream_loop", "-1", "-ss", f"{bg_start:.1f}", "-i", str(bg)]
        half = H // 2
        top, fg_h = 0, half  # used for subtitle placement
        vf = (f"[0:v]scale={W}:{half}:force_original_aspect_ratio=increase,crop={W}:{half}{editing.punch(W, half, Z)}[clip];"
              f"[1:v]scale={W}:{half},setsar=1[game];"
              f"[clip][game]vstack,"
              f"drawbox=x=0:y=0:w={W}:h=230:color=black@0.45:t=fill,"
              f"{T(62, 115)}")
    elif layout == "split" and streamer_cfg.get("facecam"):
        x, y, w, h = streamer_cfg["facecam"]  # fractions of the frame
        cam_h = 640
        vf = (f"[0:v]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale={W}:{cam_h}:force_original_aspect_ratio=increase,"
              f"crop={W}:{cam_h}[cam];"
              f"[0:v]scale=-2:{H - cam_h},crop={W}:{H - cam_h}[game];"
              f"[cam][game]vstack,{T(60, cam_h + 100)}")
    elif layout == "fill":
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"{T(64, 260)}")
    else:  # "zoom" (default) or "blur": blurred background, video in the middle, title on top
        zoom = cfg.get("zoom", 1.0) if layout == "zoom" else 1.0
        fg_w = int(W * zoom) // 2 * 2
        fg_h = int(fg_w * 9 / 16) // 2 * 2
        top = (H - fg_h) // 2
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=25:5,"
              f"eq=brightness=-0.12[bg];"
              f"[0:v]scale={fg_w}:{fg_h},crop={W}:{fg_h}{editing.punch(W, fg_h, Z)}[fg];"
              f"[bg][fg]overlay=0:{top},"
              f"{T(68, top // 2)},"
              f"{drawtext(name_file, 44, f'{top + fg_h}+({top}-text_h)/2', '#bf94ff')}")
    if cfg.get("subtitles", True):
        try:
            if layout in ("gameplay", "facecam"):
                mv = H // 2 - 70  # centred on the seam between clip and gameplay
            elif layout == "split" and streamer_cfg.get("facecam"):
                mv = 520
            elif layout == "fill":
                mv = 520
            else:
                mv = H - (top + fg_h) + 20  # just over the bottom edge of the video
            prompt = clip.get("prompt") or f"{clip.get('broadcaster_name', '')} stream clip. Twitch chat, bro, bruh, W, L."
            ass = captions.build(src, tmp / f"{clip['id']}.ass", margin_v=mv, prompt=prompt)
            if ass:
                vf += f",subtitles={ass}:fontsdir=/usr/share/fonts"
        except Exception as e:
            print(f"Subtitles skipped: {e}")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), *extra_inputs, "-filter_complex", vf,
                    "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-c:a", "aac", "-b:a", "128k", "-t", "180", "-shortest", str(dst)], check=True)


BLOCKED = re.compile(
    r"\bn[\s-]?word\b|\bnigg|\bniga\b|\bfagg?|\bf[a@]g\b|\bretard|\bkys\b|\bnsfw\b|\bslur|\bracis|"
    r"\bporn|\bnud(e|ity)|\bonlyfans\b|\bsuicid|\bcocaine",
    re.I)


def unsafe(text):
    """True if a title or transcript has slurs / content that gets videos or channels flagged."""
    return bool(BLOCKED.search(text or ""))


FALLBACK_TITLES = ["{s} can't believe this", "{s} had chat going crazy", "Wait for it... ({s})",
                   "{s} lost it", "This {s} moment is insane", "{s} was NOT ready"]


def quote_title(clip, words, hook_len):
    """Title like: Jynxzi: "HE'S CHEATING" 😭  (the line said in the hook / loudest moment)."""
    seg = [w for w in words if w.start < max(hook_len, 3.0)] or words[:8]
    q = re.sub(r"[^\w' ?!]", "", captions.text(seg[:7])).strip()
    if len(q.split()) < 2 or unsafe(q):
        return None
    emoji = random.Random(clip["id"]).choice(["😭", "💀", "😂", "🤯"])
    return f'{clip["broadcaster_name"]}: "{q.upper()}" {emoji}'[:85]


def make_title(clip):
    if clip.get("final_title"):
        return clip["final_title"]
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


def submit_alert(label, campaign, yt_url, tt_url):
    """One GitHub issue (= instant phone/email notification) with every link to submit."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (campaign and token and repo):
        return
    lines = [f"**{label}** is live. Submit both links in Content Rewards within 30 minutes:", "",
             f"1. YouTube: {yt_url}"]
    if tt_url:
        lines.append(f"2. TikTok: {tt_url}")
    lines += ["", f"👉 Campaign (click **Submit clip**): {campaign}", "", "Close this issue once submitted."]
    try:
        requests.post(f"https://api.github.com/repos/{repo}/issues", timeout=30,
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"title": f"Submit now: {label}", "body": "\n".join(lines)})
    except Exception as e:
        print(f"Notify failed: {e}")


def burn_subtitles(src, dst, margin_v):
    """Add auto subtitles to an already-vertical video. Returns dst, or src if there was no speech."""
    ass = captions.build(src, Path(dst).with_suffix(".ass"), margin_v=margin_v,
                         prompt="IShowSpeed, Expedia, Japan, Hudson. Stream clip.")
    if not ass:
        return src
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-vf",
                    f"subtitles={ass}:fontsdir=/usr/share/fonts",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-c:a", "copy", str(dst)], check=True)
    return dst


def upload_batch_item(yt, item, cfg):
    body = {
        "snippet": {"title": item["title"][:100], "description": item["description"], "tags": item.get("tags", []),
                    "categoryId": "20"},
        "status": {"privacyStatus": cfg["privacy"], "selfDeclaredMadeForKids": False},
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(item["_path"]), chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    return resp["id"]


def crosspost(path, caption, name, cfg):
    if not cfg.get("tiktok", True):
        return
    try:
        pid = tiktok.post(str(path), caption.replace("#shorts", "").strip(), f"{name}.mp4")
        if pid:
            link = tiktok.wait_for_link(pid)
            print(f"Posted to TikTok: {link or pid}")
            return link or "(TikTok still processing - check your profile)"
    except Exception as e:
        print(f"TikTok post failed: {e}")


def main():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    posted = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    done = 0
    pending = [b for b in cfg.get("batches", []) if f"batch:{b['id']}" not in posted and not b.get("paused")]
    if pending:
        yt = youtube()
        for item in pending[: cfg.get("batch_per_run", 1)]:
            try:
                if item.get("source"):
                    # raw footage: render it now with the chosen layout (+ subtitles) like a Twitch clip
                    fake = {"id": item["id"], "broadcaster_name": item.get("streamer", ""),
                            "broadcaster_login": item.get("streamer", "").lower(),
                            "tag_text": item.get("tag_text"), "prompt": item.get("prompt"),
                            "trim": item.get("trim"), "hook": item.get("hook")}
                    out = Path(tempfile.gettempdir()) / f"{item['id']}_v.mp4"
                    rcfg = dict(cfg, layout=item.get("layout", cfg.get("layout", "zoom")))
                    make_vertical(ROOT / item["source"], out, fake, rcfg, item.get("overlay_title", item["title"]))
                    item["_path"] = out
                else:
                    item["_path"] = ROOT / item["file"]
                if cfg.get("subtitles", True) and not item.get("source"):
                    try:
                        item["_path"] = burn_subtitles(item["_path"], Path(tempfile.gettempdir()) / f"{item['id']}_sub.mp4",
                                                       item.get("subs_margin", 570))
                    except Exception as e:
                        print(f"Subtitles skipped: {e}")
                vid = upload_batch_item(yt, item, cfg)
                print(f"Uploaded batch '{item['id']}' -> https://youtube.com/shorts/{vid}")
                posted.add(f"batch:{item['id']}")
                STATE.write_text(json.dumps(sorted(posted), indent=1))
                tt = crosspost(item["_path"], item.get("tiktok_caption") or item["description"], item["id"], cfg)
                submit_alert(item["id"], item.get("campaign"), f"https://youtube.com/shorts/{vid}", tt)
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
                tt = crosspost(vert, f"{make_title(clip)} {' '.join(streamer_cfg(clip, cfg).get('hashtags', []))} "
                                f"#{clip['broadcaster_name']} #twitch #fyp", clip["id"], cfg)
                submit_alert(f"{clip['broadcaster_name']} clip", streamer_cfg(clip, cfg).get("campaign"),
                             f"https://youtube.com/shorts/{vid}", tt)
                posted.add(clip["id"])
                STATE.write_text(json.dumps(sorted(posted), indent=1))
                done += 1
            except UnsafeClip as e:
                print(f"Skipped '{clip['title']}': {e}")
                posted.add(clip["id"])  # never try this clip again
                STATE.write_text(json.dumps(sorted(posted), indent=1))
            except Exception as e:  # keep going with the next clip
                print(f"Failed on {clip['url']}: {e}")


if __name__ == "__main__":
    main()
