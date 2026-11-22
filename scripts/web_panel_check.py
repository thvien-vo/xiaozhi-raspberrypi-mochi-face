#!/usr/bin/env python3
"""Browser check of the web panel with Playwright (test tool, not an app dependency).

    pip install playwright && python -m playwright install chromium
    python scripts/web_panel_check.py http://127.0.0.1:8080 --out shots/
    python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --max-deg 20

Loads the page at laptop and phone sizes, fails on console errors, checks that the 3D
model loaded, drags the robot's head and checks the angle changed the right way, and
saves screenshots of every tab. --max-deg bounds every angle it sends (real hardware).

Face mirror: changes the expression and the eye color (palette, emotion mode, rainbow)
from the page and checks the face on the page follows: the expression button, and the
color of the eyes in the face canvas (the robot's snapshots, or the page's own drawing).
The robot's eye color and mode are put back at the end. --face-only skips the head.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

TABS = ["control", "motion", "camera", "face", "chat"]


def wait_ready(page, timeout=20000):
    page.wait_for_function("window.__robot && window.__robot.ready && window.__panel.snapshot", timeout=timeout)


def target(page):
    return page.evaluate("({...window.__panel.target})")


def server_state(page):
    return page.evaluate("window.__panel.snapshot")


def drag_head(page, dx, dy, steps=12):
    p = page.evaluate("window.__robot.headScreenPoint()")
    page.mouse.move(p["x"], p["y"])
    page.mouse.down()
    for i in range(1, steps + 1):
        page.mouse.move(p["x"] + dx * i / steps, p["y"] + dy * i / steps)
        time.sleep(0.03)
    page.mouse.up()
    time.sleep(0.3)


# Screenshots wait forever while an MJPEG image keeps loading: swap in a still of the
# current frame first (this also closes the stream, as leaving the tab would)
FREEZE_CAMERA = """() => {
  const img = document.getElementById('camera-img');
  if (!img.naturalWidth) return 0;
  const c = document.createElement('canvas');
  c.width = img.naturalWidth; c.height = img.naturalHeight;
  c.getContext('2d').drawImage(img, 0, 0);
  img.onerror = null;
  img.src = c.toDataURL('image/jpeg');
  return c.width;
}"""


def shoot(page, path, full_page=False):
    page.evaluate(FREEZE_CAMERA)
    page.screenshot(path=path, full_page=full_page)


def send(page, obj):
    page.evaluate("(o) => window.__panel.ws.send(JSON.stringify(o))", obj)


# Average color of the bright pixels of the face canvas (the eyes) and their share
EYE_PIXELS = """() => {
  const c = document.getElementById('face-canvas');
  const d = c.getContext('2d').getImageData(0, 0, c.width, Math.round(c.height * 0.72)).data;
  let r = 0, g = 0, b = 0, n = 0;
  for (let i = 0; i < d.length; i += 4) {
    if (Math.max(d[i], d[i + 1], d[i + 2]) > 110) { r += d[i]; g += d[i + 1]; b += d[i + 2]; n++; }
  }
  return n ? { r: r / n, g: g / n, b: b / n, share: n / (d.length / 4), source: window.__face.source } : { share: 0, source: window.__face.source };
}"""


def hex_rgb(h):
    return int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)


def color_close(px, hex_color, tol=60):
    if not px.get("share"):
        return False
    r, g, b = hex_rgb(hex_color)
    return abs(px["r"] - r) < tol and abs(px["g"] - g) < tol and abs(px["b"] - b) < tol


def face_checks(page, out, check, prefix="laptop"):
    """Expression and eye color changes show in the page's face (2D card and 3D screen)."""
    page.click('.tabs button[data-tab="face"]')
    s = server_state(page)
    eye0 = s["face"]["eye"]
    emotion0 = s["app"]["emotion"]
    if eye0 is None:
        check(False, "face: robot face available (GUI)")
        return
    stream = s["face"]["stream"]
    time.sleep(2.5)
    src = page.evaluate("window.__face.source")
    check(src == ("robot" if stream else "drawn"), f"face: page shows {'the robot snapshots' if stream else 'its own drawing'} (source {src})")
    check(page.evaluate("!!(window.__robot.screenPanel && window.__robot.screenPanel.material.map)"), "face: 3D screen uses the face canvas")
    pill = page.inner_text("#face-source")
    print("     face source pill:", pill)

    def wait_color(expected, what, timeout=4.0):
        end = time.time() + timeout
        px = {}
        while time.time() < end:
            px = page.evaluate(EYE_PIXELS)
            if color_close(px, expected):
                break
            time.sleep(0.25)
        shown = {k: round(v) if isinstance(v, float) and k != "share" else v for k, v in px.items()}
        check(color_close(px, expected), f"face: {what}: eyes on the page are {expected} ({shown})")
        return px

    # Expression from the page -> button state and face shape. A real robot may be in a
    # conversation: wait until it is not speaking (a reply's emotion would replace ours),
    # and take the largest of a few samples (one may fall on a blink)
    def quiet(timeout=20.0):
        end = time.time() + timeout
        while time.time() < end and server_state(page)["app"]["device_state"] == "speaking":
            time.sleep(0.5)

    def lit():
        best = {}
        for _ in range(4):
            px = page.evaluate(EYE_PIXELS)
            if px.get("share", 0) > best.get("share", -1):
                best = px
            time.sleep(0.15)
        return best

    send(page, {"type": "eye_color", "color": "#ffffff", "mode": "fixed"})
    for _attempt in range(3):
        quiet()
        send(page, {"type": "emotion", "name": "neutral"})
        time.sleep(1.2)
        neutral = lit()
        send(page, {"type": "emotion", "name": "angry"})
        time.sleep(1.5)
        s = server_state(page)
        if s["app"]["emotion"] == "angry":
            break
        print("     the robot changed its face by itself (conversation), retrying:", s["app"]["emotion"])
    check(s["app"]["emotion"] == "angry", f"face: emotion angry reached the robot ({s['app']['emotion']})")
    on = page.evaluate("[...document.querySelectorAll('#emotions button.on')].map(b => b.dataset.name)")
    check(on == ["angry"], f"face: angry button selected ({on})")
    angry = lit()
    check(angry.get("share", 0) < neutral.get("share", 1) * 0.92, f"face: angry eyes are smaller than neutral (lit share {angry.get('share', 0):.3f} < {neutral.get('share', 0):.3f})")
    shoot(page, str(out / f"{prefix}_face_angry.png"))

    # Palette color
    page.click('#swatches button[data-color="#b46bff"]')
    wait_color("#b46bff", "purple swatch")
    check(server_state(page)["face"]["eye"]["color"] == "#b46bff", "face: robot eye color is purple")
    shoot(page, str(out / f"{prefix}_face_purple.png"))
    # Free color (validated and kept visible by the robot)
    send(page, {"type": "eye_color", "color": "#00ff88", "mode": "fixed"})
    wait_color("#00ff88", "free color #00ff88")
    # Bad values are refused
    r = page.evaluate("""() => new Promise((res) => {
      const ws = window.__panel.ws, id = 'bad' + Date.now();
      const h = (ev) => { if (typeof ev.data === 'string') { const m = JSON.parse(ev.data); if (m.id === id) { ws.removeEventListener('message', h); res(m); } } };
      ws.addEventListener('message', h);
      ws.send(JSON.stringify({ type: 'eye_color', color: 'red; drop', id }));
    })""")
    check(r.get("ok") is False, f"face: invalid color refused ({r.get('message')})")

    # Emotion mode: sad -> blue, angry -> red
    page.click('#eye-modes button[data-mode="emotion"]')
    send(page, {"type": "emotion", "name": "sad"})
    wait_color("#3d9bff", "emotion mode, sad = blue")
    send(page, {"type": "emotion", "name": "angry"})
    wait_color("#ff4d4d", "emotion mode, angry = red")
    shoot(page, str(out / f"{prefix}_face_emotion_angry.png"))

    # Rainbow: the color keeps changing
    page.click('#eye-modes button[data-mode="rainbow"]')
    send(page, {"type": "emotion", "name": "neutral"})
    time.sleep(1.5)
    a = page.evaluate(EYE_PIXELS)
    time.sleep(2.0)
    b = page.evaluate(EYE_PIXELS)
    moved = a.get("share") and b.get("share") and abs(a["r"] - b["r"]) + abs(a["g"] - b["g"]) + abs(a["b"] - b["b"]) > 25
    check(bool(moved), f"face: rainbow changes the color ({[round(a.get(k, 0)) for k in 'rgb']} -> {[round(b.get(k, 0)) for k in 'rgb']})")
    shoot(page, str(out / f"{prefix}_face_rainbow.png"))

    # Put back what the robot had
    send(page, {"type": "eye_color", "color": eye0["color"], "mode": eye0["mode"]})
    send(page, {"type": "emotion", "name": emotion0 if emotion0 in ("neutral",) else "neutral"})
    time.sleep(1.0)
    check(server_state(page)["face"]["eye"] == eye0, f"face: eye color restored to {eye0}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="web_panel_shots")
    ap.add_argument("--max-deg", type=float, default=30.0)
    ap.add_argument("--face-only", action="store_true", help="Only the face/eye color checks (no head moves)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    errors: list[str] = []
    m = args.max_deg

    def check(cond, text):
        print(("OK   " if cond else "FAIL ") + text, flush=True)
        if not cond:
            failures.append(text)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=["--use-angle=swiftshader", "--enable-unsafe-swiftshader"])

        # ----- Laptop -----
        ctx = browser.new_context(viewport={"width": 1440, "height": 900}, color_scheme="light")
        page = ctx.new_page()
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(args.url)
        wait_ready(page)
        s = server_state(page)
        check(s["type"] == "state", "WebSocket state received")
        if args.face_only:
            face_checks(page, out, check)
            ctx.close()
            browser.close()
            real_errors = [e for e in errors if "camera.mjpg" not in e]
            check(not real_errors, f"no console errors {json.dumps(real_errors, ensure_ascii=False)[:500]}")
            return 1 if failures else 0
        names = page.evaluate("[window.__robot.pan.name, window.__robot.tilt.name]")
        check(names == ["pan_pivot", "tilt_pivot"], f"GLB nodes found {names}")
        geo = page.evaluate("window.__robot.layout()")
        check(abs(geo["floorY"]) < 1, f"model stands on the floor (lowest point y {geo['floorY']:.1f})")
        if geo["baseTopY"] is not None:
            check(geo["headMinY"] > geo["baseTopY"], f"head is above the base (head bottom y {geo['headMinY']:.0f} > base top y {geo['baseTopY']:.0f})")
        print("     facing (x, z):", [round(v, 2) for v in geo["forward"]])
        send(page, {"type": "center"})
        time.sleep(1.5)
        page.screenshot(path=str(out / "laptop_control.png"))

        # Dragging is robot-centric from any view: left = the robot's own left (pan +)
        send(page, {"type": "tracking", "enabled": False})
        drag_head(page, -min(140, m / 0.4), 0)
        t = target(page)
        check(t["pan"] > 5, f"front view, drag left -> pan positive (robot's left): {t}")
        time.sleep(1.5)
        s = server_state(page)
        check(abs(s["servo"]["target"]["pan"] - t["pan"]) < 1.0, f"robot accepted target pan {s['servo']['target']}")
        if s["servo"].get("connected"):
            check(s["servo"].get("current") is not None and abs(s["servo"]["current"]["pan"] - t["pan"]) < 2, f"robot reports current {s['servo'].get('current')}")
        page.screenshot(path=str(out / "laptop_drag_left.png"))
        drag_head(page, 0, -min(60, m / 0.4 / 2))
        t2 = target(page)
        check(t2["tilt"] > t["tilt"] + 5, f"drag up -> tilt up: {t2}")
        page.screenshot(path=str(out / "laptop_drag_up.png"))

        # Back view: dragging left turns the robot to its own left (pan +)
        send(page, {"type": "center"})
        time.sleep(1.0)
        page.click("#btn-view-back")
        time.sleep(1.2)
        drag_head(page, min(140, m / 0.4), 0)
        t3 = target(page)
        check(t3["pan"] < -5, f"back view, drag right -> pan negative (robot's right): {t3}")
        page.screenshot(path=str(out / "laptop_back_drag_right.png"))
        page.click("#btn-view-front")
        send(page, {"type": "center"})
        time.sleep(1.5)

        # Angles beyond the limit are clamped on the robot (only where full swings are allowed)
        if m >= 90:
            send(page, {"type": "set_angles", "pan": 500, "tilt": -500})
            time.sleep(0.4)
            s = server_state(page)
            lim = s["servo"]["limits"]
            check(s["servo"]["target"] == {"pan": lim["pan"], "tilt": -lim["tilt"]}, f"out-of-range clamped {s['servo']['target']}")
            send(page, {"type": "center"})
            time.sleep(0.8)

        for tab in TABS:
            page.click(f'.tabs button[data-tab="{tab}"]')
            time.sleep(4.0 if tab == "camera" else 0.6)
            if tab == "camera":
                cam = page.evaluate("document.getElementById('camera-img').naturalWidth")
                avail = server_state(page)["camera"]["available"]
                check(cam > 0 or not avail, f"camera stream shows frames (width {cam}, available {avail})")
            shoot(page, str(out / f"laptop_tab_{tab}.png"))
        face_checks(page, out, check)
        page.click('.tabs button[data-tab="control"]')
        ctx.close()

        # ----- Phone (dark) -----
        ctx = browser.new_context(
            viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True,
            color_scheme="dark",
        )
        page = ctx.new_page()
        page.on("console", lambda msg: msg.type == "error" and errors.append(msg.text))
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.goto(args.url)
        wait_ready(page)
        time.sleep(1.0)
        page.screenshot(path=str(out / "phone_top.png"))
        page.screenshot(path=str(out / "phone_full.png"), full_page=True)
        for tab in TABS:
            page.click(f'.tabs button[data-tab="{tab}"]')
            time.sleep(4.0 if tab == "camera" else 0.5)
            shoot(page, str(out / f"phone_tab_{tab}.png"), full_page=True)
        face_checks(page, out, check, prefix="phone")
        ctx.close()
        browser.close()

    real_errors = [e for e in errors if "camera.mjpg" not in e]
    check(not real_errors, f"no console errors {json.dumps(real_errors, ensure_ascii=False)[:500]}")
    print(f"Screenshots in {out.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
