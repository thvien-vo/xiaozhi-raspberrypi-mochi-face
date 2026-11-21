# Pi 5 install log

Everything added or changed outside `~/Xiaozhi_RaspberryPi` on the Pi (user `admin`),
with how to undo it. No file under `/boot`, `/etc`, or `/usr` was touched, nothing was
installed with apt, and the Pi was not rebooted. `sudo` needs a password on that Pi, so
nothing ran as root.

| What | Where | Undo |
| --- | --- | --- |
| Python 3.12 and uv cache | `~/.local/share/uv`, `~/.cache/uv` | `uv python uninstall 3.12; uv cache clean` |
| Virtual environment | `~/Xiaozhi_RaspberryPi/.venv` | `rm -rf ~/Xiaozhi_RaspberryPi/.venv` |
| Device identity and settings (mode 600), copied from the laptop | `~/.local/share/py-xiaozhi/config/{config.json,efuse.json}` | `rm -r ~/.local/share/py-xiaozhi` |
| App logs, limited to about 40 MB | `~/.local/share/py-xiaozhi/logs/` | same as above |
| YOLO models | `~/Xiaozhi_RaspberryPi/models/yolo/` | `rm -r` it |
| fontconfig and freetype `.deb` files, unpacked with `dpkg -x` | `~/.local/debs`, `~/.local/xiaozhi-libs` | `rm -rf ~/.local/debs ~/.local/xiaozhi-libs` |
| systemd user services, enabled | `~/.config/systemd/user/{xiaozhi,jabra-autopair}.service` | `systemctl --user disable --now xiaozhi.service jabra-autopair.service; rm ~/.config/systemd/user/{xiaozhi,jabra-autopair}.service` |
| Desktop autostart entry (only used if a desktop is installed later) | `~/.config/autostart/xiaozhi-autostart.desktop` | `rm` it |
| Audio choice (written at each start) | `~/.config/alsa/asoundrc`, `~/.local/state/xiaozhi/audio-selected` | `rm -r ~/.config/alsa/asoundrc ~/.local/state/xiaozhi` |
| Temporary test logs | `/tmp/offscreen_app.log`, `/tmp/fd*.log`, `~/uvsync.log` | `rm` them |

Already present before this work and left alone: lingering for `admin`
(`loginctl enable-linger`), which is what lets the user services start at boot.

The laptop and the Pi use the same device identity. Do not run both at once.

## Bản sửa lỗi chữ và âm thanh (không thêm file hệ thống)

Chỉ đổi code trong `~/Xiaozhi_RaspberryPi` (`src/ui/gui/fonts.py`, `main.py`, `face_demo.py`,
`src/audio_codecs/audio_converter.py`). Không thêm file nào ngoài các mục ở bảng trên. Ảnh chụp
framebuffer dùng file tạm trong `/tmp`, đã xóa. Để quay lại, `git checkout` các file đó.

## Chạm không nghe: bật chế độ auto

Thêm `FACE.AUTO_MODE` (preset Pi: `true`) vào `config.json` của Pi. Gỡ: đặt lại `false` rồi
`systemctl --user restart xiaozhi`. Không thêm file hệ thống.

## Tiếng rè: Jabra phát nhanh gấp 3 ở 16 kHz

Thêm `AUDIO_DEVICES.force_output_sample_rate` = 48000 vào `config.json` của Pi (gỡ: đặt lại
`null`, restart dịch vụ). Không thêm file hệ thống. File tạm `/tmp/harness.py`, `/tmp/*.wav`
đã xóa.

## AI luôn nghe: FACE.ALWAYS_LISTEN

Code mới: `src/plugins/always_listen.py` (bộ giám sát, chỉ nạp khi `FACE.ALWAYS_LISTEN` true),
`src/plugins/ui_session.py` (chạm = ngắt lời, không dừng), `src/bootstrap/session.py` (ngắt lời
xóa hàng đợi âm thanh). Preset Pi bật `FACE.ALWAYS_LISTEN: true`; mặc định `false` nên laptop
không đổi. Khi mất nghe, thử lại sau 0,5 s rồi 1, 2, 5, 10, 30 s (giữ 30 s); chỉ reset sau khi
nghe ổn định 10 s. Nếu transcript giống câu AI vừa nói hoặc đến trong 1 s sau khi AI nói xong,
log ghi `possible self-hearing` (AEC phần mềm vẫn tắt). Không thêm file hệ thống.

Áp dụng trên Pi: lấy các commit này vào nhánh `pi5-deploy`, chạy
`python scripts/apply_config_preset.py` (hoặc đặt `FACE.ALWAYS_LISTEN` true trong `config.json`),
rồi `systemctl --user restart xiaozhi`. Gỡ: đặt lại `false` và restart.

## Đổi sang webcam UGREEN: AI không nói/không nghe (2026-11-21)

**Nguyên nhân tìm được.** Jabra vẫn được chọn đúng (card `USB`, hw:1,0; webcam là card `U2K`, mic
webcam không được dùng), loa và mic Jabra đều chạy (thu thử RMS ≈ 690). Nhưng Pi đang chạy bản
cũ trên nhánh `robot-face` (commit 2634b16, cộng bản sửa chưa commit) với `FACE.ALWAYS_LISTEN`
= `false`: sau khi khởi động app không kết nối server, chỉ chạm màn hình mới mở phiên. Log cuối
dừng ở dòng "Camera opened", không có dòng nào về server. Lần chạm đầu tiên sau khi đổi webcam
không có phản hồi rõ nên trông như "không nói, không nghe". Nguồn ổn (`get_throttled=0x0`). `dmesg`
chỉ có lỗi liệt kê USB của màn cảm ứng `1a86:e5e3` lúc khởi động (tự hết).

**Đã sửa (code ở nhánh `pi5-deploy`, Pi đã được đưa lên cùng commit):**
- `deploy/raspberry-pi/jabra-audio.sh`: chọn card theo TÊN dài chứa "Jabra" và phải có cả phát
  lẫn thu; dùng `CARD=<id>` chỉ khi id không trùng card khác, trùng thì dùng số card; mỗi lần
  khởi động đặt loa 75% và mic 70% bằng `amixer` (không cần root) và ghi vào log; không có Jabra
  thì ghi `ERROR`, xóa `~/.config/alsa/asoundrc` cũ, không chọn thiết bị khác. Đổi mức bằng
  `JABRA_SPK_PCT` / `JABRA_MIC_PCT`.
- `AUDIO_DEVICES.strict_names` (preset Pi: true): app không rơi về thiết bị khác (mic webcam)
  khi không thấy Jabra. `AUDIO_DEVICES.init_retry_s` (preset Pi: 90): chưa có Jabra thì thử lại
  nền, giao diện và camera vẫn chạy (`src/utils/audio_device.py`, `src/plugins/audio.py`).
- `CAMERA.fourcc` (preset Pi: `MJPG`, 640x480): webcam gửi MJPEG, đã kiểm bằng `v4l2-ctl`
  (`src/mcp/tools/camera/capture_backend.py`, `src/vision/camera_service.py`).
- `FACE.GREET_ON_START` (preset Pi: true), `FACE.GREET_TEXT` ("Xin chào"),
  `FACE.GREET_MIN_INTERVAL_S` (300): `src/plugins/always_listen.py`,
  `src/bootstrap/session.py` (`begin_greeting`). Mặc định false nên laptop không đổi.
  Server từ chối câu dài trên kênh `detect` ("Detect is only for wake words"); câu ngắn như
  "Xin chào" thì server đáp bằng giọng nói. Giữ GREET_TEXT ngắn một hai từ.

**Cách chào hoạt động.** Chờ thiết bị âm thanh mở (tối đa 90 s) và kết nối server (thử lại
1/2/5/10/30 s, bỏ chào sau 120 s), gửi `listen detect` rồi `listen start` như firmware, giữ
trạng thái IDLE nên mic không gửi tiếng trong lúc AI chào; khi TTS kết thúc mới chuyển sang nghe.
Giờ chào lần cuối lưu ở `~/.local/state/xiaozhi/last-greeting`: không chào quá một lần mỗi 5
phút, kết nối lại WebSocket không chào lại. Xóa file này để thử chào ngay.

**Gỡ / quay lại.** `FACE.GREET_ON_START=false` tắt chào; `git checkout 2634b16` trong
`~/Xiaozhi_RaspberryPi` về bản cũ (bản sao lưu: `~/pi-uncommitted-*.patch`,
`~/pre-sync-backup-*.tar.gz`, `~/config.json.bak-*`). Không sửa /boot, /etc, không reboot.
