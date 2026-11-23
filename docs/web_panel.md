# Trang web điều khiển robot

Trang web chạy ngay trong app Xiaozhi (plugin `web_panel`). Từ laptop hoặc điện thoại trong cùng mạng LAN, bạn có thể:

- xoay đầu robot trên mô hình 3D;
- xem webcam có khung YOLO;
- xem mặt robot trực tiếp (trên mô hình 3D và ô 2D), đổi biểu cảm và màu mắt;
- chạy cử chỉ, ghi và phát lại động tác;
- tải nhạc lên robot và phát trên loa robot;
- nhắn tin cho Xiaozhi.

## Mở trang

1. Trong `~/.local/share/py-xiaozhi/config/config.json` trên Pi, bật mục `WEB`. Preset `deploy/raspberry-pi/config.pi5.json` đã bật sẵn.

   ```json
   "WEB": {
     "ENABLED": true,
     "HOST": "0.0.0.0",
     "PORT": 8080,
     "PASSWORD": "",
     "CAMERA_STREAM_FPS": 8,
     "FACE_STREAM_FPS": 10,
     "HTTPS_ENABLED": false,
     "HTTPS_PORT": 8443
   }
   ```

2. Khởi động lại app: `systemctl --user restart xiaozhi`. Log có dòng `Web panel listening on http://Pi5.local:8080`.
3. Mở **http://Pi5.local:8080** trên trình duyệt. Nếu máy không phân giải được `.local` (một số máy Android), dùng IP của Pi, ví dụ `http://10.126.33.109:8080`.

Không cần cài thêm gì. three.js 0.170 (GLTFLoader, OrbitControls) nằm sẵn trong `web_assets/vendor/three`, nên trang chạy được cả khi không có Internet.

## Các phần của trang

| Phần | Làm gì |
|---|---|
| **Nghỉ / Thức** (thanh trên cùng) | Công tắc lớn cho robot đi nghỉ hoặc thức dậy, tab nào cũng thấy (xem "Chế độ Nghỉ") |
| Thanh trạng thái | Trạng thái WebSocket, servo đã nối chưa, app đang rảnh/nghe/nói, ai đang điều khiển đầu |
| Khung 3D | Mô hình đặc là góc thật robot báo về. Mô hình cam mờ là góc đích. **Kéo vào đầu robot**: ngang để xoay, dọc để gật. Kéo chỗ trống để xoay góc nhìn, cuộn để phóng to. Nút Trước/Sau/Trên đổi góc nhìn, "Nhìn thẳng" đưa đầu về giữa. |
| Điều khiển | Thanh trượt xoay/gật, joystick, chế độ nghiêng điện thoại, bật/tắt bám người, chỉnh KP và vùng chết khi đang chạy ("Lưu vào cấu hình" ghi vào `SERVO.*`) |
| Động tác | Cử chỉ: gật đầu, lắc đầu, nhìn quanh, ngạc nhiên, nhảy. Ghi động tác, danh sách phát lại/xóa. |
| Camera | Webcam MJPEG có khung YOLO. Khung cam là người robot bám theo. |
| Mặt | Ô "Màn hình robot" (mặt đang hiện trên màn 7"), 21 biểu cảm, màu mắt (bảng màu, tự chọn, cầu vồng, theo cảm xúc), bật/tắt `FACE.AUTO_MODE`, "Đánh thức mặt" |
| (thanh trên cùng) | Âm lượng tổng, nút tắt tiếng, +/- và mục "Âm thanh" (xem "Âm lượng"); hiện ở mọi tab |
| Nhạc | Thư viện nhạc trên robot: tải lên, xóa, tìm, phát, tua, âm lượng, lặp/trộn, hàng đợi, nhảy theo nhạc (xem "Nhạc") |
| Trò chuyện | Câu người nói (STT) và câu robot trả lời (TTS), ô gửi tin nhắn chữ; lọc câu ảo giác (câu bị bỏ qua hiện xám) và VAD micro (xem "Lọc câu ảo giác…") |

**Chiều kéo:** kéo đầu sang trái thì robot quay sang trái của nó, kéo lên thì ngẩng. Nhìn từ góc nào cũng vậy, giống thanh trượt và joystick; chữ "Trái/Phải" đều tính theo robot.

**Quy ước góc:** pan dương là robot quay sang trái của nó, tilt dương là ngẩng lên. Giới hạn: pan ±90°, tilt ±45° (`SERVO.PAN_LIMIT`, `TILT_LIMIT`).

## Ai được điều khiển đầu

Thứ tự ưu tiên: **Web** (người đang kéo) > **lệnh giọng nói** > **nhảy theo nhạc** > **bám người YOLO**.

- Mỗi lệnh từ web giữ quyền thêm `SERVO.MANUAL_HOLD_SEC` giây (mặc định 5). Trong thời gian đó, lệnh giọng nói bị từ chối và bám người tạm dừng. Hết thời gian không thao tác thì quyền được nhả.
- Đóng trang web cũng nhả quyền sau thời gian đó.
- Cử chỉ và động tác chạy trên Pi, nên đóng trình duyệt chúng vẫn chạy tiếp. Kéo đầu trên web sẽ dừng động tác đang chạy.

Lệnh giọng nói mới (MCP `self.head.gesture`): "gật đầu đi", "lắc đầu đi", "nhìn quanh xem", "giả vờ ngạc nhiên", "nhảy đi". Chúng chạy đúng các cử chỉ trên trang web.

## Mặt robot trên trang web

Màn hình của mô hình 3D và ô "Màn hình robot" ở tab Mặt hiện **đúng hình mặt mà app đang vẽ** trên màn 7":

- Khi có trang đang mở (và tab trình duyệt đang hiện), app chụp riêng phần mặt (`RobotFace` trong QML) bằng `QQuickItem.grabToImage` ở cỡ nhỏ 320×135, nén JPEG (~2–5 KB) và gửi qua WebSocket dưới dạng tin nhị phân, khoảng `WEB.FACE_STREAM_FPS` hình/giây (mặc định 10). Không ai xem thì không chụp. Mặt trên màn robot vẫn vẽ như cũ, việc chụp là một lần vẽ thêm ở cỡ nhỏ.
- Vì là hình chụp, mọi thay đổi đều hiện ra: biểu cảm chọn từ web, từ server/giọng nói, mặt tự đổi khi nghe/nghĩ/nói/buồn ngủ/ngủ, chớp mắt, màu mắt, cầu vồng.
- Nhãn "● Trực tiếp" nghĩa là đang nhận hình từ robot. Nếu không có hình (app không chạy GUI, `FACE.STYLE` không phải `robot`, hoặc `WEB.FACE_STREAM_FPS = 0`), trang tự **vẽ lại** mặt bằng chính các preset mắt của robot (`/face_presets.js` là `qml/face/FacePresets.js`), theo biểu cảm, trạng thái và màu mắt hiện tại; nhãn là "Vẽ lại".
- Nút biểu cảm đang chọn lấy từ chính `FaceModel` của app, nên cũng đổi khi biểu cảm đổi từ nguồn khác. Nhãn cạnh "Biểu cảm" ghi thêm "đang nghe", "đang nói", "đang nghĩ", "buồn ngủ", "đang ngủ".

## Màu mắt

Đổi ở tab **Mặt → Màu mắt**, bằng giọng nói, hoặc trong config. Màn robot và mặt trên web đổi ngay; lựa chọn được lưu vào config nên khởi động lại vẫn giữ.

```json
"FACE": {
  "EYE_COLOR": "#ffffff",
  "EYE_COLOR_MODE": "fixed"
}
```

- `EYE_COLOR`: mã hex `#rrggbb` (mặc định trắng, như trước). Màu quá tối được làm sáng lên vừa đủ để mắt vẫn rõ trên nền đen (độ chói tối thiểu ~0,125, tương phản khoảng 3,5:1). Giá trị sai trong config thì dùng mặc định.
- `EYE_COLOR_MODE`:
  - `fixed`: một màu.
  - `rainbow` (cầu vồng): màu đổi dần liên tục, một vòng 12 giây.
  - `emotion` (theo cảm xúc): mỗi biểu cảm một màu. Vui/cười = vàng, buồn/khóc = xanh dương, giận = đỏ, yêu thương/hôn = hồng, ngượng = hồng nhạt, ngạc nhiên/sốc/thèm = cam, suy nghĩ/bối rối = tím, ngầu/tự tin = xanh ngọc, nháy mắt/tinh nghịch = xanh lá, buồn ngủ/ngủ = xanh nhạt. Bình thường dùng màu `EYE_COLOR`.
- Màu áp dụng cho thân mắt, mắt trái tim (với mắt trắng mặc định thì tim vẫn màu hồng như cũ), mắt xoắn ốc (bối rối) và chữ "z" khi ngủ. Nước mắt, má hồng giữ màu riêng. Mặt robot không có hiệu ứng phát sáng/viền nên không có gì khác để tô.
- Trên web: 9 màu có sẵn (trắng, xanh dương, xanh ngọc, xanh lá, tím, hồng, cam, đỏ, vàng), ô **Tự chọn** (bảng chọn màu; đang kéo thì xem trước, thả ra mới lưu), và ba nút **Cố định / Cầu vồng / Theo cảm xúc**. Bấm một màu cũng tắt cầu vồng/cảm xúc.
- Pi kiểm tra mọi giá trị: chỉ nhận hex hợp lệ và tên chế độ `fixed`, `rainbow`, `emotion`; giá trị khác bị từ chối với thông báo lỗi.

Lệnh giọng nói (MCP `self.face.set_eye_color`, nhận tên màu tiếng Việt):

- "đổi màu mắt sang màu tím", "cho mắt màu xanh lá", "mắt màu đỏ đi"
- "bật mắt cầu vồng"
- "mắt đổi màu theo cảm xúc"
- "tắt cầu vồng" / "giữ một màu" (về `fixed`, giữ màu hiện tại)
- "về màu mắt mặc định" (trắng)

## Nhạc

Robot phát nhạc từ **thư viện trên Pi**: `~/.local/share/py-xiaozhi/music` (mp3, m4a, wav, ogg, flac; có thể có thư mục con). Nhạc ra **loa robot**. Tên bài, ca sĩ, album, ảnh bìa đọc từ tag; không có tag thì lấy từ tên file kiểu `Ca sĩ - Tên bài.mp3`.

Tab **Nhạc**:

- **Tải nhạc lên:** kéo thả file vào trang, hoặc bấm khung "Tải nhạc lên robot" (chọn được nhiều file, dùng được trên điện thoại). Mỗi file tối đa 100 MB, cả thư viện 8 GB, luôn chừa 500 MB trống trên thẻ. File chép thẳng vào thư mục bằng `scp` cũng tự hiện ra.
- **Phát:** bấm một bài; cả thư viện thành hàng đợi bắt đầu từ bài đó. Có tạm dừng, bài trước (đã nghe quá 4 giây thì về đầu bài), bài sau, tua, dừng hẳn, âm lượng (kênh Nhạc, cùng một thanh với mục "Âm thanh" ở thanh trên), lặp (không/cả danh sách/một bài), trộn bài.
- **Hàng đợi:** nút + thêm bài, − bỏ bài, bấm vào bài để nhảy tới.
- **Xóa:** bấm ✕ rồi bấm "Xóa?" để xác nhận.
- **Nhảy theo nhạc:** đầu robot lắc nhẹ theo nhịp ước lượng từ file (tối đa ±16° ngang, ±7° dọc quanh tư thế đang đứng, luôn trong giới hạn servo). Kéo đầu trên web, lệnh giọng nói hoặc một cử chỉ đang chạy thì nhảy tự nhường. Mặc định tắt; bật/tắt được nhớ lại. Nhảy có tiếng servo.
- Mặt robot vui và nhún theo nhịp khi có nhạc (`MUSIC.FACE_BOUNCE`).
- Mọi trang đang mở và lệnh giọng nói dùng chung một trình phát nên luôn khớp nhau.
- Không có loa thì tab hiện khung đỏ và nhạc không phát (các phần khác vẫn dùng được).

Câu lệnh giọng nói (MCP `music_player.*`):

| Nói | Robot làm |
|---|---|
| "bật nhạc đi", "mở bài nào cũng được" | Phát một bài bất kỳ, cả thư viện xếp hàng |
| "mở bài Nơi này có anh", "phát nhạc của Sơn Tùng" | Tìm gần đúng trong thư viện (không cần dấu, nghe sai chút vẫn được); tên ca sĩ thì phát các bài của ca sĩ đó |
| "bài tiếp", "bài trước" | Chuyển bài |
| "tạm dừng nhạc", "phát tiếp", "dừng nhạc" | Dừng/phát |
| "to lên", "nhỏ xuống", "nhạc to 50%" | Âm lượng nhạc |
| "lặp bài này", "trộn bài" | Chế độ phát |
| "nhảy theo nhạc đi", "thôi đừng nhảy nữa" | Bật/tắt nhảy |
| "có những bài nào?" | Đọc vài bài trong thư viện |

Không có bài thì robot nói thẳng là chưa phát được và gợi ý bài gần giống, không báo "đang phát" nữa.

Khi robot nói, nhạc tự nhỏ lại rồi to dần trở lại (`MUSIC.DUCK_WHILE_SPEAKING`). Robot chỉ nghe được lệnh khi đang ở trạng thái nghe, và micro cũng nghe thấy nhạc: nếu robot khó nghe lệnh thì giảm âm lượng nhạc hoặc đứng gần hơn. Với `FACE.ALWAYS_LISTEN`, lời hát có thể bị nhận nhầm thành câu nói và robot trả lời chen vào (nhạc vẫn phát, chỉ nhỏ lại). Server STT đôi khi còn tự "nghe" ra câu dù đang yên lặng.

Cấu hình (`config.json`, mục `MUSIC`): `LIBRARY_DIR` (trống = thư mục mặc định), `MAX_UPLOAD_MB`, `MAX_LIBRARY_MB`, `VOLUME`, `REPEAT`, `SHUFFLE`, `DANCE`, `FACE_BOUNCE`, `DUCK_WHILE_SPEAKING`, `ONLINE_ENABLED`. (`VOLUME` chỉ còn là mức nhạc cũ: dùng khi `AUDIO_VOLUME.MUSIC` còn `null`.) Nguồn nhạc trực tuyến cũ (API không chính thức) **mặc định tắt**: máy chủ của nó hỏng, nhạc có bản quyền bị khóa.

Giải mã cần ffmpeg. Pi không có sẵn nên app dùng bản đi kèm gói `imageio-ffmpeg` (trong `pyproject.toml`), không cần quyền root. Cài trên Pi (tải khoảng 58 MB): `~/.local/bin/uv sync --frozen --inexact --no-dev --extra gui --extra vision`.

An toàn: tên file tải lên được làm sạch, chỉ nhận đúng đuôi và đúng nội dung âm thanh (chữ ký file + đọc thử bằng mutagen), ghi vào thư mục tạm rồi mới đưa vào thư viện, không ghi đè bài có sẵn. Trang chỉ dùng mã bài (không bao giờ đường dẫn), liên kết tượng trưng bị bỏ qua, nên không đọc/xóa được file ngoài thư mục nhạc. Đường tải lên cần đăng nhập (nếu có mật khẩu) và chỉ nhận từ chính trang này.

## Âm lượng

Bốn kênh, mỗi kênh 0-100%: **Tổng** (mọi âm ra loa), **Giọng robot** (tiếng robot nói và thông báo), **Nhạc**, **Hiệu ứng** (đã có sẵn trong code và config nhưng chưa âm thanh nào dùng nên hiện mờ). Âm lượng thật của mỗi nguồn = Tổng × kênh riêng (đường cong bình phương cho dễ nghe, nên 50% × 50% = 25% thanh trượt, tức 1/16 công suất). **Tắt tiếng** giữ nguyên các mức; bật lại là về đúng mức cũ. Kéo Tổng lên trong lúc đang tắt tiếng thì tự bật tiếng.

**Cách chỉnh: gain phần mềm trong bộ trộn của app** (`AudioCodec`, `src/audio_codecs/levels.py`), không đụng mixer phần cứng. Lý do: Pi chạy ALSA trực tiếp, không có PulseAudio/PipeWire (chỉ có `amixer`); mixer của card Jabra phụ thuộc số card, bị `jabra-audio.sh` đặt lại 75% mỗi lần khởi động và không tách được từng nguồn; gain phần mềm đổi ngay trong 20 ms, tách được giọng/nhạc, và cắm lại Jabra không làm mất mức vì mức nằm trong app. Mixer phần cứng của Jabra vẫn là 75% (`JABRA_SPK_PCT`) và đóng vai trò "trần" cố định.

- **Thanh trên cùng (mọi tab, dùng được trên điện thoại):** nút loa = tắt/bật tiếng, nút −/+ (bước 5%), thanh Tổng, và nút "Âm thanh ▾" mở bốn thanh riêng. Kéo thanh thì trang gửi tối đa ~12 lệnh/giây rồi một lệnh lưu khi thả; robot áp dụng ngay.
- **Đồng bộ:** mọi trình duyệt đang mở, thanh trong tab Nhạc, lệnh giọng nói và web luôn cùng một con số (robot là nơi giữ số, web chỉ hiển thị).
- **Không có loa** (Jabra chưa cắm hoặc luồng phát không chạy): thanh hiện chữ "Chưa có loa"; vẫn kéo và lưu được, mức áp dụng khi có loa.
- **Lệnh giọng nói:** "to lên" / "nhỏ xuống" / "âm lượng 50%" / "tắt tiếng" / "bật tiếng" chỉnh âm lượng Tổng (tool `self.audio_speaker.set_volume` với `change`, `volume`, `mute`, `channel`). "Robot nói nhỏ lại" chỉnh kênh Giọng robot. Chỉ khi nói rõ về nhạc ("nhạc to lên") mới dùng tool nhạc `music_player.set_volume`.
- **Lưu:** mục `AUDIO_VOLUME` trong `config.json` (`MASTER`, `VOICE`, `MUSIC`, `EFFECTS`, `MUTED`), ghi sau lần chỉnh cuối khoảng nửa giây; khởi động lại vẫn giữ.
- **Lệnh WebSocket:** `{"type":"volume","channel":"master|voice|music|effects","volume":0-100,"save":true}`, `{"type":"volume_step","channel":"master","delta":5}`, `{"type":"mute","muted":true}` (bỏ `muted` để đảo). Kênh lạ bị từ chối, số ngoài 0-100 bị cắt về giới hạn.

## Chế độ Nghỉ

Công tắc lớn **Đang thức / Đang nghỉ** nằm ở thanh trên cùng (mọi tab, dùng được trên điện thoại; trên điện thoại nó nằm cạnh tên robot). Bấm để đổi; trạng thái hiện chữ rõ ("Đang nghỉ · Bấm để thức dậy") và mọi trình duyệt đang mở đổi theo cùng lúc (WebSocket). Bấm lặp trong vòng 1 giây bị bỏ qua.

**Khi bật Nghỉ** (`src/core/rest_mode.py`, `src/plugins/rest.py`):

1. Robot đang nói thì dừng ngay: gửi `abort` cho server, xóa hàng đợi giọng đang phát.
2. **Đóng hẳn phiên trò chuyện với server** (đóng kênh âm thanh WebSocket). Không còn gói mic nào gửi lên, không có lượt STT/hội thoại mới nên không tốn hạn mức xiaozhi.me. Mọi đường mở lại phiên (từ khóa đánh thức, chạm màn hình, tin nhắn chữ từ web, `AlwaysListen`, lời chào) đều bị chặn trong `ConversationSession.connect_protocol`, và gói mic bị chặn ở `StateManager.should_capture_audio`. Thông báo âm thanh của server (`notify`) cũng bị bỏ qua. Với giao thức MQTT, kênh điều khiển (không phải phiên hội thoại) vẫn giữ như thường lệ; WebSocket mặc định của Pi không có kênh nào mở.
3. Mặt chuyển sang biểu cảm **ngủ** có sẵn (mắt nhắm, chữ "zzz"). Chạm màn hình, âm thanh hay nhạc không đánh thức mặt. `FACE.AUTO_MODE` / `ALWAYS_LISTEN` không bị sửa trong config, nhưng không có gì được phép nghe cho tới khi thức.
4. Dừng bám người (nhớ trạng thái bật/tắt trước đó), đầu từ từ về giữa rồi cúi nhẹ **-15°** (không vượt `SERVO.TILT_LIMIT`).
5. **Nhạc vẫn phát** nếu đang phát, và vẫn điều khiển được từ web. Xoay đầu, cử chỉ, âm lượng trên web vẫn dùng được; chỉ không bật bám người, không gửi tin nhắn chữ, không "đánh thức mặt" trong lúc nghỉ (trang báo lý do).

**Khi thức dậy:** cho phép nghe lại; `AlwaysListen` tự mở lại phiên và nghe sau khoảng 0,5 s. Mặt hết ngủ và có một vẻ mặt vui ngắn (2,5 s) thay cho lời chào, không bắt robot nói gì để khỏi tốn lượt. Bám người trở lại như trước khi nghỉ, đầu về giữa.

**Lưu trạng thái:** `FACE.RESTING` trong `config.json` (ghi mỗi lần đổi). Khởi động lại khi đang nghỉ thì vẫn nghỉ: không kết nối, không chào, mặt ngủ, đầu cúi. Nhật ký `app.log` ghi các dòng `Chế độ NGHỈ: VÀO nghỉ / RA khỏi nghỉ (nguồn: web|voice|khởi động)`.

**Bằng giọng nói:** nói "đi ngủ đi" / "nghỉ đi" thì tool `self.robot.go_to_rest` đặt lịch nghỉ; robot chúc ngủ ngon xong (khoảng 2,5 s sau khi server báo hết câu) mới vào nghỉ, tối đa chờ 25 s. **Không có lệnh giọng nói để thức**, vì lúc nghỉ robot không nghe; chỉ đánh thức bằng công tắc web.

**Lệnh WebSocket:** `{"type":"rest","enabled":true|false}`; `enabled` phải là `true`/`false` (kiểm tra lại ở phía Pi). Trạng thái nằm trong `state.rest` = `{resting, since, source, pending}`.

## Lọc câu ảo giác và chỉ gửi micro khi có tiếng nói

Server nhận dạng giọng (STT) đôi khi "nghe ra" câu kết video YouTube từ tiếng ồn, im lặng hay nhạc: "Hãy subscribe cho kênh Ghiền Mì Gõ để không bỏ lỡ những video hấp dẫn", "La La School", "Cảm ơn các bạn đã theo dõi và hẹn gặp lại". Có hai lớp chặn, cả hai nằm ở tab **Trò chuyện**.

### 1. Lọc câu ảo giác (`STT_FILTER`)

**Quy tắc** (`src/audio_processing/stt_filter.py`):

1. Câu và mẫu được chuẩn hóa: chữ thường, bỏ dấu (đ → d), bỏ dấu câu, tách từ. Khoảng trắng không tính khi so chữ, nên "lala" = "la la".
2. Mỗi mẫu được tìm như một đoạn từ liên tiếp, cho phép sai chính tả: mẫu ≤ 4 chữ cái phải khớp đúng; mẫu dài hơn được sai khoảng 1 chữ trên 4 chữ cái, tối đa 3 ("subcribe", "la la scool", "Diền Mì Gõ"). Với mẫu nhiều từ, đoạn khớp mờ phải có ít nhất một nửa số từ đúng hẳn ("chiên mì gói" không bị coi là "Ghiền Mì Gõ").
3. Câu **bị bỏ qua** khi có ít nhất một mẫu khớp **và** mọi từ còn lại chỉ là từ đệm (hãy, cho, kênh, của, mình, các, bạn, nhé, và, để, những, video, tiếp theo, youtube, …), hoặc khi cả câu có ít hơn `MIN_CHARS` chữ cái (mặc định 2).
4. Còn lại là yêu cầu thật và robot trả lời bình thường: "mở nhạc La La School" còn "mở nhạc", "tìm kênh youtube học tiếng anh" còn "tìm học tiếng anh". Câu dài hơn 30 từ không bao giờ bị bỏ qua. Câu người dùng gõ trên web (và lời chào) cũng không bao giờ bị lọc.

**Mẫu mặc định** (37 mẫu, `DEFAULT_PATTERNS`): La La School; Ghiền Mì Gõ; Hãy subscribe cho kênh (Ghiền Mì Gõ); subscribe / subcribe; subscribe kênh YouTube; kênh YouTube; đăng ký/đăng kí kênh; đừng quên đăng ký kênh; nhấn nút đăng ký; bấm/nhấn chuông thông báo; theo dõi kênh; ủng hộ kênh; ủng hộ mình; like và share; like share và subscribe; chia sẻ video; để không bỏ lỡ những video hấp dẫn / mới nhất; cảm ơn các bạn đã theo dõi (và hẹn gặp lại); cảm ơn các bạn đã xem (video); cảm ơn các bạn đã lắng nghe; hẹn gặp lại các bạn (trong (những) video tiếp theo); chúc các bạn xem video vui vẻ; Vietsub; phụ đề bởi; Thanks/Thank you for watching; Subtitles by the Amara.org community; hai câu tiếng Trung hay gặp của Whisper.

**Khi một câu bị bỏ qua** (`src/core/stt_gate.py`, `ConversationSession.drop_hallucinated_turn`):

- Câu không hiện như lời người nói; khung chat hiện câu đó màu xám với nhãn **đã bỏ qua** (di chuột để xem mẫu khớp). `app.log` ghi `STT filter: ignored '<câu>' (matched: <mẫu>)`.
- Robot gửi `abort`, xóa hàng đợi giọng. Mặt giữ nguyên trạng thái nghe, không có câu trả lời, không phát âm thanh, không chạy tool (lệnh `tools/call` của lượt đó được trả lỗi "Cancelled" để server không chờ).
- **Đo trên server chính thức:** `abort` không hủy câu trả lời. Server trả `tts stop` ngay rồi vẫn gửi `llm`, các câu `tts sentence_start` và toàn bộ âm thanh. Vì vậy cổng chặn **mọi** `tts`/`llm`/`tools/call`/khung âm thanh cho tới khi có câu STT thật tiếp theo, tin nhắn gõ tay, hoặc 30 s. Micro đóng trong lúc server còn gửi câu trả lời, và mở lại (gửi `listen start`) khi luồng đó đã im 2 s (tối đa 15 s).
- **Vẫn tốn hạn mức:** khi câu STT về tới robot thì server đã chạy STT và LLM cho lượt đó. Lọc chỉ giúp robot im lặng. Chỉ VAD (phần dưới) mới bớt lượt gửi lên server.

**Mảnh câu ảo giác và đuôi câu vừa bỏ** (server vẫn giữ câu ảo giác trong lịch sử hội thoại nên một mảnh của nó, như "Để không bỏ lỡ.", dễ bị trả lời như lời thật):

- `FRAGMENTS`: các mảnh đã biết ("để không bỏ lỡ", "những video hấp dẫn", "bấm chuông", "và hẹn gặp lại", …). Câu bị bỏ qua khi **cả câu** chính là một mảnh (mờ như mẫu thường, có thể thêm từ đệm cuối như "nhé"). "để không bỏ lỡ cuộc hẹn" hay "hẹn gặp lại" vẫn được trả lời.
- Câu ngắn (2–10 từ) đến trong vòng 20 s sau một lượt bị bỏ qua và chỉ là một đoạn từ liền nhau của câu vừa bị bỏ (cũng so mờ) cũng bị bỏ qua, dù không có trong danh sách. Log: `matched: đuôi câu vừa bỏ: ...`.
- Danh sách mảnh chỉ sửa trong `config.json` (`STT_FILTER.FRAGMENTS`); thiếu khóa này thì dùng mặc định.

**Lọc câu trả lời của robot** (`STT_FILTER.REPLY_FILTER`, mặc định bật; công tắc "Lọc câu trả lời" trên web):

- Trước khi hiện chữ và phát âm thanh, mỗi câu `tts sentence_start` được so với `REPLY_PATTERNS` (hẹp hơn `PATTERNS`: "La La School", "Ghiền Mì Gõ", "subscribe kênh", "đăng ký kênh", "bấm chuông", "không bỏ lỡ những video", …; mờ, tìm ở bất kỳ chỗ nào trong câu). Khớp thì câu đó bị bỏ cùng `sentence_end` và các gói âm thanh của nó (tới câu kế tiếp); các câu khác của lượt vẫn nói bình thường. Câu bị bỏ hiện trong "Câu đã bỏ qua gần đây" với nhãn "câu trả lời: ...". Log: `STT filter: robot sentence not spoken '<câu>'`.
- Không chặn khi chính câu người nói gần nhất đã nhắc mẫu đó ("mở nhạc La La School" → "Đang mở La La School" vẫn nói).
- Dựa vào việc server gửi `sentence_start` trước âm thanh của câu (đúng trên server chính thức).

**Làm mới ngữ cảnh server** (`STT_FILTER.RESET_CONTEXT`, mặc định **tắt**, công tắc trên web, thử nghiệm): giao thức không có thông điệp xóa lịch sử. Cách duy nhất từ phía robot là đóng kênh âm thanh rồi mở lại (hello mới = phiên mới). Khi bật, sau khi nghe lại bình thường sau một lượt bị bỏ qua, robot đóng và mở lại kênh. Chưa kiểm chứng được server có quên câu cũ hay không, nên mặc định tắt; hai lớp lọc trên đã đủ để robot im lặng dù server còn nhớ.

**Trên web:** công tắc bật/tắt; số câu đã bỏ qua; mục "Câu đã bỏ qua gần đây" (20 câu mới nhất, nút **Làm mẫu** để thêm câu đó vào danh sách); mục "Mẫu" hiện các mẫu dạng thẻ (bấm × để xóa), ô thêm mẫu, số chữ cái tối thiểu, nút **Khôi phục mẫu mặc định**. Mọi thay đổi được lưu vào `config.json`.

**Config:**

```json
"STT_FILTER": {"ENABLED": true, "PATTERNS": ["La La School", "..."], "MIN_CHARS": 2,
  "FRAGMENTS": ["để không bỏ lỡ", "..."], "REPLY_FILTER": true, "REPLY_PATTERNS": ["La La School", "..."],
  "RESET_CONTEXT": false}
```

**Lệnh WebSocket** (Pi kiểm tra lại mọi giá trị):

| Lệnh | Ý nghĩa |
|---|---|
| `{"type":"stt_filter","enabled":true,"min_chars":2,"reply_filter":true,"reset_context":false}` | Bật/tắt bộ lọc, đổi số chữ cái tối thiểu (0–20), bật/tắt lọc câu trả lời, bật/tắt làm mới phiên server; trường nào cũng tùy chọn |
| `{"type":"stt_pattern_add","pattern":"..."}` | Thêm mẫu (tối đa 120 ký tự, phải có chữ, tối đa 200 mẫu, trùng thì bỏ qua) |
| `{"type":"stt_pattern_remove","pattern":"..."}` | Xóa mẫu |
| `{"type":"stt_patterns_reset"}` | Về danh sách mặc định |
| `{"type":"stt_recent_clear"}` | Xóa danh sách câu đã bỏ qua gần đây |

Trạng thái: `state.stt_filter` = `{enabled, patterns, min_chars, fragments, reply_enabled, reply_patterns, reset_context, ignored_total, recent, defaults, gate}`.

### 2. Chỉ gửi micro khi có tiếng nói (`VAD`)

Trước đây, khi đang nghe, mọi khung micro 20 ms đều được gửi lên server. Giờ `src/audio_processing/vad_gate.py` đứng giữa bộ mã hóa Opus và đường gửi (`AudioCodec.set_input_gate`):

- **Bộ phát hiện:** Silero VAD (`models/vad/silero_vad.onnx`, giấy phép MIT) chạy qua `sherpa-onnx` có sẵn, cửa sổ 32 ms, tốn khoảng 0,3 ms mỗi khung trên Pi 5. Không nạp được mô hình thì dùng ngưỡng năng lượng (cao hơn mức ồn nền 12 dB).
- Một khung là "tiếng nói" khi Silero ≥ `THRESHOLD` **và** mức âm ≥ `ENERGY_DB`.
- Chỉ mở khi tiếng nói kéo dài liền ≥ `MIN_SPEECH_MS` (cho phép hở tối đa 60 ms). Khi mở, gửi kèm `PREROLL_MS` âm thanh ngay trước đó để không mất chữ đầu. Đóng sau `HANGOVER_MS` không có tiếng nói.
- Không gửi khi robot đang nói (như cũ: chỉ gửi ở trạng thái nghe), và không gửi khi đang bỏ một lượt ảo giác.

| Tham số | Mặc định | Giới hạn | Ý nghĩa |
|---|---|---|---|
| `ENABLED` | `true` | | Tắt thì gửi liên tục như trước |
| `THRESHOLD` | 0.5 | 0.1–0.95 | Độ nhạy Silero (cao = khó mở hơn) |
| `MIN_SPEECH_MS` | 300 | 60–2000 | Thời lượng nói tối thiểu để mở |
| `PREROLL_MS` | 300 | 0–1000 | Âm thanh gửi kèm trước lúc mở |
| `HANGOVER_MS` | 900 | 200–3000 | Im bao lâu thì đóng |
| `ENERGY_DB` | -50 | -80 – -10 | Mức âm tối thiểu (dBFS) |

**Trên web:** công tắc, chấm trạng thái trực tiếp (đang gửi / đang chờ tiếng nói), mức ồn nền, mức tiếng nói gần nhất, tỷ lệ thời gian đã gửi, các thanh trượt. Kéo thanh thì áp dụng ngay, thả tay mới lưu config.

**Lệnh WebSocket:** `{"type":"vad","enabled":true,"threshold":0.5,"min_speech_ms":300,"preroll_ms":300,"hangover_ms":900,"energy_db":-50,"save":true}`. Mọi trường đều tùy chọn; `save:false` chỉ áp dụng, không ghi config. Trạng thái: `state.vad`.

### Thử nghiệm trên robot thật (không cần người nói)

`XIAOZHI_MIC_INJECT_FILE=/đường/dẫn.wav` (16 kHz, mono, 16-bit) thay micro bằng file đó (phát lặp, `src/audio_codecs/mic_inject.py`). File được đọc lại khi đổi, nên có thể đổi file bằng `mv` mà không khởi động lại. Không có file thì dùng micro thật. Đây chỉ là công cụ test; app ghi cảnh báo khi biến này được đặt.

```bash
systemctl --user set-environment XIAOZHI_MIC_INJECT_FILE=/tmp/xz-inject.wav && systemctl --user restart xiaozhi
cp speech.wav /tmp/x.wav && mv /tmp/x.wav /tmp/xz-inject.wav     # đổi âm thanh đầu vào
systemctl --user unset-environment XIAOZHI_MIC_INJECT_FILE && systemctl --user restart xiaozhi   # trả lại micro
```

### Kết quả đo (Pi 5, server chính thức, 2026-11-23)

Mỗi lần đo 2 phút; "nhạc" là nhạc tổng hợp bằng numpy/ffmpeg (không có bản quyền). Phòng đặt robot có tiếng người/video thật ở xa, nên "im lặng" ở phòng thật không hoàn toàn im.

| Điều kiện | Lượt STT | Bị lọc bỏ | Robot trả lời câu ảo giác |
|---|---|---|---|
| Phòng thật, im lặng, **trước** | 7 | 0 | 7 (gồm "Cảm ơn các bạn đã theo dõi và hẹn gặp lại" ×2, "Hãy subscribe cho kênh Ghiền Mì Gõ…") |
| Phòng thật, nhạc qua loa, **trước** | 2 | 0 | 2 ("Hãy subscribe cho kênh La La School…") |
| Phòng thật, im lặng, **sau** | 5 | 5 | 0 |
| Phòng thật, nhạc qua loa, **sau** | 4 | 3 | 0 (1 câu người thật trong phòng được trả lời đúng) |
| Bơm ồn nền -55 dBFS, VAD+lọc tắt / bật | 0 / 0 | – | 0 / 0 |
| Bơm nhạc tổng hợp, VAD+lọc tắt / bật | 0 / 0 | – | 0 / 0 |
| Bơm câu tổng hợp "Kể cho tôi nghe một câu chuyện cười ngắn", VAD tắt / bật | 1 / 1 | 0 | nhận đủ câu cả hai lần |
| Bơm câu tổng hợp "Hãy subscribe cho kênh Ghiền Mì Gõ…", "Cảm ơn các bạn đã theo dõi và hẹn gặp lại…" | 1 + 1 | 2 | 0: không phát âm thanh, mặt không chuyển sang nói, không chạy tool |

Trong cả hai điều kiện có lời "Có vẻ bạn đã im lặng một lúc rồi… tạm biệt" của server sau khoảng 1 phút không có câu nào. Đó là câu server tự gửi (không có STT), không phải ảo giác.

VAD giảm phần thời gian gửi micro (ồn nền: không mở lần nào; phòng thật: chỉ gửi khi có tiếng người). Nhưng phòng này thường xuyên có tiếng người thật, nên VAD không giảm được số lượt STT ở đây; lớp lọc là thứ làm robot im lặng.

Unit test: `pytest tests/test_stt_filter.py`.

## Ghi và phát lại động tác

1. Bấm **Ghi**.
2. Kéo đầu robot (trên mô hình, thanh trượt hoặc joystick). Robot làm theo và Pi ghi lại các góc.
3. Đặt tên, bấm **Lưu**.

File JSON được lưu ở `~/.local/share/py-xiaozhi/motions/<tên>.json` (tối đa 120 s, 4000 khung). Khi phát lại, góc vẫn bị giới hạn theo cấu hình hiện tại.

## Mật khẩu

Đặt `WEB.PASSWORD` thành một chuỗi khác rỗng. Trang sẽ hỏi mật khẩu, và phiên đăng nhập là cookie, hết khi app khởi động lại.

Đây chỉ là lớp bảo vệ trong LAN: HTTP không mã hóa, nên đừng mở cổng này ra Internet. WebSocket chỉ nhận kết nối từ chính trang này (kiểm tra `Origin`), để trang web lạ trong cùng trình duyệt không điều khiển được robot.

## HTTPS (cho chế độ nghiêng điện thoại)

Trình duyệt chỉ cho đọc cảm biến nghiêng (DeviceOrientation) trên HTTPS.

1. Đặt `"HTTPS_ENABLED": true`.
2. Khởi động lại app. Lần đầu app gọi `openssl` để tạo chứng chỉ tự ký vào `~/.local/share/py-xiaozhi/web_cert/` (khóa riêng quyền 600, nằm ngoài repo, không commit).
3. Mở **https://Pi5.local:8443**. Trình duyệt sẽ cảnh báo chứng chỉ: chọn "Nâng cao" → "Tiếp tục".
4. Trên iPhone, bấm "Bắt đầu" để cấp quyền cảm biến.

Khi đó HTTP 8080 vẫn chạy song song. Mở trang bằng HTTP thì chế độ nghiêng bị ẩn và trang hiện đường link HTTPS.

Muốn tạo lại chứng chỉ (ví dụ sau khi đổi IP), xóa thư mục `web_cert` rồi khởi động lại app.

## Thử trên laptop, không cần robot

```bash
python scripts/web_panel_dev.py --port 8080      # servo, camera, app đều giả lập
QT_QPA_PLATFORM=offscreen python scripts/web_panel_dev.py --port 8080 --real-face   # thêm mặt QML thật + chụp hình như trên Pi
python scripts/web_panel_check.py http://127.0.0.1:8080 --out shots/
python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --max-deg 20   # robot thật, góc nhỏ
python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --face-only    # chỉ kiểm tra mặt/màu mắt
python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --music-only   # chỉ kiểm tra nhạc (có tiếng)
python scripts/web_panel_dev.py --port 8080 --no-audio                              # xem trang khi robot không có loa
```

Phần kiểm tra mặt đổi biểu cảm và màu mắt từ trang (bảng màu, màu tự do, giá trị sai, theo cảm xúc, cầu vồng), đo màu mắt trong ô mặt của trang, rồi trả lại màu và chế độ ban đầu của robot.

`web_panel_check.py` cần Playwright: `pip install playwright && python -m playwright install chromium`. Đây là công cụ test, không phải thư viện của app.

Unit test: `pytest tests/test_web_panel.py tests/test_eye_color.py tests/test_music_library.py tests/test_stt_filter.py`.

Mô hình `web_assets/xiaozhi_robot.glb` là bản lắp ráp CAD (glTF, đơn vị mm). Cây node:

```
robot (xoay -90° quanh X) → pan_pivot → turn → upper → tilt_pivot → head_shell, bezel, webcam…
```

Quy ước của mô hình:

- `pan_pivot.rotation.z = +pan`: dương là robot quay sang trái của nó.
- `tilt_pivot.rotation.x = -tilt`: tilt dương là ngẩng lên.
- Hai pivot nằm đúng trục thật.

Trang tự đặt mô hình đứng trên sàn và tô màu theo tên node (file CAD không có vật liệu). Hướng mặt robot được tính từ node `bezel`, và trang đặt một tấm màn hình sau khung đó, dán hình mặt robot (xem "Mặt robot trên trang web").

`scripts/build_robot_glb.py` vẫn còn nhưng không dùng mặc định. Nó sinh mô hình khối đơn giản ra `web_assets/xiaozhi_robot_simple.glb`, chỉ để dự phòng. Mô hình đó ghi chiều quay riêng của nó bằng glTF extras `{"sign": 1}` trên hai pivot.

## Giới hạn hiện tại

- **Góc "thật" là ước lượng.** Firmware `servo_bridge` chỉ trả `OK <pan> <tilt>`, tức là góc đích nó đã nhận, không đọc được vị trí servo. Trang hiện góc board đã xác nhận, mô phỏng thêm tốc độ ramp của firmware (150°/s ở servo). Servo bị kẹt hoặc mất nguồn thì trang không biết.
- **Biểu cảm chọn từ web giữ đến lần đổi trạng thái kế tiếp** (nghe/nói), giống biểu cảm server gửi. Với `FACE.ALWAYS_LISTEN` thì trạng thái đổi theo từng lượt nói.
- `FACE.AUTO_MODE` ở đây là chế độ trò chuyện tự động (chạm một lần để nghe liên tục), không phải chế độ tự đổi biểu cảm. Khi `FACE.ALWAYS_LISTEN` bật, robot luôn ở chế độ tự động, nên công tắc bị khóa.
- Ô mặt trên web chỉ có phần mắt (vùng `RobotFace`, 72% trên của màn); chữ phụ đề và ảnh camera nhỏ ở góc màn robot không được gửi.
- Hình mặt trên web trễ khoảng 0,1–0,2 s và mờ hơn màn thật (320 px, JPEG).
- **Tin nhắn chữ** được gửi như câu người dùng nói (`listen detect`). Server trả lời bằng chữ và giọng; phần giọng cần loa.
- Mỗi trình duyệt gửi tối đa 40 lệnh mỗi giây. Lệnh góc khi kéo được gửi tối đa 20 lần mỗi giây.
