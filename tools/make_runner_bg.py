"""Generate an ORIGINAL endless-runner style background loop (no third-party footage).
Output: assets/bg/runner.mp4, 1080x960, 30 fps."""
import math, random, subprocess, sys
from PIL import Image, ImageDraw

W, H, FPS, SECS = 1080, 960, 30, int(sys.argv[2]) if len(sys.argv) > 2 else 30
VX, VY = 540, 250                       # vanishing point
SPEED = 9.0                             # world units per second
LANE = 1.1                              # lane spacing in world units
rng = random.Random(7)

def proj(x, z):                          # world (x, z) on the ground -> screen
    s = 1.0 / z
    return VX + x * 520 * s, VY + (H - VY) * s * 1.05

def height(h, z):
    return h * 520 / z

# deterministic world objects along an infinite track (z measured from the start)
trains = [(rng.choice([-1, 1]) * LANE, 14 + i * 9 + rng.random() * 4,
           rng.choice([(231, 76, 60), (52, 152, 219), (241, 196, 15), (155, 89, 182), (46, 204, 113)]))
          for i in range(400)]
barriers = [(0, 30 + i * 23 + rng.random() * 6) for i in range(300)]
coins = [(0, 6 + i * 1.6) for i in range(3000)]
PAL = [(255, 99, 72), (72, 219, 251), (255, 159, 243), (254, 202, 87), (29, 209, 161), (95, 39, 205)]

ff = subprocess.Popen(["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                       "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "veryfast",
                       "-crf", "22", "-pix_fmt", "yuv420p", sys.argv[1]], stdin=subprocess.PIPE)
score = 0
collected = set()
for f in range(FPS * SECS):
    t = f / FPS
    travel = t * SPEED
    im = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(im)
    # sky gradient
    for y in range(0, VY + 40, 4):
        k = y / (VY + 40)
        d.rectangle([0, y, W, y + 4], fill=(int(120 + 100 * k), int(170 + 60 * k), 255))
    # sun
    d.ellipse([820, 40, 940, 160], fill=(255, 236, 140))
    # ground (whole lower area), then a lighter track bed
    d.rectangle([0, VY - 2, W, H], fill=(105, 98, 92))
    d.polygon([proj(-1.9, 60), proj(1.9, 60), proj(1.9, 0.4), proj(-1.9, 0.4)], fill=(128, 120, 112))
    # side walls (buildings) in segments
    seg = 2.0
    first = math.floor(travel / seg) * seg
    for i in range(40, 0, -1):
        z0 = first + i * seg - travel
        z1 = z0 + seg
        if z0 < 0.35:
            continue
        col = PAL[int((first / seg) + i) % len(PAL)]
        for side in (-1, 1):
            x = side * 2.9
            (ax, ay), (bx, by) = proj(x, z1), proj(x, z0)
            d.polygon([(ax, ay), (bx, by), (bx, by - height(3.2, z0)), (ax, ay - height(3.2, z1))], fill=col)
            # windows
            wx, wy = proj(x, (z0 + z1) / 2)
            ww, wh = height(0.25, (z0 + z1) / 2), height(0.4, (z0 + z1) / 2)
            for r in range(3):
                yy = wy - height(0.8 + r * 0.8, (z0 + z1) / 2)
                d.rectangle([wx - ww, yy, wx + ww, yy + wh], fill=(255, 250, 220))
    # rails + sleepers on 3 lanes
    sl = 0.7
    firsts = math.floor(travel / sl) * sl
    for i in range(80, 0, -1):
        z = firsts + i * sl - travel
        if z < 0.4:
            continue
        for lane in (-LANE, 0, LANE):
            a, b = proj(lane - 0.42, z), proj(lane + 0.42, z)
            d.line([a, b], fill=(92, 64, 51), width=max(2, int(30 / z)))
    for lane in (-LANE, 0, LANE):
        for off in (-0.3, 0.3):
            d.line([proj(lane + off, 40), proj(lane + off, 0.4)], fill=(190, 190, 200), width=4)
    # collect visible objects, draw far to near
    objs = []
    for x, z, c in trains:
        zz = z - travel
        if 1.8 < zz < 45:
            objs.append((zz, "train", x, c))
    for x, z in barriers:
        zz = z - travel
        if 0.5 < zz < 45:
            objs.append((zz, "bar", x, None))
    for i, (x, z) in enumerate(coins):
        zz = z - travel
        if zz < 1.25 and i not in collected and zz > 0:
            collected.add(i); score += 1
        if 1.25 < zz < 30 and i not in collected:
            objs.append((zz, "coin", x, i))
    for zz, kind, x, extra in sorted(objs, key=lambda o: -o[0]):
        if kind == "train":
            z0, z1 = zz, zz + 6
            (ax, ay), (bx, by) = proj(x - 0.5, z0), proj(x + 0.5, z0)
            hh = height(1.6, z0)
            (cx, cy), (dx, dy) = proj(x - 0.5, z1), proj(x + 0.5, z1)
            d.polygon([(cx, cy - height(1.6, z1)), (dx, dy - height(1.6, z1)), (bx, by - hh), (ax, ay - hh)],
                      fill=tuple(min(255, v + 40) for v in extra))
            d.rectangle([ax, ay - hh, bx, by], fill=extra)
            d.rectangle([ax + (bx - ax) * 0.15, ay - hh * 0.8, bx - (bx - ax) * 0.15, ay - hh * 0.45],
                        fill=(200, 235, 255))
        elif kind == "bar":
            (ax, ay), (bx, by) = proj(x - 0.45, zz), proj(x + 0.45, zz)
            hh = height(0.55, zz)
            for k in range(6):
                x0 = ax + (bx - ax) * k / 6
                d.rectangle([x0, ay - hh, x0 + (bx - ax) / 6, ay - hh * 0.6],
                            fill=(230, 60, 60) if k % 2 == 0 else (250, 250, 250))
            d.rectangle([ax, ay - hh * 0.6, ax + 6, ay], fill=(80, 80, 80))
            d.rectangle([bx - 6, ay - hh * 0.6, bx, ay], fill=(80, 80, 80))
        else:
            cx, cy = proj(x, zz)
            r = height(0.16, zz)
            spin = abs(math.cos(t * 6 + extra))
            cy -= height(0.45, zz)
            d.ellipse([cx - r * spin, cy - r, cx + r * spin, cy + r], fill=(255, 205, 40), outline=(200, 140, 0),
                      width=max(1, int(r / 5)))
    # runner (original bean character), jumps when a barrier is close
    jump = 0.0
    for x, z in barriers:
        zz = z - travel
        if -0.6 < zz < 2.2:
            p = (2.2 - zz) / 2.8
            jump = math.sin(p * math.pi) * 1.4
    px, py = proj(0, 1.45)
    bob = abs(math.sin(t * 12)) * 8 if jump == 0 else 0
    py -= height(jump, 1.45) + bob
    d.ellipse([px - 70, py + 5, px + 70, py + 30], fill=(60, 55, 50))  # shadow-ish
    d.rounded_rectangle([px - 55, py - 190, px + 55, py], radius=55, fill=(255, 127, 80))
    d.rounded_rectangle([px - 40, py - 160, px + 40, py - 110], radius=20, fill=(40, 40, 60))
    d.ellipse([px - 25, py - 150, px - 8, py - 128], fill=(120, 255, 220))
    d.ellipse([px + 8, py - 150, px + 25, py - 128], fill=(120, 255, 220))
    leg = math.sin(t * 14) * 18 if jump == 0 else 10
    d.rounded_rectangle([px - 40, py - 10, px - 14, py + 30 + leg], radius=10, fill=(40, 40, 60))
    d.rounded_rectangle([px + 14, py - 10, px + 40, py + 30 - leg], radius=10, fill=(40, 40, 60))
    # coin counter
    d.rounded_rectangle([24, 24, 250, 90], radius=30, fill=(0, 0, 0, 120))
    d.ellipse([38, 36, 80, 78], fill=(255, 205, 40))
    d.text((95, 38), f"{score}", fill="white", font_size=40)
    ff.stdin.write(im.tobytes())
ff.stdin.close()
ff.wait()
