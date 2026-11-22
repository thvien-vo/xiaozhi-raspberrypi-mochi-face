// Music tab: library, uploads, player and queue. Playback happens on the robot; the page
// shows the state sent over the WebSocket (voice commands included) and moves the progress
// bar between updates.
const $ = (id) => document.getElementById(id);
const SEEK_SEND_MS = 250;
const UPLOAD_HEADER = "X-Xiaozhi-Upload";

const ICONS = {
  play: "M8 5.2v13.6a1 1 0 0 0 1.5.86l11-6.8a1 1 0 0 0 0-1.72l-11-6.8A1 1 0 0 0 8 5.2z",
  pause: "M7 5h3.5v14H7zM13.5 5H17v14h-3.5z",
  prev: "M6 5h2.5v14H6zM19 6v12a.8.8 0 0 1-1.25.66L9.5 12.7a.85.85 0 0 1 0-1.4l8.25-5.96A.8.8 0 0 1 19 6z",
  next: "M15.5 5H18v14h-2.5zM5 6v12a.8.8 0 0 0 1.25.66l8.25-5.96a.85.85 0 0 0 0-1.4L6.25 5.34A.8.8 0 0 0 5 6z",
  shuffle: "M16 4l4 3.5L16 11V8.6h-1.6c-1.2 0-2 .6-2.9 2l-3 4.6C7.2 17.2 6 18 4 18H3v-2.1h1c1.2 0 1.8-.4 2.7-1.8l3-4.6c1.3-2 2.6-3 4.7-3H16zM3 6h1c2 0 3.2.8 4.5 2.8l.4.6-1.3 2-.9-1.4C5.8 8.5 5.2 8.1 4 8.1H3zM16 13l4 3.5-4 3.5v-2.4h-1.6c-2 0-3.2-.8-4.5-2.8l-.2-.3 1.3-2 .5.8c.9 1.4 1.7 2 2.9 2H16z",
  repeat: "M7 6h9V3.5L20.5 7 16 10.5V8H7a2 2 0 0 0-2 2v2H3v-2a4 4 0 0 1 4-4zm10 12H8v2.5L3.5 17 8 13.5V16h9a2 2 0 0 0 2-2v-2h2v2a4 4 0 0 1-4 4z",
  stop: "M6.5 6.5h11v11h-11z",
  volume: "M4 9.5v5h3.2l4.8 4v-13l-4.8 4zM15 8.2a5 5 0 0 1 0 7.6M17.6 5.6a9 9 0 0 1 0 12.8",
};

function icon(name) {
  const stroke = name === "volume" ? ' fill="currentColor" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"' : ' fill="currentColor"';
  return `<svg viewBox="0 0 24 24" aria-hidden="true"${stroke}><path d="${ICONS[name]}"/></svg>`;
}

export function fmtTime(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
function fmtSize(b) { return b >= 1e9 ? `${(b / 1e9).toFixed(1)} GB` : `${Math.max(0.1, b / 1e6).toFixed(1)} MB`; }

// Search that ignores case and Vietnamese marks
export function plain(text) {
  return (text || "").toLowerCase().replace(/đ/g, "d").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim();
}

function gradientFor(id) {
  let h = 0;
  for (const c of id || "") h = (h * 31 + c.charCodeAt(0)) % 360;
  return `linear-gradient(135deg, hsl(${h} 62% 52%), hsl(${(h + 50) % 360} 62% 38%))`;
}

function coverInto(el, track) {
  const key = track ? `${track.id}:${track.cover}` : "";
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.replaceChildren();
  el.style.background = track ? gradientFor(track.id) : "";
  if (track && track.cover && track.id) {
    const img = document.createElement("img");
    img.alt = ""; img.loading = "lazy"; img.decoding = "async";
    img.src = `/api/music/cover/${track.id}`;
    img.onerror = () => img.remove();
    el.append(img);
  } else {
    const s = document.createElement("span"); s.textContent = "♪"; el.append(s);
  }
}

export class MusicTab {
  constructor({ send, command, toast }) {
    this.send = send; this.command = command; this.toast = toast;
    this.state = null;
    this.library = null; this.libraryRev = -1; this.libraryBusy = false;
    this.queue = null; this.queueRev = -1; this.queueBusy = false;
    this.base = { pos: 0, at: 0, key: "" };
    this.seeking = false; this.volumeUntil = 0; this.volumeTimer = 0;
    this.uploads = []; this.uploading = false;
    this.visible = false;
    this.retryAt = 0;
    this.bind();
  }

  bind() {
    $("mu-prev").innerHTML = icon("prev"); $("mu-next").innerHTML = icon("next");
    $("mu-shuffle").innerHTML = icon("shuffle"); $("mu-stop").innerHTML = icon("stop");
    $("mu-repeat").innerHTML = icon("repeat") + "<em>1</em>";
    $("mu-vol-icon").innerHTML = icon("volume");
    $("mu-play").onclick = () => {
      const playing = this.state?.state === "playing";
      this.command({ type: playing ? "music_pause" : "music_play" });
    };
    $("mu-prev").onclick = () => this.command({ type: "music_previous" });
    $("mu-next").onclick = () => this.command({ type: "music_next" });
    $("mu-stop").onclick = () => this.command({ type: "music_stop" });
    $("mu-shuffle").onclick = () => this.send({ type: "music_mode", shuffle: !this.state?.shuffle });
    $("mu-repeat").onclick = () => {
      const order = ["off", "all", "one"];
      this.send({ type: "music_mode", repeat: order[(order.indexOf(this.state?.repeat) + 1) % 3] });
    };
    const bar = $("mu-seek");
    bar.oninput = () => { this.seeking = true; $("mu-pos").textContent = fmtTime(bar.value); this.paintBar(bar); };
    bar.onchange = () => {
      const position = Number(bar.value);
      this.base = { pos: position, at: performance.now(), key: this.base.key, hold: performance.now() + 1500 };
      this.seeking = false;
      this.command({ type: "music_seek", position });
    };
    const vol = $("mu-volume");
    vol.oninput = () => {
      $("mu-vol-out").textContent = `${vol.value}%`; this.paintBar(vol);
      this.volumeUntil = performance.now() + 1200;
      clearTimeout(this.volumeTimer);
      this.volumeTimer = setTimeout(() => this.send({ type: "music_volume", volume: Number(vol.value), save: false }), 60);
    };
    vol.onchange = () => {
      clearTimeout(this.volumeTimer);
      this.send({ type: "music_volume", volume: Number(vol.value) });
    };
    $("mu-dance").onchange = (e) => this.command({ type: "music_dance", enabled: e.target.checked });
    $("mu-search").oninput = () => this.renderLibrary();
    $("mu-queue-clear").onclick = () => this.command({ type: "music_queue_clear" });

    const input = $("mu-files"), drop = $("mu-drop");
    input.onchange = () => { this.addFiles([...input.files]); input.value = ""; };
    drop.onclick = () => input.click();
    drop.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } };
    // Files can be dropped anywhere on the tab; elsewhere a drop must not replace the page
    const page = document.querySelector('.tabpage[data-page="music"]');
    const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
    window.addEventListener("dragover", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      drop.classList.toggle("over", page.contains(e.target));
    });
    window.addEventListener("dragleave", (e) => { if (!e.relatedTarget) drop.classList.remove("over"); });
    window.addEventListener("drop", (e) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      drop.classList.remove("over");
      if (page.contains(e.target)) this.addFiles([...e.dataTransfer.files]);
    });
    setInterval(() => this.tick(), 250);
  }

  setVisible(on) { this.visible = on; if (on) { this.refresh(); this.tick(); } }

  // ----- State from the robot -----
  onState(m) {
    const none = !m;
    $("mu-missing").hidden = !none;
    if (none) { $("mu-body").hidden = true; return; }
    $("mu-body").hidden = false;
    this.state = m;
    const key = `${m.queue_uid}:${m.track?.id}:${m.state}`;
    const expected = this.position();
    const held = this.base.hold && performance.now() < this.base.hold;
    if (key !== this.base.key || (!held && (m.state !== "playing" || Math.abs(expected - m.position) > 1.6))) {
      // Whole seconds come in: the middle of one is the best guess
      this.base = { pos: m.position + (m.state === "playing" && m.position > 0 ? 0.4 : 0), at: performance.now(), key };
    }
    this.renderPlayer();
    this.refresh();
  }

  position() {
    const m = this.state;
    if (!m) return 0;
    let p = this.base.pos;
    if (m.state === "playing") p += (performance.now() - this.base.at) / 1000;
    return m.duration ? Math.min(p, m.duration) : p;
  }

  tick() {
    if (!this.visible || !this.state || this.seeking) return;
    const bar = $("mu-seek"), m = this.state;
    const pos = m.state === "stopped" ? 0 : this.position();
    bar.max = Math.max(1, m.duration || 1);
    bar.value = pos;
    bar.setAttribute("aria-valuetext", `${fmtTime(pos)} trên ${fmtTime(m.duration)}`);
    this.paintBar(bar);
    $("mu-pos").textContent = fmtTime(pos);
    $("mu-dur").textContent = m.duration ? fmtTime(m.duration) : "-:--";
  }

  paintBar(el) {
    const span = Number(el.max) - Number(el.min || 0) || 1;
    el.style.setProperty("--fill", `${((Number(el.value) - Number(el.min || 0)) / span) * 100}%`);
  }

  renderPlayer() {
    const m = this.state, t = m.track;
    const playing = m.state === "playing";
    $("mu-title").textContent = t ? t.title : "Chưa phát bài nào";
    $("mu-artist").textContent = t ? (t.artist || t.album || "") : "Chọn một bài bên dưới, hoặc nói “bật nhạc đi”.";
    coverInto($("mu-cover"), t);
    $("mu-cover").classList.toggle("playing", playing);
    const btn = $("mu-play");
    if (btn.dataset.icon !== (playing ? "pause" : "play")) {
      btn.dataset.icon = playing ? "pause" : "play";
      btn.innerHTML = icon(btn.dataset.icon);
      btn.setAttribute("aria-label", playing ? "Tạm dừng" : "Phát");
    }
    const pill = $("mu-state");
    pill.textContent = playing ? "● Đang phát" : m.state === "paused" ? (m.pause_source === "tts" ? "Chờ robot nói xong" : "Tạm dừng") : "Đã dừng";
    pill.classList.toggle("live", playing);
    $("mu-stop").disabled = m.state === "stopped";
    $("mu-seek").disabled = m.state === "stopped" || !m.duration;
    const sh = $("mu-shuffle"), rp = $("mu-repeat");
    sh.classList.toggle("on", m.shuffle); sh.setAttribute("aria-pressed", String(m.shuffle));
    rp.classList.toggle("on", m.repeat !== "off"); rp.classList.toggle("one", m.repeat === "one");
    rp.title = { off: "Không lặp", all: "Lặp cả danh sách", one: "Lặp một bài" }[m.repeat] || "";
    rp.setAttribute("aria-label", rp.title);
    const vol = $("mu-volume");
    if (performance.now() > this.volumeUntil && !vol.matches(":active")) {
      vol.value = m.volume; $("mu-vol-out").textContent = `${m.volume}%`; this.paintBar(vol);
    }
    const audio = m.audio || {};
    const warn = $("mu-audio-warn");
    warn.hidden = !!audio.ok;
    warn.textContent = audio.ok ? "" : `⚠ ${audio.message}. Cắm lại loa rồi khởi động lại robot nếu vẫn chưa có tiếng.`;
    $("mu-output").textContent = audio.ok ? `Phát trên loa robot${audio.device ? `: ${audio.device.split(":")[0]}` : ""}` : "Không có loa";
    const err = $("mu-error");
    err.hidden = !m.error; err.textContent = m.error || "";
    const d = m.dance || {};
    const sw = $("mu-dance");
    sw.checked = !!d.enabled; sw.disabled = !d.available;
    $("mu-dance-note").textContent = !d.available ? "Cần servo (SERVO.ENABLED)."
      : d.active ? `Đầu đang nhảy${m.bpm ? ` theo nhịp ${m.bpm} BPM` : ""}.`
      : d.enabled ? "Đầu sẽ nhảy khi có nhạc. Kéo đầu robot hoặc ra lệnh giọng nói thì tạm nhường."
      : `Đầu lắc nhẹ theo nhịp, góc nhỏ và trong giới hạn servo.${m.bpm ? ` Nhịp bài này: ${m.bpm} BPM.` : ""}`;
    this.markPlaying();
  }

  markPlaying() {
    const id = this.state?.state !== "stopped" ? this.state?.track?.id : null;
    for (const li of $("mu-list").children) li.classList.toggle("playing", !!id && li.dataset.id === id);
    const uid = this.state?.queue_uid;
    for (const li of $("mu-queue").children) li.classList.toggle("playing", !!uid && Number(li.dataset.uid) === uid);
  }

  // ----- Library and queue (fetched only when their revision changes) -----
  async refresh(force = false) {
    if (!this.visible || !this.state) return;
    if (performance.now() < this.retryAt && !force) return;
    if (!this.libraryBusy && (force || this.library === null || this.state.library_rev !== this.libraryRev)) {
      this.libraryBusy = true;
      try {
        const r = await fetch("/api/music/library");
        if (!r.ok) throw new Error(r.status);
        this.library = await r.json();
        this.libraryRev = this.library.rev;
        this.renderLibrary();
      } catch { this.retryAt = performance.now() + 3000; }
      this.libraryBusy = false;
    }
    if (!this.queueBusy && this.state.queue_rev !== this.queueRev) {
      this.queueBusy = true;
      try {
        const r = await fetch("/api/music/queue");
        if (!r.ok) throw new Error(r.status);
        this.queue = await r.json();
        this.queueRev = this.queue.rev;
        this.renderQueue();
      } catch { this.retryAt = performance.now() + 3000; }
      this.queueBusy = false;
    }
  }

  renderLibrary() {
    const lib = this.library;
    if (!lib) return;
    const all = lib.tracks;
    const mb = Math.round(lib.max_upload / 1048576);
    $("mu-drop-note").textContent = `${lib.extensions.join(", ")} · tối đa ${mb} MB mỗi file · đã dùng ${fmtSize(lib.usage)}`;
    $("mu-files").accept = lib.extensions.map((e) => "." + e).join(",") + ",audio/*";
    $("mu-count").textContent = `${all.length} bài`;
    const q = plain($("mu-search").value);
    const words = q.split(" ").filter(Boolean);
    const shown = words.length ? all.filter((t) => {
      const text = plain(`${t.title} ${t.artist} ${t.album} ${t.file}`);
      return words.every((w) => text.includes(w));
    }) : all;
    const list = $("mu-list");
    list.replaceChildren();
    if (!shown.length) {
      const li = document.createElement("li"); li.className = "empty";
      li.textContent = all.length ? "Không có bài nào khớp." : "Thư viện trống. Tải nhạc của bạn lên ở khung phía trên.";
      list.append(li);
      return;
    }
    for (const t of shown) list.append(this.trackRow(t));
    this.markPlaying();
  }

  trackRow(t) {
    const li = document.createElement("li");
    li.dataset.id = t.id;
    const main = document.createElement("button");
    main.className = "mu-main"; main.title = `Phát ${t.title}`;
    const cover = document.createElement("span"); cover.className = "mu-thumb"; coverInto(cover, t);
    const text = document.createElement("span"); text.className = "mu-text";
    const a = document.createElement("b"); a.textContent = t.title;
    const b = document.createElement("small"); b.textContent = [t.artist, t.album].filter(Boolean).join(" · ") || t.ext.toUpperCase();
    text.append(a, b);
    const time = document.createElement("span"); time.className = "meta"; time.textContent = fmtTime(t.duration);
    main.append(cover, text, time);
    main.onclick = () => this.command({ type: "music_play", track_id: t.id });
    const add = document.createElement("button");
    add.className = "btn small icon-btn"; add.textContent = "＋"; add.title = "Thêm vào hàng đợi"; add.setAttribute("aria-label", `Thêm ${t.title} vào hàng đợi`);
    add.onclick = () => this.command({ type: "music_enqueue", track_id: t.id });
    const del = document.createElement("button");
    del.className = "btn small icon-btn"; del.textContent = "✕"; del.title = "Xóa khỏi robot"; del.setAttribute("aria-label", `Xóa ${t.title}`);
    del.onclick = () => {
      if (del.dataset.armed) { this.command({ type: "music_delete", track_id: t.id }); return; }
      del.dataset.armed = "1"; del.textContent = "Xóa?"; del.classList.add("danger");
      setTimeout(() => { delete del.dataset.armed; del.textContent = "✕"; del.classList.remove("danger"); }, 2500);
    };
    li.append(main, add, del);
    return li;
  }

  renderQueue() {
    const q = this.queue;
    const list = $("mu-queue");
    list.replaceChildren();
    $("mu-queue-count").textContent = `${q.items.length} bài`;
    $("mu-queue-clear").disabled = !q.items.length;
    if (!q.items.length) {
      const li = document.createElement("li"); li.className = "empty"; li.textContent = "Hàng đợi trống. Phát một bài thì cả thư viện được xếp hàng."; list.append(li);
      return;
    }
    for (const it of q.items) {
      const li = document.createElement("li"); li.dataset.uid = it.uid;
      const main = document.createElement("button"); main.className = "mu-main";
      const text = document.createElement("span"); text.className = "mu-text";
      const a = document.createElement("b"); a.textContent = it.title;
      const b = document.createElement("small"); b.textContent = it.artist;
      text.append(a, b);
      const time = document.createElement("span"); time.className = "meta"; time.textContent = fmtTime(it.duration);
      main.append(text, time);
      main.onclick = () => this.command({ type: "music_queue_play", uid: it.uid });
      const del = document.createElement("button");
      del.className = "btn small icon-btn"; del.textContent = "−"; del.title = "Bỏ khỏi hàng đợi"; del.setAttribute("aria-label", `Bỏ ${it.title} khỏi hàng đợi`);
      del.onclick = () => this.command({ type: "music_dequeue", uid: it.uid });
      li.append(main, del);
      list.append(li);
    }
    this.markPlaying();
    const current = list.querySelector(".playing");
    if (current) list.scrollTop = Math.max(0, current.offsetTop - list.offsetTop - 40);
  }

  // ----- Uploads: one file at a time -----
  addFiles(files) {
    const ext = this.library?.extensions || ["mp3", "m4a", "wav", "ogg", "flac"];
    const max = this.library?.max_upload || 100 * 1048576;
    for (const file of files) {
      const item = { file, progress: 0, status: "wait", message: "" };
      if (!ext.includes(file.name.split(".").pop().toLowerCase())) { item.status = "bad"; item.message = `chỉ nhận ${ext.join(", ")}`; }
      else if (file.size > max) { item.status = "bad"; item.message = `file quá lớn (tối đa ${Math.round(max / 1048576)} MB)`; }
      else if (!file.size) { item.status = "bad"; item.message = "file trống"; }
      this.uploads.push(item);
    }
    this.renderUploads();
    if (!this.uploading) this.runUploads();
  }

  async runUploads() {
    this.uploading = true;
    for (let item; (item = this.uploads.find((u) => u.status === "wait"));) {
      item.status = "run";
      this.renderUploads();
      const r = await this.uploadOne(item);
      item.status = r.ok ? "ok" : "bad";
      item.message = r.message || "";
      this.renderUploads();
      await this.refresh(true);
    }
    this.uploading = false;
    setTimeout(() => {
      if (this.uploading) return;
      this.uploads = this.uploads.filter((u) => u.status === "bad");
      this.renderUploads();
    }, 4000);
  }

  uploadOne(item) {
    return new Promise((resolve) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/music/upload?name=${encodeURIComponent(item.file.name)}`);
      xhr.setRequestHeader(UPLOAD_HEADER, "1");
      xhr.setRequestHeader("Content-Type", "application/octet-stream");
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) { item.progress = e.loaded / e.total; this.renderUploads(); } };
      xhr.onload = () => {
        let r = null;
        try { r = JSON.parse(xhr.responseText); } catch {}
        resolve(r || { ok: false, message: xhr.status === 401 ? "cần đăng nhập lại" : `lỗi ${xhr.status}` });
      };
      xhr.onerror = () => resolve({ ok: false, message: "mất kết nối khi tải lên" });
      xhr.send(item.file);
    });
  }

  renderUploads() {
    const box = $("mu-uploads");
    box.hidden = !this.uploads.length;
    while (box.childElementCount > this.uploads.length) box.lastElementChild.remove();
    this.uploads.forEach((u, i) => {
      let row = box.children[i];
      if (!row) {
        row = document.createElement("li");
        row.innerHTML = '<span class="name"></span><span class="meta"></span><button class="btn small icon-btn" aria-label="Ẩn">✕</button><div class="progress"><div></div></div>';
        box.append(row);
      }
      row.className = u.status;
      row.querySelector(".name").textContent = u.file.name;
      row.querySelector(".meta").textContent = { wait: "chờ", run: u.progress >= 1 ? "kiểm tra…" : `${Math.round(u.progress * 100)}%`, ok: "✓ xong", bad: u.message }[u.status];
      const x = row.querySelector("button");
      x.hidden = u.status !== "bad";
      x.onclick = () => { this.uploads = this.uploads.filter((o) => o !== u); this.renderUploads(); };
      row.querySelector(".progress").hidden = u.status !== "run";
      row.querySelector(".progress div").style.width = `${u.progress * 100}%`;
    });
  }
}
