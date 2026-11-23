// Chat tab: the STT hallucination filter (patterns, recently ignored sentences) and the
// local VAD settings. The robot owns every value; the page sends commands and shows what
// comes back in the state snapshot (s.stt_filter, s.vad).
const $ = (id) => document.getElementById(id);
const SEND_MS = 150;
const HOLD_MS = 1500;  // After touching a slider, ignore the robot's number this long

const VAD_SLIDERS = [
  { key: "threshold", label: "Độ chặt (xác suất giọng nói)", step: 0.05, fmt: (v) => v.toFixed(2),
    ends: ["Nhạy", "Chặt"] },
  { key: "energy_db", label: "Âm lượng tối thiểu", step: 1, fmt: (v) => `${Math.round(v)} dBFS`,
    ends: ["Nghe cả tiếng nhỏ", "Chỉ tiếng to"] },
  { key: "min_speech_ms", label: "Nói ít nhất", step: 10, fmt: (v) => `${Math.round(v)} ms` },
  { key: "preroll_ms", label: "Gửi kèm trước tiếng nói", step: 10, fmt: (v) => `${Math.round(v)} ms` },
  { key: "hangover_ms", label: "Gửi tiếp sau khi im", step: 50, fmt: (v) => `${Math.round(v)} ms` },
];

function timeText(t) {
  return new Date(t * 1000).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export class ListenPanel {
  constructor({ send, command }) {
    this.send = send;
    this.command = command;
    this.sliders = {}; this.outs = {}; this.hold = {}; this.timers = {};
    this.filter = null; this.vad = null;
    this.patternsKey = ""; this.recentKey = "";
    this.build();
  }

  build() {
    $("sf-enabled").onchange = (e) => this.command({ type: "stt_filter", enabled: e.target.checked });
    $("sf-reply").onchange = (e) => this.command({ type: "stt_filter", reply_filter: e.target.checked });
    $("sf-context").onchange = (e) => this.command({ type: "stt_filter", reset_context: e.target.checked });
    $("sf-add-form").onsubmit = async (e) => {
      e.preventDefault();
      const input = $("sf-add");
      const pattern = input.value.trim();
      if (!pattern) return;
      const r = await this.command({ type: "stt_pattern_add", pattern });
      if (r?.ok) input.value = "";
    };
    $("sf-patterns-reset").onclick = () => this.command({ type: "stt_patterns_reset" });
    $("sf-recent-clear").onclick = () => this.command({ type: "stt_recent_clear" });
    $("sf-min").onchange = (e) => {
      const n = Number(e.target.value);
      if (Number.isFinite(n)) this.command({ type: "stt_filter", min_chars: Math.round(n) });
    };
    $("sf-patterns").onclick = (e) => {
      const b = e.target.closest("button[data-pattern]");
      if (b) this.command({ type: "stt_pattern_remove", pattern: b.dataset.pattern });
    };
    $("sf-recent").onclick = (e) => {
      const b = e.target.closest("button[data-add]");
      if (b) { $("sf-patterns-box").open = true; $("sf-add").value = b.dataset.add; $("sf-add").focus(); }
    };

    $("vad-enabled").onchange = (e) => this.command({ type: "vad", enabled: e.target.checked });
    const box = $("vad-sliders");
    for (const c of VAD_SLIDERS) {
      const label = document.createElement("label");
      label.className = "slider compact";
      label.innerHTML = `<span class="slider-label"><span>${c.label}</span><output>-</output></span>
        <input type="range" step="${c.step}" aria-label="${c.label}">
        ${c.ends ? `<span class="slider-ends"><span>${c.ends[0]}</span><span>${c.ends[1]}</span></span>` : ""}`;
      box.append(label);
      const input = label.querySelector("input"), out = label.querySelector("output");
      this.sliders[c.key] = input; this.outs[c.key] = out;
      input.oninput = () => {
        out.textContent = c.fmt(Number(input.value));
        this.hold[c.key] = performance.now() + HOLD_MS;
        clearTimeout(this.timers[c.key]);
        this.timers[c.key] = setTimeout(() => this.send({ type: "vad", [c.key]: Number(input.value), save: false }), SEND_MS);
      };
      input.onchange = () => {
        clearTimeout(this.timers[c.key]);
        this.hold[c.key] = performance.now() + HOLD_MS;
        this.command({ type: "vad", [c.key]: Number(input.value), save: true });
      };
    }
  }

  onState(s) {
    this.renderFilter(s.stt_filter);
    this.renderVad(s.vad);
  }

  renderFilter(f) {
    $("sf-card").hidden = !f;
    if (!f) return;
    this.filter = f;
    $("sf-enabled").checked = !!f.enabled;
    $("sf-reply").checked = !!f.reply_enabled;
    $("sf-context").checked = !!f.reset_context;
    if (document.activeElement !== $("sf-min")) $("sf-min").value = f.min_chars;
    const gate = f.gate || {};
    $("sf-stats").textContent = `${f.enabled ? "Đang bật" : "Đang tắt"} · đã bỏ qua ${f.ignored_total} câu từ lúc khởi động`
      + (gate.dropped_audio_frames ? ` · chặn ${gate.dropped_audio_frames} gói âm thanh trả lời` : "")
      + (gate.suppressing ? " · đang chặn một lượt" : "");
    $("sf-count").textContent = String(f.patterns.length);

    const pkey = JSON.stringify(f.patterns);
    if (pkey !== this.patternsKey) {
      this.patternsKey = pkey;
      const ul = $("sf-patterns");
      ul.replaceChildren();
      for (const p of f.patterns) {
        const li = document.createElement("li");
        const span = document.createElement("span"); span.textContent = p;
        const b = document.createElement("button");
        b.type = "button"; b.dataset.pattern = p; b.textContent = "×";
        b.title = `Xóa mẫu "${p}"`; b.setAttribute("aria-label", `Xóa mẫu ${p}`);
        li.append(span, b); ul.append(li);
      }
      if (!f.patterns.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "Chưa có mẫu nào."; ul.append(li); }
    }

    const recent = f.recent || [];
    $("sf-recent-count").textContent = String(recent.length);
    const rkey = JSON.stringify(recent.map((r) => [r.time, r.text]));
    if (rkey !== this.recentKey) {
      this.recentKey = rkey;
      const ul = $("sf-recent");
      ul.replaceChildren();
      for (const r of recent) {
        const li = document.createElement("li");
        const text = document.createElement("div"); text.className = "sf-text";
        const q = document.createElement("span"); q.className = "name"; q.textContent = r.text || "(trống)";
        const meta = document.createElement("span"); meta.className = "meta";
        meta.textContent = `${timeText(r.time)} · khớp: ${r.pattern || "-"}`;
        text.append(q, meta);
        const b = document.createElement("button");
        b.type = "button"; b.className = "btn small"; b.dataset.add = r.text || ""; b.textContent = "Làm mẫu";
        b.title = "Chép câu này vào ô thêm mẫu";
        li.append(text, b); ul.append(li);
      }
      if (!recent.length) { const li = document.createElement("li"); li.className = "empty"; li.textContent = "Chưa bỏ qua câu nào."; ul.append(li); }
    }
  }

  renderVad(v) {
    $("vad-card").hidden = !v;
    if (!v) return;
    this.vad = v;
    $("vad-enabled").checked = !!v.enabled;
    const now = performance.now();
    for (const c of VAD_SLIDERS) {
      const input = this.sliders[c.key];
      const range = v.ranges?.[c.key];
      if (range) { input.min = range[0]; input.max = range[1]; }
      input.disabled = !v.enabled;
      if (!(this.hold[c.key] > now)) {
        input.value = v[c.key];
        this.outs[c.key].textContent = c.fmt(Number(v[c.key]));
      }
    }
    const dot = $("vad-dot");
    dot.dataset.on = String(!!(v.enabled && v.open));
    const backend = v.backend === "silero" ? "Silero VAD" : v.backend === "energy" ? "chỉ theo âm lượng" : v.backend;
    const parts = [];
    if (!v.enabled) parts.push("Tắt: micro gửi liên tục khi đang nghe");
    else parts.push(v.open ? "Đang gửi (có tiếng nói)" : "Im lặng: không gửi");
    if (v.enabled) {
      parts.push(`bộ nhận: ${backend}`);
      parts.push(`ồn nền ≈ ${v.noise_db} dBFS`);
      if (v.last_speech_db != null) parts.push(`tiếng nói gần nhất ${v.last_speech_db} dBFS, ${(v.last_open_ms / 1000).toFixed(1)} s`);
      if (v.sent_ratio != null) parts.push(`lọt qua ${Math.round(v.sent_ratio * 100)}% khung`);
    }
    $("vad-status").textContent = parts.join(" · ");
    $("vad-note").textContent = v.backend_error ? `Không nạp được Silero (${v.backend_error}); đang dùng ngưỡng âm lượng.` : "";
  }
}
