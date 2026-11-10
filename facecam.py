"""Find the streamer's face-cam in a clip so it can be cropped to the top of a split-screen."""
import subprocess

import cv2
import numpy as np


def _frames(path, n=8):
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(path)], capture_output=True, text=True).stdout or 10)
    out = []
    for i in range(n):
        t = dur * (i + 0.5) / n
        raw = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
                              "-vf", "scale=960:-2", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True).stdout
        if raw:
            out.append(np.frombuffer(raw, np.uint8).reshape(-1, 960))
    return out


def find_face_box(path):
    """Return (x, y, w, h) of the face as fractions of the frame, or None."""
    det = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    hits = []
    for g in _frames(path):
        H, W = g.shape
        faces = det.detectMultiScale(g, scaleFactor=1.1, minNeighbors=6, minSize=(int(W * 0.04), int(W * 0.04)))
        for (x, y, w, h) in faces:
            hits.append((x / W, y / H, w / W, h / H))
    if not hits:
        return None
    # keep the face position that shows up most consistently across frames
    best, best_n = None, 0
    for a in hits:
        cx, cy = a[0] + a[2] / 2, a[1] + a[3] / 2
        n = sum(1 for b in hits if abs(b[0] + b[2] / 2 - cx) < 0.06 and abs(b[1] + b[3] / 2 - cy) < 0.08)
        if n > best_n or (n == best_n and a[2] > best[2]):
            best, best_n = a, n
    if best_n < 2:
        return None
    group = [b for b in hits if abs(b[0] + b[2] / 2 - (best[0] + best[2] / 2)) < 0.06
             and abs(b[1] + b[3] / 2 - (best[1] + best[3] / 2)) < 0.08]
    return tuple(float(np.median([g[i] for g in group])) for i in range(4))


def cam_crop(face, frame_w, frame_h, out_aspect, scale=3.0):
    """Crop rectangle (pixels) around the face with the given aspect (w/h), clamped to the frame."""
    fx, fy, fw, fh = face[0] * frame_w, face[1] * frame_h, face[2] * frame_w, face[3] * frame_h
    cw = min(frame_w, fw * scale * 1.2)
    ch = cw / out_aspect
    if ch > frame_h:
        ch, cw = frame_h, frame_h * out_aspect
    cx, cy = fx + fw / 2, fy + fh / 2 + fh * 0.35  # include shoulders
    x = min(max(cx - cw / 2, 0), frame_w - cw)
    y = min(max(cy - ch / 2, 0), frame_h - ch)
    return int(x) // 2 * 2, int(y) // 2 * 2, int(cw) // 2 * 2, int(ch) // 2 * 2
