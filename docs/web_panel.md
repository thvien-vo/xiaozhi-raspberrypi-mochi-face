# Trang web điều khiển robot

Trang web chạy ngay trong app Xiaozhi (plugin `web_panel`). Từ laptop hoặc điện thoại trong cùng mạng LAN, bạn có thể:

- xoay đầu robot trên mô hình 3D;
- xem webcam có khung YOLO;
- đổi biểu cảm mặt;
- chạy cử chỉ, ghi và phát lại động tác;
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
| Mặt | 21 biểu cảm mà mặt robot vẽ được, bật/tắt `FACE.AUTO_MODE`, "Đánh thức mặt" |
| Trò chuyện | Câu người nói (STT) và câu robot trả lời (TTS), ô gửi tin nhắn chữ |

**Chiều kéo:** kéo đầu sang trái thì robot quay sang trái của nó, kéo lên thì ngẩng. Nhìn từ góc nào cũng vậy, giống thanh trượt và joystick; chữ "Trái/Phải" đều tính theo robot.

**Quy ước góc:** pan dương là robot quay sang trái của nó, tilt dương là ngẩng lên. Giới hạn: pan ±90°, tilt ±45° (`SERVO.PAN_LIMIT`, `TILT_LIMIT`).

## Ai được điều khiển đầu

Thứ tự ưu tiên: **Web** (người đang kéo) > **lệnh giọng nói** > **bám người YOLO**.

- Mỗi lệnh từ web giữ quyền thêm `SERVO.MANUAL_HOLD_SEC` giây (mặc định 5). Trong thời gian đó, lệnh giọng nói bị từ chối và bám người tạm dừng. Hết thời gian không thao tác thì quyền được nhả.
- Đóng trang web cũng nhả quyền sau thời gian đó.
- Cử chỉ và động tác chạy trên Pi, nên đóng trình duyệt chúng vẫn chạy tiếp. Kéo đầu trên web sẽ dừng động tác đang chạy.

Lệnh giọng nói mới (MCP `self.head.gesture`): "gật đầu đi", "lắc đầu đi", "nhìn quanh xem", "giả vờ ngạc nhiên", "nhảy đi". Chúng chạy đúng các cử chỉ trên trang web.

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
python scripts/web_panel_check.py http://127.0.0.1:8080 --out shots/
python scripts/web_panel_check.py http://Pi5.local:8080 --out shots/ --max-deg 20   # robot thật, góc nhỏ
```

`web_panel_check.py` cần Playwright: `pip install playwright && python -m playwright install chromium`. Đây là công cụ test, không phải thư viện của app.

Unit test: `pytest tests/test_web_panel.py`.

Mô hình `web_assets/xiaozhi_robot.glb` là bản lắp ráp CAD (glTF, đơn vị mm). Cây node:

```
robot (xoay -90° quanh X) → pan_pivot → turn → upper → tilt_pivot → head_shell, bezel, webcam…
```

Quy ước của mô hình:

- `pan_pivot.rotation.z = +pan`: dương là robot quay sang trái của nó.
- `tilt_pivot.rotation.x = -tilt`: tilt dương là ngẩng lên.
- Hai pivot nằm đúng trục thật.

Trang tự đặt mô hình đứng trên sàn và tô màu theo tên node (file CAD không có vật liệu). Hướng mặt robot được tính từ node `bezel`, và trang vẽ thêm màn hình đen có hai mắt sau khung đó.

`scripts/build_robot_glb.py` vẫn còn nhưng không dùng mặc định. Nó sinh mô hình khối đơn giản ra `web_assets/xiaozhi_robot_simple.glb`, chỉ để dự phòng. Mô hình đó ghi chiều quay riêng của nó bằng glTF extras `{"sign": 1}` trên hai pivot.

## Giới hạn hiện tại

- **Góc "thật" là ước lượng.** Firmware `servo_bridge` chỉ trả `OK <pan> <tilt>`, tức là góc đích nó đã nhận, không đọc được vị trí servo. Trang hiện góc board đã xác nhận, mô phỏng thêm tốc độ ramp của firmware (150°/s ở servo). Servo bị kẹt hoặc mất nguồn thì trang không biết.
- **Biểu cảm chọn từ web giữ đến lần đổi trạng thái kế tiếp** (nghe/nói), giống biểu cảm server gửi. Với `FACE.ALWAYS_LISTEN` thì trạng thái đổi theo từng lượt nói.
- `FACE.AUTO_MODE` ở đây là chế độ trò chuyện tự động (chạm một lần để nghe liên tục), không phải chế độ tự đổi biểu cảm. Khi `FACE.ALWAYS_LISTEN` bật, robot luôn ở chế độ tự động, nên công tắc bị khóa.
- Mặt robot không có tham số màu mắt hay tham số đổi được lúc chạy, nên trang không có các mục đó.
- **Tin nhắn chữ** được gửi như câu người dùng nói (`listen detect`). Server trả lời bằng chữ và giọng; phần giọng cần loa.
- Mỗi trình duyệt gửi tối đa 40 lệnh mỗi giây. Lệnh góc khi kéo được gửi tối đa 20 lần mỗi giây.
