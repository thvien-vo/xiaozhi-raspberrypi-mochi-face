# servo_bridge (Yolo UNO / ESP32-S3)

Firmware cho đầu robot pan/tilt: Pi 5 gửi góc qua USB Serial, board điều khiển 2 servo MG996R.
Nạp bằng Arduino IDE với thư viện **ESP32Servo**, `Tools -> USB CDC On Boot: Enabled`.

## Nối dây

| Servo | Chân Yolo UNO | Ghi chú |
|---|---|---|
| Pan (xoay ngang) | GPIO18 / D9 | bánh răng 1:1, servo 90 = nhìn thẳng, 0..180 = -90..+90 độ |
| Tilt (gật) | GPIO17 / D8 | bánh răng 1:2, servo 90 = đầu thẳng, 0..180 = -45..+45 độ |

Servo cấp nguồn bằng adapter 5 V 4 A riêng, **chung GND** với Yolo UNO. Không cấp servo từ USB.

## Giao thức (115200 baud, mỗi lệnh một dòng)

| Hướng | Nội dung |
|---|---|
| Pi -> board | `<pan> <tilt>\n`, hai số nguyên 0..180 (góc servo) |
| board -> Pi | `OK <pan> <tilt>` khi nhận lệnh, `ERR ...` khi sai cú pháp |
| khi khởi động | `READY servo_bridge`, về `90 90` |

Board tự quay mượt tới góc đích (3 độ mỗi 20 ms, khoảng 150 độ/s).

## Phía Pi

- `deploy/raspberry-pi/99-yolo-uno.rules` tạo `/dev/yolo_uno` (VID 303a) và báo ModemManager bỏ qua cổng.
- `src/servo/servo_controller.py` mở cổng với DTR/RTS tắt (tránh reset hoặc vào chế độ nạp code),
  gửi tối đa 20 lệnh/s, tự kết nối lại khi rút/cắm USB.
- Cấu hình ở mục `SERVO` (xem `src/utils/config_manager.py`). Thử nhanh: `.venv/bin/python scripts/servo_test.py`.
