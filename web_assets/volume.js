// Speaker volume: the master slider and mute button in the top bar (every tab), and the
// "Âm thanh" panel with one slider per channel. The robot owns the numbers; sliders send
// them (at most one message per SEND_MS while dragging, a saved one on release) and show
// what the robot reports, so web, voice commands and the music tab stay in step.
const $ = (id) => document.getElementById(id);
const SEND_MS = 80;
const HOLD_MS = 1200;  // After touching a slider, ignore the robot's number this long

const CHANNELS = [
  { key: "master", label: "Tổng", note: "Mọi âm thanh ra loa" },
  { key: "voice", label: "Giọng robot", note: "Tiếng robot nói" },
  { key: "music", label: "Nhạc", note: "Nhạc phát trên robot" },
  { key: "effects", label: "Hiệu ứng", note: "Chưa có âm thanh nào dùng", dim: true },
];

const SPEAKER = '<path d="M4 9.5v5h3.2l4.8 4v-13l-4.8 4z" fill="currentColor"/>';
const WAVES = '<path d="M15 8.2a5 5 0 0 1 0 7.6M17.6 5.6a9 9 0 0 1 0 12.8" fill="none"/>';
const CROSS = '<path d="M15.5 9.5l5 5M20.5 9.5l-5 5" fill="none"/>';
const svg = (inner) => `<svg viewBox="0 0 24 24" aria-hidden="true" stroke="currentColor" stroke-width="1.8" stroke-linecap="round">${inner}</svg>`;

function paint(el) {
  const lo = Number(el.min || 0), span = (Number(el.max) - lo) || 1;
  el.style.setProperty("--fill", `${((Number(el.value) - lo) / span) * 100}%`);
}

export class VolumePanel {
  constructor({ send }) {
    this.send = send;
    this.sliders = {}; this.outs = {};
    this.hold = {}; this.timers = {}; this.lastSent = {};
    this.data = null;
    this.build();
  }

  build() {
    const panel = $("vol-panel");
    for (const c of CHANNELS) {
      const row = document.createElement("div");
      row.className = "vol-row" + (c.dim ? " dim" : "");
      row.innerHTML = `<div class="vol-row-head"><span>${c.label}</span><small>${c.note}</small></div>
        <input type="range" class="mu-range" min="0" max="100" step="1" value="100" aria-label="Âm lượng ${c.label.toLowerCase()}">
        <output>100%</output>`;
      panel.insertBefore(row, $("vol-note"));
      const input = row.querySelector("input"), out = row.querySelector("output");
      this.sliders[c.key] = input; this.outs[c.key] = out;
      input.oninput = () => this.drag(c.key, input);
      input.onchange = () => this.release(c.key, input);
    }
    // The bar's slider is the master channel too
    const bar = $("vol-master");
    this.bar = bar;
    bar.oninput = () => this.drag("master", bar);
    bar.onchange = () => this.release("master", bar);
    $("vol-mute").onclick = () => this.send({ type: "mute" });
    $("vol-down").onclick = () => this.step(-1);
    $("vol-up").onclick = () => this.step(1);
    $("vol-more").onclick = () => this.toggle();
    $("vol-mute").innerHTML = svg(SPEAKER + WAVES);
  }

  toggle(open = $("vol-panel").hidden) {
    $("vol-panel").hidden = !open;
    $("vol-more").setAttribute("aria-expanded", String(open));
  }

  step(sign) {
    const delta = sign * (this.data?.step || 5);
    this.hold.master = performance.now() + HOLD_MS;
    this.show("master", Math.max(0, Math.min(100, Number(this.bar.value) + delta)));  // At once; the robot confirms
    this.send({ type: "volume_step", channel: "master", delta });
  }

  drag(channel, input) {
    const value = Number(input.value);
    this.show(channel, value, input);
    this.hold[channel] = performance.now() + HOLD_MS;
    const wait = (this.lastSent[channel] || 0) + SEND_MS - performance.now();
    clearTimeout(this.timers[channel]);
    const fire = () => {
      this.lastSent[channel] = performance.now();
      this.send({ type: "volume", channel, volume: Number(input.value), save: false });
    };
    if (wait <= 0) fire(); else this.timers[channel] = setTimeout(fire, wait);
  }

  release(channel, input) {
    clearTimeout(this.timers[channel]);
    this.hold[channel] = performance.now() + HOLD_MS;
    this.send({ type: "volume", channel, volume: Number(input.value) });
  }

  // Put a value on the sliders of a channel (not the one being dragged)
  show(channel, value, except = null) {
    const targets = channel === "master" ? [this.sliders.master, this.bar] : [this.sliders[channel]];
    for (const el of targets) {
      if (el !== except) el.value = value;
      paint(el);
    }
    this.outs[channel].textContent = `${value}%`;
    if (channel === "master") $("vol-out").textContent = this.data?.muted ? "Tắt" : `${value}%`;
  }

  onState(v) {
    const root = $("vol");
    if (!v) { root.hidden = true; $("vol-panel").hidden = true; return; }
    root.hidden = false;
    this.data = v;
    const now = performance.now();
    for (const c of CHANNELS) {
      const value = v[c.key];
      if (typeof value !== "number") continue;
      const touched = now < (this.hold[c.key] || 0) || this.sliders[c.key].matches(":active") || (c.key === "master" && this.bar.matches(":active"));
      if (!touched) this.show(c.key, value);
    }
    const muted = !!v.muted;
    $("vol-out").textContent = muted ? "Tắt" : `${v.master}%`;
    const mute = $("vol-mute");
    mute.setAttribute("aria-pressed", String(muted));
    mute.title = muted ? "Bật lại tiếng" : "Tắt tiếng";
    mute.setAttribute("aria-label", mute.title);
    mute.innerHTML = svg(SPEAKER + (muted ? CROSS : WAVES));
    root.dataset.muted = String(muted);
    root.dataset.speaker = v.available ? "ok" : "none";
    $("vol-panel").dataset.muted = String(muted);
    const warn = $("vol-warn");
    warn.hidden = !!v.available;
    warn.title = v.message || "";
    const base = "Âm lượng thực tế mỗi nguồn = Tổng × kênh riêng.";
    $("vol-note").textContent = v.message || (v.device ? `Loa: ${v.device}. ${base}` : base);
    $("vol-note").dataset.tone = v.available ? "" : "warn";
  }
}
