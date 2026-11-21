#!/usr/bin/env python3
"""Browser check of the web panel with Playwright (test tool, not an app dependency).

    pip install playwright && python -m playwright install chromium
    python scripts/web_panel_check.py http://127.0.0.1:8080 --out shots/
    python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --max-deg 20

Loads the page at laptop and phone sizes, fails on console errors, checks that the 3D
model loaded, drags the robot's head and checks the angle changed the right way, and
saves screenshots of every tab. --max-deg bounds every angle it sends (real hardware).
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", default="web_panel_shots")
    ap.add_argument("--max-deg", type=float, default=30.0)
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
        ctx.close()
        browser.close()

    real_errors = [e for e in errors if "camera.mjpg" not in e]
    check(not real_errors, f"no console errors {json.dumps(real_errors, ensure_ascii=False)[:500]}")
    print(f"Screenshots in {out.resolve()}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
