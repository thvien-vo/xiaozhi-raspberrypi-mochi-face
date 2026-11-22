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
| Thanh trạng thái | Trạng thái WebSocket, servo đã nối chưa, app đang rảnh/nghe/nói, ai đang điều khiển đầu |
| Khung 3D | Mô hình đặc là góc thật robot báo về. Mô hình cam mờ là góc đích. **Kéo vào đầu robot**: ngang để xoay, dọc để gật. Kéo chỗ trống để xoay góc nhìn, cuộn để phóng to. Nút Trước/Sau/Trên đổi góc nhìn, "Nhìn thẳng" đưa đầu về giữa. |
| Điều khiển | Thanh trượt xoay/gật, joystick, chế độ nghiêng điện thoại, bật/tắt bám người, chỉnh KP và vùng chết khi đang chạy ("Lưu vào cấu hình" ghi vào `SERVO.*`) |
| Động tác | Cử chỉ: gật đầu, lắc đầu, nhìn quanh, ngạc nhiên, nhảy. Ghi động tác, danh sách phát lại/xóa. |
| Camera | Webcam MJPEG có khung YOLO. Khung cam là người robot bám theo. |
| Mặt | Ô "Màn hình robot" (mặt đang hiện trên màn 7"), 21 biểu cảm, màu mắt (bảng màu, tự chọn, cầu vồng, theo cảm xúc), bật/tắt `FACE.AUTO_MODE`, "Đánh thức mặt" |
| Nhạc | Thư viện nhạc trên robot: tải lên, xóa, tìm, phát, tua, âm lượng, lặp/trộn, hàng đợi, nhảy theo nhạc (xem "Nhạc") |
| Trò chuyện | Câu người nói (STT) và câu robot trả lời (TTS), ô gửi tin nhắn chữ |

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
- **Phát:** bấm một bài; cả thư viện thành hàng đợi bắt đầu từ bài đó. Có tạm dừng, bài trước (đã nghe quá 4 giây thì về đầu bài), bài sau, tua, dừng hẳn, âm lượng (riêng cho nhạc, không đổi giọng robot), lặp (không/cả danh sách/một bài), trộn bài.
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

Khi robot nói, nhạc tự nhỏ lại rồi to dần trở lại (`MUSIC.DUCK_WHILE_SPEAKING`). Robot chỉ nghe được lệnh khi đang ở trạng thái nghe, và micro cũng nghe thấy nhạc: nếu robot khó nghe lệnh thì giảm âm lượng nhạc hoặc đứng gần hơn.

Cấu hình (`config.json`, mục `MUSIC`): `LIBRARY_DIR` (trống = thư mục mặc định), `MAX_UPLOAD_MB`, `MAX_LIBRARY_MB`, `VOLUME`, `REPEAT`, `SHUFFLE`, `DANCE`, `FACE_BOUNCE`, `DUCK_WHILE_SPEAKING`, `ONLINE_ENABLED`. Nguồn nhạc trực tuyến cũ (API không chính thức) **mặc định tắt**: máy chủ của nó hỏng, nhạc có bản quyền bị khóa.

Giải mã cần ffmpeg. Pi không có sẵn nên app dùng bản đi kèm gói `imageio-ffmpeg` (trong `pyproject.toml`), không cần quyền root.

An toàn: tên file tải lên được làm sạch, chỉ nhận đúng đuôi và đúng nội dung âm thanh (chữ ký file + đọc thử bằng mutagen), ghi vào thư mục tạm rồi mới đưa vào thư viện, không ghi đè bài có sẵn. Trang chỉ dùng mã bài (không bao giờ đường dẫn), liên kết tượng trưng bị bỏ qua, nên không đọc/xóa được file ngoài thư mục nhạc. Đường tải lên cần đăng nhập (nếu có mật khẩu) và chỉ nhận từ chính trang này.

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

Unit test: `pytest tests/test_web_panel.py tests/test_eye_color.py tests/test_music_library.py`.

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
