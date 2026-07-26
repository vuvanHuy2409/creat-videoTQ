# 🀄 Nhận Diện Chữ Trung Quốc từ Video

Ứng dụng desktop sử dụng **Pipeline AI hai tầng**:  
`Video Frame` → `YOLOv8` (phát hiện vùng chữ) → `EasyOCR` (đọc chữ Trung Quốc)

---

## 📦 Cài Đặt

```bash
pip install -r requirements.txt
```

> Nếu dùng Apple Silicon (M1/M2) hoặc NVIDIA GPU, EasyOCR sẽ tự động dùng GPU để tăng tốc.

---

## 🚀 Chạy Ứng Dụng

```bash
python main.py
```

---

## 🖥️ Hướng Dẫn Sử Dụng

1. **Chờ tải mô hình** – Khi khởi động, app sẽ tự tải YOLO và EasyOCR (lần đầu có thể mất 1–2 phút do tải file weights từ internet).
2. **Chọn video** – Nhấn nút `📂 Chọn Video`, chọn file `.mp4`, `.avi`, `.mov`...
3. **Điều chỉnh ngưỡng** – Kéo thanh **Ngưỡng YOLO** (mặc định 0.4). Giá trị thấp → phát hiện nhiều hơn nhưng có thể nhầm; giá trị cao → chỉ giữ kết quả chắc chắn.
4. **Bắt đầu** – Nhấn `▶ Bắt Đầu Nhận Diện`.
5. **Xem kết quả** – Bảng bên phải hiển thị `[timestamp]  chữ Trung  (độ tin cậy)`.
6. **Xuất CSV** – Nhấn `💾 Xuất CSV` để lưu toàn bộ kết quả ra file Excel-compatible.

---

## 📂 Cấu Trúc Dự Án

```
nhận diện chữ/
├── main.py          # Giao diện Tkinter
├── pipeline.py      # Logic YOLO + EasyOCR (chạy trong thread nền)
├── requirements.txt # Thư viện cần cài
└── README.md        # File này
```

---

## ⚙️ Nâng Cấp Nếu Cần

| Mục tiêu | Cách làm |
|---|---|
| Nhận diện biển số xe Trung Quốc chính xác hơn | Thay `yolov8n.pt` bằng `best.pt` (mô hình tự train) |
| Tăng tốc trên Mac M1/M2 | Đặt `gpu=True` trong `pipeline.py` |
| Nhận diện thêm tiếng Việt | Thêm `'vi'` vào list ngôn ngữ EasyOCR |
| Lưu video kết quả (có bounding box) | Thêm `cv2.VideoWriter` vào `pipeline.py` |

---

## 🔬 Giải Thích Pipeline

```
┌─────────────┐     ┌─────────────────────┐     ┌──────────────────┐
│  Frame ảnh  │────▶│  YOLO: Phát hiện    │────▶│  Cắt ảnh (crop)  │
│  từ video   │     │  vùng có chữ (bbox) │     │  frame[y1:y2,    │
└─────────────┘     └─────────────────────┘     │  x1:x2]          │
                                                  └────────┬─────────┘
                                                           │
                                                           ▼
                                              ┌────────────────────────┐
                                              │  EasyOCR: Đọc chữ     │
                                              │  Trung từ ảnh cắt ra  │
                                              └────────────┬───────────┘
                                                           │
                                                           ▼
                                              ┌────────────────────────┐
                                              │  Hiển thị bbox + text  │
                                              │  lên canvas Tkinter    │
                                              └────────────────────────┘
```
