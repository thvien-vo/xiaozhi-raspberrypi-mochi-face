// The robot's screen on the page: a canvas laid out like the 7" screen (1024x600, face in
// the top 72%). It shows the snapshots the robot sends (exactly what its face draws) and,
// when none arrive (no GUI, dev server, FACE_STREAM_FPS=0), draws the face itself from
// the same eye presets the robot uses (/face_presets.js is qml/face/FacePresets.js), the
// same expression and the same eye color. The 3D model uses this canvas as its screen.

const SCREEN_W = 512, SCREEN_H = 300;     // 1024x600 at half size
const FACE_H = Math.round(SCREEN_H * 0.72); // RobotFace's height in FaceWindow.qml
const FRAME_STALE_MS = 2000;
const FALLBACK_FPS = 20;

export class FaceScreen {
  constructor(canvas, { onDraw } = {}) {
    this.canvas = canvas;
    canvas.width = SCREEN_W; canvas.height = SCREEN_H;
    this.ctx = canvas.getContext("2d");
    this.onDraw = onDraw;
    this.lastFrame = 0;
    this.presets = null;
    this.face = { emotion: "neutral", visual: "idle", activity: "awake" };
    this.eye = { color: "#ffffff", mode: "fixed" };
    this.constants = { emotion_colors: {}, rainbow_period_s: 12, rainbow_saturation: 0.7 };
    this.blink = 1; this.nextBlink = performance.now() + 2500;
    this._timer = 0;
    this.source = "none";  // "robot" (snapshots) or "drawn" (fallback)
    import("/face_presets.js").then((m) => { this.presets = m; this._kick(); })
      .catch((e) => console.warn("face presets unavailable", e));
    this._clear();
  }

  setConstants(c) { if (c) Object.assign(this.constants, c); this._kick(); }

  setState(face, eye) {
    if (face) this.face = face;
    if (eye) this.eye = eye;
    this._kick();
  }

  // A snapshot from the robot (ImageBitmap of the face item)
  setFrame(bitmap) {
    this.lastFrame = performance.now();
    this.source = "robot";
    const ctx = this.ctx;
    ctx.fillStyle = "#000"; ctx.fillRect(0, 0, SCREEN_W, SCREEN_H);
    ctx.drawImage(bitmap, 0, 0, SCREEN_W, FACE_H);
    bitmap.close && bitmap.close();
    this.onDraw && this.onDraw();
    this._kick();
  }

  get live() { return performance.now() - this.lastFrame < FRAME_STALE_MS; }

  // The eye color right now (same rule as RobotFace.qml / face_color.effective_color)
  currentColor(expression) {
    const e = this.eye || {};
    if (e.mode === "rainbow") {
      const c = this.constants;
      return hsvToHex((Date.now() / 1000 / c.rainbow_period_s) % 1, c.rainbow_saturation, 1);
    }
    if (e.mode === "emotion") return this.constants.emotion_colors[expression] || e.color;
    return e.color || "#ffffff";
  }

  // Fallback drawing runs only while no snapshots arrive
  _kick() {
    if (this._timer) return;
    this._timer = setTimeout(() => { this._timer = 0; this._tick(); }, this.live ? FRAME_STALE_MS : 1000 / FALLBACK_FPS);
  }

  _tick() {
    if (this.live) { this._kick(); return; }
    if (document.hidden) { this._timer = setTimeout(() => { this._timer = 0; this._tick(); }, 500); return; }
    this.source = "drawn";
    this._drawFallback();
    this.onDraw && this.onDraw();
    this._kick();
  }

  _clear() {
    this.ctx.fillStyle = "#000";
    this.ctx.fillRect(0, 0, SCREEN_W, SCREEN_H);
  }

  _drawFallback() {
    const ctx = this.ctx;
    this._clear();
    if (!this.presets) return;
    const now = performance.now();
    // Blink like the robot: every 2-6 s, ~190 ms
    if (now > this.nextBlink) {
      const t = now - this.nextBlink;
      this.blink = t < 70 ? 1 - 0.95 * (t / 70) : t < 190 ? 0.05 + 0.95 * ((t - 70) / 120) : 1;
      if (t >= 190) { this.blink = 1; this.nextBlink = now + 2000 + Math.random() * 4000; }
    }
    const f = this.face;
    const name = this.presets.activeName(f.emotion, f.visual, f.activity);
    const pose = this.presets.compose(name, f.visual);
    const color = this.currentColor(name);
    const W = SCREEN_W, H = FACE_H;
    const unit = Math.min(W / 1024, H / 432);
    const ew = 190 * unit, eh = 230 * unit, gap = 112 * unit;
    const breath = 1 + 0.018 * Math.sin(now / 2400 * Math.PI);
    ctx.save();
    ctx.translate(W / 2, H / 2); ctx.scale(breath, breath); ctx.translate(-W / 2, -H / 2);
    for (const [side, shape] of [[-1, pose.left], [1, pose.right]]) {
      const cx = W / 2 + side * (gap + ew) / 2 + pose.gazeX * ew;
      const cy = H / 2 + pose.gazeY * eh + shape.dy * eh;
      drawEye(ctx, cx, cy, ew, eh, shape, side, this.blink, color);
    }
    if (pose.overlay === "zzz") {
      ctx.fillStyle = color; ctx.globalAlpha = 0.8;
      ctx.font = `bold ${Math.round(50 * unit)}px sans-serif`;
      ctx.fillText("z z", W / 2 + gap / 2 + ew, H / 2 - eh * 0.4);
      ctx.globalAlpha = 1;
    }
    ctx.restore();
  }
}

function drawEye(ctx, cx, cy, baseW, baseH, s, side, blink, color) {
  const w = baseW * s.w, h = baseH * s.h;
  ctx.save();
  ctx.translate(cx, cy);
  ctx.rotate(-side * s.tilt * Math.PI / 180);
  ctx.scale(1, blink);
  ctx.translate(-w / 2, -h / 2);
  if (s.body > 0.01) {
    ctx.globalAlpha = s.body;
    ctx.fillStyle = color;
    roundRect(ctx, 0, 0, w, h, Math.min(w, h) / 2 * Math.max(0, Math.min(1, s.r)));
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  ctx.fillStyle = "#000";
  if (s.lidTop > 0.002) {
    const lw = w * 1.7, lh = h * 1.3;
    ctx.save();
    ctx.translate(w / 2, h * s.lidTop);                  // Bottom center of the lid
    ctx.rotate(-side * s.lidTopTilt * Math.PI / 180);
    ctx.fillRect(-lw / 2, -lh, lw, lh);
    ctx.restore();
  }
  if (s.lidBot > 0.002) {
    const lw = w * 1.3;
    roundRect(ctx, (w - lw) / 2, h * (1 - s.lidBot), lw, lw, lw / 2 * s.lidBotRound);
    ctx.fill();
  }
  const size = Math.min(baseW, baseH);
  if (s.heart > 0.01) {
    const hw = size * 1.25, x0 = w / 2 - hw / 2, y0 = h / 2 - hw / 2;
    ctx.globalAlpha = s.heart;
    ctx.fillStyle = color.toLowerCase() === "#ffffff" ? "#ff4d6d" : color;
    ctx.beginPath();
    const P = (a, b) => [x0 + a * hw, y0 + b * hw];
    ctx.moveTo(...P(0.5, 0.9));
    ctx.bezierCurveTo(...P(0.02, 0.58), ...P(0.02, 0.12), ...P(0.3, 0.12));
    ctx.bezierCurveTo(...P(0.42, 0.12), ...P(0.48, 0.2), ...P(0.5, 0.28));
    ctx.bezierCurveTo(...P(0.52, 0.2), ...P(0.58, 0.12), ...P(0.7, 0.12));
    ctx.bezierCurveTo(...P(0.98, 0.12), ...P(0.98, 0.58), ...P(0.5, 0.9));
    ctx.fill();
    ctx.globalAlpha = 1;
  }
  if (s.spiral > 0.01) {
    const c = size / 2, rot = (performance.now() / 2600) * Math.PI * 2 * side;
    ctx.globalAlpha = s.spiral;
    ctx.strokeStyle = color; ctx.lineWidth = size * 0.075; ctx.lineCap = "round";
    ctx.beginPath();
    for (let a = 0; a < Math.PI * 6.2; a += 0.15) {
      const r = c * 0.9 * a / (Math.PI * 6.2);
      ctx.lineTo(w / 2 + r * Math.cos(a * side + rot), h / 2 + r * Math.sin(a * side + rot));
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }
  ctx.restore();
}

function roundRect(ctx, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h / 2));
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

export function hsvToHex(h, s, v) {
  const i = Math.floor(h * 6), f = h * 6 - i;
  const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  const [r, g, b] = [[v, t, p], [q, v, p], [p, v, t], [p, q, v], [t, p, v], [v, p, q]][i % 6];
  return "#" + [r, g, b].map((c) => Math.round(c * 255).toString(16).padStart(2, "0")).join("");
}
