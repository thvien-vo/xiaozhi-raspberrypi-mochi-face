// Xiaozhi web panel: WebSocket client, controls and the 3D view.
import { Robot3D } from "./robot3d.js";

const $ = (id) => document.getElementById(id);
const SEND_INTERVAL_MS = 50;      // Angle commands while dragging: at most 20 per second
const LOCAL_TARGET_MS = 600;      // Prefer our own target this long after the last input

const EMOTIONS = {
  neutral: ["😐", "Bình thường"], happy: ["🙂", "Vui"], laughing: ["😆", "Cười lớn"],
  funny: ["😂", "Buồn cười"], sad: ["😔", "Buồn"], angry: ["😠", "Giận"], crying: ["😭", "Khóc"],
  loving: ["😍", "Yêu thương"], embarrassed: ["😳", "Ngượng"], surprised: ["😯", "Ngạc nhiên"],
  shocked: ["😱", "Sốc"], thinking: ["🤔", "Suy nghĩ"], winking: ["😉", "Nháy mắt"],
  cool: ["😎", "Ngầu"], relaxed: ["😌", "Thư giãn"], delicious: ["🤤", "Thèm"],
  kissy: ["😘", "Hôn"], confident: ["😏", "Tự tin"], sleepy: ["😴", "Buồn ngủ"],
  silly: ["😜", "Tinh nghịch"], confused: ["🙄", "Bối rối"],
};
const GESTURE_ICONS = { nod: "🙇", shake: "🙅", look_around: "👀", surprised: "😮", dance: "💃" };

const state = {
  ws: null, connected: false, snapshot: null, hello: null,
  target: { pan: 0, tilt: 0 }, localUntil: 0,
  pending: null, lastSend: 0, sendTimer: null, msgId: 0, waiters: new Map(),
  chatIds: new Set(),
};

// ---------- Toasts ----------
function toast(text, bad = false) {
  if (!text) return;
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = text;
  $("toasts").append(el);
  setTimeout(() => el.remove(), 3200);
}

// ---------- WebSocket ----------
let retryMs = 500;
function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => { state.connected = true; retryMs = 500; renderChips(); };
  ws.onclose = (ev) => {
    state.connected = false; renderChips();
    if (ev.code === 1008 || ev.code === 4401) { location.href = "/login"; return; }
    setTimeout(connect, retryMs);
    retryMs = Math.min(retryMs * 2, 5000);
  };
  ws.onerror = () => {};
  ws.onmessage = (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === "state") onState(msg);
    else if (msg.type === "hello") { state.hello = msg; renderTiltMode(); }
    else if (msg.type === "chat") onChat(msg.messages || []);
    else if (msg.type === "result") onResult(msg);
  };
}

function send(obj, { wantReply = false } = {}) {
  const ws = state.ws;
  if (!ws || ws.readyState !== WebSocket.OPEN) { if (wantReply) toast("Chưa kết nối tới robot", true); return Promise.resolve(null); }
  if (!wantReply) { ws.send(JSON.stringify(obj)); return Promise.resolve(null); }
  const id = ++state.msgId;
  ws.send(JSON.stringify({ ...obj, id }));
  return new Promise((resolve) => {
    state.waiters.set(id, resolve);
    setTimeout(() => { if (state.waiters.delete(id)) resolve(null); }, 5000);
  });
}

function onResult(msg) {
  const waiter = msg.id != null && state.waiters.get(msg.id);
  if (waiter) { state.waiters.delete(msg.id); waiter(msg); }
  if (!msg.ok && !msg.rate_limited) toast(msg.message || "Lỗi", true);
  else if (waiter && msg.message) toast(msg.message);
}

// Commands that answer with a toast
async function command(obj) { return send(obj, { wantReply: true }); }

// ---------- Angles ----------
function clampAngle(v, lim) { return Math.max(-lim, Math.min(lim, v)); }
function limits() { return state.snapshot?.servo?.limits || { pan: 90, tilt: 45 }; }

// Throttled: the latest target always gets sent, at most every SEND_INTERVAL_MS
function setTarget(pan, tilt) {
  const lim = limits();
  pan = clampAngle(pan, lim.pan); tilt = clampAngle(tilt, lim.tilt);
  state.target = { pan, tilt };
  state.localUntil = performance.now() + LOCAL_TARGET_MS;
  robot.setTarget(pan, tilt);
  renderAngles();
  state.pending = { pan: Math.round(pan * 10) / 10, tilt: Math.round(tilt * 10) / 10 };
  const wait = state.lastSend + SEND_INTERVAL_MS - performance.now();
  if (wait <= 0) flushTarget();
  else if (!state.sendTimer) state.sendTimer = setTimeout(flushTarget, wait);
}
function flushTarget() {
  state.sendTimer = null;
  if (!state.pending) return;
  send({ type: "set_angles", ...state.pending });
  state.pending = null;
  state.lastSend = performance.now();
}
function center() { setTarget(0, 0); }

// ---------- State from the robot ----------
function onState(s) {
  state.snapshot = s;
  const servo = s.servo || {};
  if (servo.limits) robot.setLimits(servo.limits);
  if (servo.target && performance.now() > state.localUntil && !robot.dragging && !stick.active) {
    state.target = { ...servo.target };
    robot.setTarget(servo.target.pan, servo.target.tilt);
  }
  const real = servo.current || servo.acked;
  if (real) robot.setReal(real.pan, real.tilt);
  renderChips(); renderAngles(); renderTracking(); renderMotion(); renderFace(); renderChatState(); renderCameraAvailability();
}

function renderChips() {
  const s = state.snapshot;
  const set = (id, tone, text, hidden = false) => {
    const el = $(id); el.dataset.tone = tone; el.querySelector("b").textContent = text; el.hidden = hidden;
  };
  set("chip-ws", state.connected ? "good" : "bad", state.connected ? "Đã kết nối" : "Mất kết nối");
  if (!s) return;
  const sv = s.servo || {};
  if (!sv.enabled) set("chip-servo", "muted", "Servo tắt");
  else set("chip-servo", sv.connected ? "good" : "warn", sv.connected ? "Servo OK" : "Servo chưa nối");
  const app = s.app || {};
  const tone = { listening: "info", speaking: "accent", idle: "muted" }[app.device_state] || "muted";
  set("chip-app", tone, app.device_state_label || app.device_state || "App");
  const owners = { web: "Web đang điều khiển", voice: "Lệnh giọng nói", tracking: "Bám người" };
  let owner = sv.owner ? owners[sv.owner] : null;
  if (!owner && s.tracking?.enabled && !s.tracking.holding) owner = "Bám người";
  if (s.motion?.playing) owner = "Đang chạy động tác";
  set("chip-owner", "accent", owner || "", !owner);
}

function fmtPan(v) { const r = Math.round(v); return r === 0 ? "Giữa" : r > 0 ? `Trái ${r}°` : `Phải ${-r}°`; }
function fmtTilt(v) { const r = Math.round(v); return r === 0 ? "Giữa" : r > 0 ? `Ngẩng ${r}°` : `Cúi ${-r}°`; }

function renderAngles() {
  const t = state.target;
  const servo = state.snapshot?.servo || {};
  const real = servo.current || servo.acked;
  const lim = limits();
  const slPan = $("sl-pan"), slTilt = $("sl-tilt");
  slPan.min = -lim.pan; slPan.max = lim.pan; slTilt.min = -lim.tilt; slTilt.max = lim.tilt;
  if (document.activeElement !== slPan || !slPan.matches(":active")) slPan.value = -t.pan;
  if (document.activeElement !== slTilt || !slTilt.matches(":active")) slTilt.value = t.tilt;
  $("out-pan").textContent = fmtPan(t.pan);
  $("out-tilt").textContent = fmtTilt(t.tilt);
  const shown = real || t;
  $("hud-pan").textContent = `${Math.round(shown.pan)}°`;
  $("hud-tilt").textContent = `${Math.round(shown.tilt)}°`;
  const differ = real && (Math.abs(real.pan - t.pan) > 0.8 || Math.abs(real.tilt - t.tilt) > 0.8);
  $("hud-pan-t").textContent = differ ? `→ ${Math.round(t.pan)}°` : "";
  $("hud-tilt-t").textContent = differ ? `→ ${Math.round(t.tilt)}°` : "";
  let note = "";
  if (servo.enabled === false) note = "Servo đang tắt trong cấu hình (SERVO.ENABLED).";
  else if (servo.enabled && !servo.connected) note = "Chưa nối bo Yolo UNO: mô hình chỉ hiện góc đích.";
  else if (servo.last_reply) note = `Bo mạch trả: ${servo.last_reply}`;
  $("servo-note").textContent = note;
}

function renderTracking() {
  const tr = state.snapshot?.tracking || {};
  const sw = $("sw-track");
  sw.disabled = !tr.available;
  sw.checked = !!tr.enabled;
  for (const [id, out, key, digits] of [["sl-kpp", "out-kpp", "kp_pan", 1], ["sl-kpt", "out-kpt", "kp_tilt", 1], ["sl-db", "out-db", "deadband", 2]]) {
    const el = $(id);
    el.disabled = tr[key] == null;
    if (tr[key] != null && !el.matches(":active") && document.activeElement !== el) el.value = tr[key];
    $(out).textContent = tr[key] == null ? "-" : Number(el.value).toFixed(digits);
  }
  let note = "Dùng YOLO trên webcam, quay đầu về phía người lớn nhất trong khung hình.";
  if (!tr.available) note = "Không có bám người: cần VISION.ENABLED và servo.";
  else if (tr.enabled && tr.holding) note = "Tạm dừng vì vừa điều khiển tay; sẽ bám lại sau vài giây.";
  else if (tr.enabled) note = "Đang bám theo người.";
  $("track-note").textContent = note;
}

// ---------- Motion ----------
function renderMotion() {
  const m = state.snapshot?.motion;
  if (!m) return;
  const box = $("gestures");
  if (!box.childElementCount) {
    for (const g of m.gestures || []) {
      const b = document.createElement("button");
      b.className = "btn"; b.dataset.name = g.name;
      b.innerHTML = `<span class="ico">${GESTURE_ICONS[g.name] || "🤖"}</span><span></span>`;
      b.lastChild.textContent = g.label;
      b.onclick = () => command({ type: "gesture", name: g.name });
      box.append(b);
    }
  }
  for (const b of box.children) b.classList.toggle("playing", m.playing === b.dataset.name);
  $("btn-stop-motion").disabled = !m.playing;
  const prog = $("motion-progress");
  prog.hidden = !m.playing;
  if (m.playing && m.duration) prog.firstElementChild.style.width = `${Math.min(100, (m.progress / m.duration) * 100)}%`;

  const rec = m.recording;
  $("btn-rec").classList.toggle("on", rec);
  $("btn-rec").textContent = rec ? "Đang ghi…" : "Ghi";
  $("btn-rec").disabled = rec;
  $("btn-rec-save").disabled = !rec;
  $("btn-rec-cancel").disabled = !rec;
  $("rec-time").hidden = !rec;
  $("rec-time").textContent = `● ${m.record_elapsed.toFixed(1)} s`;

  const list = $("motion-list");
  const key = JSON.stringify((m.library || []).map((x) => [x.name, x.duration]));
  if (list.dataset.key === key) {
    for (const li of list.children) li.classList.toggle("playing", li.dataset.name === m.playing);
    return;
  }
  list.dataset.key = key;
  list.replaceChildren();
  if (!m.library?.length) {
    const li = document.createElement("li"); li.className = "empty"; li.textContent = "Chưa có động tác nào."; list.append(li); return;
  }
  for (const item of m.library) {
    const li = document.createElement("li");
    li.dataset.name = item.name;
    const name = document.createElement("span"); name.className = "name"; name.textContent = item.name;
    const meta = document.createElement("span"); meta.className = "meta"; meta.textContent = `${item.duration.toFixed(1)} s`;
    const play = document.createElement("button"); play.className = "btn small primary"; play.textContent = "Phát";
    play.onclick = () => command({ type: "play_motion", name: item.name });
    const del = document.createElement("button"); del.className = "btn small"; del.textContent = "Xóa";
    del.onclick = () => {
      if (del.dataset.armed) { command({ type: "delete_motion", name: item.name }); return; }
      del.dataset.armed = "1"; del.textContent = "Chắc chưa?"; del.classList.add("danger");
      setTimeout(() => { delete del.dataset.armed; del.textContent = "Xóa"; del.classList.remove("danger"); }, 2500);
    };
    li.append(name, meta, play, del);
    list.append(li);
  }
}

// ---------- Face ----------
function renderFace() {
  const app = state.snapshot?.app;
  if (!app) return;
  const box = $("emotions");
  const names = app.emotions?.length ? app.emotions : Object.keys(EMOTIONS);
  if (box.dataset.names !== names.join()) {
    box.dataset.names = names.join();
    box.replaceChildren();
    for (const n of names) {
      const [ico, label] = EMOTIONS[n] || ["🙂", n];
      const b = document.createElement("button");
      b.dataset.name = n; b.title = n;
      b.innerHTML = `<span class="ico">${ico}</span><span></span>`;
      b.lastChild.textContent = label;
      b.onclick = () => command({ type: "emotion", name: n });
      box.append(b);
    }
  }
  for (const b of box.children) { b.classList.toggle("on", b.dataset.name === app.emotion); b.disabled = !app.face_available; }
  $("face-current").textContent = (EMOTIONS[app.emotion]?.[1]) || app.emotion;
  $("face-note").textContent = app.face_available
    ? "Biểu cảm giữ đến khi robot chuyển trạng thái (nghe, nói), giống khi server gửi biểu cảm."
    : "Mặt robot không chạy (app không ở chế độ GUI).";
  const sw = $("sw-auto");
  sw.checked = !!app.auto_mode;
  sw.disabled = app.always_listen || app.auto_mode == null;
  $("auto-note").textContent = app.always_listen
    ? "FACE.ALWAYS_LISTEN đang bật nên robot luôn ở chế độ tự động (nghe liên tục)."
    : "FACE.AUTO_MODE: chạm màn hình một lần để bắt đầu cuộc trò chuyện nghe liên tục; tắt thì chạm để nói (bấm-giữ).";
  $("btn-wake").disabled = !app.face_available;
}

// ---------- Chat ----------
function onChat(messages) {
  const box = $("chat");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  for (const m of messages) {
    if (state.chatIds.has(m.id)) continue;
    state.chatIds.add(m.id);
    box.querySelector(".empty")?.remove();
    const el = document.createElement("div");
    el.className = `msg ${m.role === "user" ? "user" : "robot"}`;
    el.textContent = m.text;
    const t = document.createElement("time");
    t.textContent = new Date(m.time * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    el.append(t);
    box.append(el);
  }
  while (box.childElementCount > 120) box.firstElementChild.remove();
  if (atBottom) box.scrollTop = box.scrollHeight;
}
function renderChatState() {
  const app = state.snapshot?.app || {};
  $("chat-state").textContent = app.device_state_label || "-";
  const ok = app.text_supported;
  $("chat-input").disabled = !ok;
  $("chat-form").querySelector("button").disabled = !ok;
  $("chat-note").textContent = !ok ? "App này không gửi được tin nhắn chữ."
    : app.protocol_connected === false ? "Robot chưa nối với server; tin nhắn sẽ gửi khi nối lại."
    : "Tin nhắn được gửi như câu bạn nói. Robot trả lời bằng giọng (cần loa) và chữ ở đây.";
}

// ---------- Camera ----------
let cameraOn = false;
function renderCameraAvailability() {
  const ok = state.snapshot?.camera?.available;
  $("sw-cam").disabled = !ok;
  if (!ok) { stopCamera(); $("camera-msg").hidden = false; $("camera-msg").textContent = "Robot không có camera (VISION/CAMERA chưa bật)."; }
  else updateCamera();
}
function updateCamera() {
  const visible = currentTab === "camera" && $("sw-cam").checked && state.snapshot?.camera?.available;
  if (visible && !cameraOn) {
    cameraOn = true;
    const img = $("camera-img");
    $("camera-msg").hidden = false; $("camera-msg").textContent = "Đang mở camera…";
    img.onload = () => { $("camera-msg").hidden = true; };
    img.onerror = () => { cameraOn = false; img.removeAttribute("src"); $("camera-msg").hidden = false; $("camera-msg").textContent = "Không xem được camera. Thử bật lại."; };
    img.src = `/camera.mjpg?t=${Date.now()}`;
  } else if (!visible && cameraOn) stopCamera();
}
function stopCamera() {
  cameraOn = false;
  const img = $("camera-img");
  img.onerror = null;
  img.removeAttribute("src");  // Closes the stream: the robot stops encoding
  $("camera-msg").hidden = false;
  $("camera-msg").textContent = "Camera đang tắt";
}

// ---------- Tabs ----------
let currentTab = "control";
function showTab(name) {
  currentTab = name;
  for (const b of document.querySelectorAll(".tabs button")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
  for (const p of document.querySelectorAll(".tabpage")) p.hidden = p.dataset.page !== name;
  try { localStorage.setItem("xz-tab", name); } catch {}
  updateCamera();
  if (name === "chat") $("chat").scrollTop = $("chat").scrollHeight;
}

// ---------- Joystick (rate control) ----------
const stick = { active: false, x: 0, y: 0, raf: 0, last: 0 };
function initStick() {
  const el = $("stick"), knob = $("knob");
  const radius = () => el.clientWidth / 2 - 32;
  const move = (ev) => {
    const r = el.getBoundingClientRect();
    let dx = ev.clientX - (r.left + r.width / 2), dy = ev.clientY - (r.top + r.height / 2);
    const max = radius(), d = Math.hypot(dx, dy);
    if (d > max) { dx *= max / d; dy *= max / d; }
    knob.style.transform = `translate(${dx}px, ${dy}px)`;
    stick.x = dx / max; stick.y = dy / max;
  };
  el.addEventListener("pointerdown", (ev) => {
    el.setPointerCapture(ev.pointerId); stick.active = true; el.classList.add("active");
    stick.last = performance.now(); move(ev); stick.raf = requestAnimationFrame(stickLoop);
  });
  el.addEventListener("pointermove", (ev) => { if (stick.active) move(ev); });
  const end = () => {
    stick.active = false; stick.x = stick.y = 0; el.classList.remove("active");
    knob.style.transform = ""; cancelAnimationFrame(stick.raf);
  };
  el.addEventListener("pointerup", end);
  el.addEventListener("pointercancel", end);
}
function stickLoop(now) {
  if (!stick.active) return;
  const dt = Math.min(0.1, (now - stick.last) / 1000);
  stick.last = now;
  const curve = (v) => Math.sign(v) * Math.pow(Math.abs(v), 1.6);
  // Left = the robot's left (pan +), up = look up (tilt +)
  const dp = -curve(stick.x) * 90 * dt, dt2 = -curve(stick.y) * 60 * dt;
  if (Math.abs(dp) > 0.001 || Math.abs(dt2) > 0.001) setTarget(state.target.pan + dp, state.target.tilt + dt2);
  stick.raf = requestAnimationFrame(stickLoop);
}

// ---------- Tilt-the-phone mode ----------
const tilt = { on: false, base: null, handler: null };
function tiltSupported() { return window.isSecureContext && "DeviceOrientationEvent" in window; }
function renderTiltMode() {
  const help = $("tilt-help");
  if (tiltSupported()) { $("tilt-ready").hidden = false; help.hidden = true; return; }
  $("tilt-ready").hidden = true; help.hidden = false;
  const port = state.hello?.https_port;
  if (!window.isSecureContext && port) {
    const url = `https://${location.hostname}:${port}/`;
    help.innerHTML = `Trình duyệt chỉ cho đọc cảm biến nghiêng trên HTTPS. Mở <a href="${url}">${url}</a> (chứng chỉ tự ký: chọn "Nâng cao" → "Tiếp tục").`;
  } else if (!window.isSecureContext) {
    help.innerHTML = "Chế độ này cần HTTPS. Bật <code>WEB.HTTPS_ENABLED</code> trong cấu hình robot rồi mở trang bằng https (xem docs/web_panel.md).";
  } else {
    help.textContent = "Thiết bị này không có cảm biến nghiêng.";
  }
}
async function startTilt() {
  if (typeof DeviceOrientationEvent.requestPermission === "function") {
    try {
      if ((await DeviceOrientationEvent.requestPermission()) !== "granted") { toast("Chưa được cấp quyền cảm biến", true); return; }
    } catch { toast("Không xin được quyền cảm biến", true); return; }
  }
  tilt.base = null;
  let got = false;
  tilt.handler = (ev) => {
    if (ev.beta == null || ev.gamma == null) return;
    got = true;
    if (!tilt.base) tilt.base = { beta: ev.beta, gamma: ev.gamma, pan: state.target.pan, tilt: state.target.tilt };
    const b = tilt.base;
    // Lean the phone left -> robot turns to its left; tip the top away -> look up
    const pan = b.pan - (ev.gamma - b.gamma) * 2;
    const tl = b.tilt + (b.beta - ev.beta) * 1.2;
    $("tilt-readout").textContent = `nghiêng ${(ev.gamma - b.gamma).toFixed(0)}°, ngả ${(b.beta - ev.beta).toFixed(0)}° → ${fmtPan(pan)}, ${fmtTilt(tl)}`;
    setTarget(pan, tl);
  };
  window.addEventListener("deviceorientation", tilt.handler);
  tilt.on = true;
  $("btn-tilt-start").disabled = true; $("btn-tilt-stop").disabled = false;
  setTimeout(() => { if (tilt.on && !got) { toast("Không nhận được dữ liệu cảm biến nghiêng", true); stopTilt(); } }, 1500);
}
function stopTilt() {
  if (tilt.handler) window.removeEventListener("deviceorientation", tilt.handler);
  tilt.on = false; tilt.handler = null;
  $("btn-tilt-start").disabled = false; $("btn-tilt-stop").disabled = true;
}

// ---------- 3D ----------
const robot = new Robot3D($("scene"), {
  onDrag: (pan, tl) => { setTarget(pan, tl); $("stage-hint").classList.add("fade"); },
  onDragEnd: (pan, tl) => setTarget(pan, tl),
  onLoaded: () => { $("stage-loading").remove(); },
  onError: () => { $("stage-loading").textContent = "Không tải được mô hình 3D"; },
});
window.__robot = robot;  // For automated checks
window.__panel = state;

// ---------- Wiring ----------
function bind() {
  for (const b of document.querySelectorAll(".tabs button")) b.onclick = () => showTab(b.dataset.tab);
  $("btn-center").onclick = center;
  $("btn-center-stage").onclick = center;
  $("btn-view-front").onclick = () => robot.setView("front");
  $("btn-view-back").onclick = () => robot.setView("back");
  $("btn-view-top").onclick = () => robot.setView("top");
  $("sl-pan").oninput = (e) => setTarget(-Number(e.target.value), state.target.tilt);
  $("sl-tilt").oninput = (e) => setTarget(state.target.pan, Number(e.target.value));

  $("sw-track").onchange = (e) => command({ type: "tracking", enabled: e.target.checked });
  const params = (save) => ({
    type: "tracking_params", save,
    kp_pan: Number($("sl-kpp").value), kp_tilt: Number($("sl-kpt").value), deadband: Number($("sl-db").value),
  });
  let paramTimer = 0;
  for (const id of ["sl-kpp", "sl-kpt", "sl-db"]) {
    $(id).oninput = () => {
      renderTracking();
      clearTimeout(paramTimer);
      paramTimer = setTimeout(() => send(params(false)), 120);
    };
  }
  $("btn-track-save").onclick = () => command(params(true));

  for (const b of document.querySelectorAll(".seg button")) {
    b.onclick = () => {
      for (const o of document.querySelectorAll(".seg button")) o.classList.toggle("on", o === b);
      const tiltMode = b.dataset.mode === "tilt";
      $("stick-wrap").hidden = tiltMode; $("tiltmode").hidden = !tiltMode;
      if (!tiltMode) stopTilt();
      renderTiltMode();
    };
  }
  $("btn-tilt-start").onclick = startTilt;
  $("btn-tilt-stop").onclick = stopTilt;

  $("btn-stop-motion").onclick = () => send({ type: "stop_motion" });
  $("btn-rec").onclick = () => command({ type: "record_start" });
  $("btn-rec-cancel").onclick = () => command({ type: "record_cancel" });
  $("btn-rec-save").onclick = async () => {
    const name = $("rec-name").value.trim() || `Động tác ${new Date().toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }).replace(":", "h")}`;
    const r = await command({ type: "record_stop", name });
    if (r?.ok) $("rec-name").value = "";
  };

  $("sw-cam").onchange = updateCamera;
  $("sw-auto").onchange = (e) => command({ type: "auto_mode", enabled: e.target.checked });
  $("btn-wake").onclick = () => command({ type: "face_wake" });

  $("chat-form").onsubmit = async (e) => {
    e.preventDefault();
    const input = $("chat-input");
    const text = input.value.trim();
    if (!text) return;
    const r = await command({ type: "send_text", text });
    if (r?.ok) input.value = "";
  };

  document.addEventListener("visibilitychange", () => { if (document.hidden) stopCamera(); else updateCamera(); });
  initStick();
  renderTiltMode();
  setTimeout(() => $("stage-hint").classList.add("fade"), 8000);
  const bar = document.querySelector(".topbar");
  new ResizeObserver(() => document.documentElement.style.setProperty("--topbar-h", `${bar.offsetHeight}px`)).observe(bar);
  let saved = "control";
  try { saved = localStorage.getItem("xz-tab") || "control"; } catch {}
  showTab(document.querySelector(`.tabs button[data-tab="${saved}"]`) ? saved : "control");
}

bind();
connect();
