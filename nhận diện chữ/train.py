"""
train.py
══════════════════════════════════════════════════════════════════
Script huấn luyện YOLOv8 để phát hiện chữ Trung Quốc.

Quy trình:
  1. Kiểm tra dataset (nếu chưa có → gợi ý chạy prepare_dataset.py)
  2. Cấu hình hyperparameter
  3. Huấn luyện YOLOv8
  4. Đánh giá trên tập val/test
  5. Sao chép best.pt về thư mục gốc để dùng trong main.py

Sau khi chạy xong, file best.pt sẽ nằm ở:
  ./best.pt   (thư mục hiện tại, cùng chỗ với main.py)
══════════════════════════════════════════════════════════════════
"""

import os
import sys
import shutil
from pathlib import Path
import time

# ─── KIỂM TRA THƯ VIỆN ───────────────────────────────────────────────────────

try:
    from ultralytics import YOLO
except ImportError:
    print("❌ Chưa cài ultralytics. Chạy: pip3 install ultralytics")
    sys.exit(1)

# ─── CẤU HÌNH HUẤN LUYỆN ─────────────────────────────────────────────────────

# Mô hình gốc để fine-tune (nano = nhanh nhất, phù hợp CPU/GPU yếu)
# Các lựa chọn: yolov8n.pt | yolov8s.pt | yolov8m.pt | yolov8l.pt | yolov8x.pt
BASE_MODEL   = "yolov8n.pt"

# File cấu hình dataset
DATA_YAML    = "data.yaml"

# ──────────────────────────────────────────────────────────────────────────────
#  HYPERPARAMETER — Điều chỉnh theo phần cứng của bạn
# ──────────────────────────────────────────────────────────────────────────────
TRAIN_CONFIG = dict(
    # ── Cơ bản ──────────────────────────────────────────────
    epochs       = 100,          # Số vòng lặp train (tăng lên 150-200 để tốt hơn)
    imgsz        = 640,          # Kích thước ảnh đầu vào (640 = chuẩn)
    batch        = 8,            # Batch size (giảm xuống 4 nếu RAM/VRAM ít)
    patience     = 20,           # Dừng sớm nếu 20 epoch không cải thiện
    workers      = 4,            # Số CPU thread song song (giảm xuống 2 nếu lỗi)

    # ── Thiết bị ────────────────────────────────────────────
    device       = "mps",        # "mps"=Mac M1/M2 | "0"=NVIDIA GPU | "cpu"=CPU
    # device     = "cpu",        # ← Bỏ comment dòng này nếu không có GPU

    # ── Tối ưu hoá ──────────────────────────────────────────
    optimizer    = "AdamW",      # AdamW thường tốt cho bài text detection
    lr0          = 0.001,        # Learning rate ban đầu
    lrf          = 0.01,         # LR cuối = lr0 × lrf
    momentum     = 0.937,
    weight_decay = 0.0005,
    warmup_epochs= 3,            # Warm-up 3 epoch đầu

    # ── Loss weights ────────────────────────────────────────
    box          = 7.5,          # Trọng số bounding-box regression loss
    cls          = 0.5,          # Trọng số classification loss
    dfl          = 1.5,          # Trọng số DFL loss

    # ── Augmentation (tăng cường dữ liệu) ───────────────────
    hsv_h        = 0.015,        # Hue variation
    hsv_s        = 0.7,          # Saturation variation
    hsv_v        = 0.4,          # Value (brightness) variation
    degrees      = 5.0,          # Xoay ảnh ±5°
    translate    = 0.1,          # Dịch chuyển ảnh ±10%
    scale        = 0.5,          # Zoom in/out 50%
    shear        = 2.0,          # Biến dạng góc ±2°
    perspective  = 0.0001,       # Biến dạng phối cảnh
    flipud       = 0.0,          # Lật dọc (=0 vì chữ không lật ngược)
    fliplr       = 0.3,          # Lật ngang 30%
    mosaic       = 1.0,          # Mosaic augmentation (ghép 4 ảnh)
    mixup        = 0.1,          # Mixup augmentation

    # ── Lưu kết quả ─────────────────────────────────────────
    project      = "runs/train",
    name         = "chinese_text_detect",
    save_period  = 10,           # Lưu checkpoint mỗi 10 epoch
    plots        = True,         # Vẽ biểu đồ loss, mAP...
    verbose      = True,
)

# ─── HÀM KIỂM TRA DATASET ────────────────────────────────────────────────────

def check_dataset() -> bool:
    """Kiểm tra dataset đã được tạo chưa."""
    train_dir = Path("dataset/images/train")
    val_dir   = Path("dataset/images/val")

    if not train_dir.exists() or not any(train_dir.iterdir()):
        print("❌  Dataset chưa có!")
        print("    Hãy chạy trước: python3 prepare_dataset.py")
        return False

    n_train = len(list(train_dir.glob("*.jpg")))
    n_val   = len(list(val_dir.glob("*.jpg")))
    print(f"[✓] Dataset sẵn sàng: {n_train} ảnh train | {n_val} ảnh val")
    return True


def find_best_pt(run_dir: Path) -> Path | None:
    """Tìm file best.pt trong kết quả train."""
    candidates = list(run_dir.rglob("best.pt"))
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


# ─── HUẤN LUYỆN ──────────────────────────────────────────────────────────────

def train():
    print("\n" + "═" * 62)
    print("  🀄  HUẤN LUYỆN YOLO v8 — PHÁT HIỆN CHỮ TRUNG QUỐC")
    print("═" * 62)

    # Kiểm tra dataset
    if not check_dataset():
        return

    # Kiểm tra data.yaml
    if not Path(DATA_YAML).exists():
        print(f"❌  Không tìm thấy {DATA_YAML}")
        return

    print(f"\n[ℹ] Mô hình gốc : {BASE_MODEL}")
    print(f"[ℹ] Dataset     : {DATA_YAML}")
    print(f"[ℹ] Epochs      : {TRAIN_CONFIG['epochs']}")
    print(f"[ℹ] Batch size  : {TRAIN_CONFIG['batch']}")
    print(f"[ℹ] Image size  : {TRAIN_CONFIG['imgsz']}×{TRAIN_CONFIG['imgsz']}")
    print(f"[ℹ] Device      : {TRAIN_CONFIG['device']}")
    print()

    # Tải mô hình gốc
    print("[1/4] Tải mô hình YOLOv8...")
    model = YOLO(BASE_MODEL)

    # ── BẮT ĐẦU TRAIN ──
    print("[2/4] Bắt đầu huấn luyện...\n")
    t_start = time.time()

    results = model.train(
        data    = DATA_YAML,
        **TRAIN_CONFIG
    )

    elapsed = time.time() - t_start
    h, m = divmod(int(elapsed), 3600)
    m, s = divmod(m, 60)
    print(f"\n[✓] Huấn luyện xong sau {h:02d}h {m:02d}m {s:02d}s")

    # ── ĐÁNH GIÁ TRÊN TẬP VAL ──
    print("\n[3/4] Đánh giá mô hình trên tập val...")
    val_metrics = model.val()
    print(f"      mAP50   : {val_metrics.box.map50:.4f}")
    print(f"      mAP50-95: {val_metrics.box.map:.4f}")
    print(f"      Precision: {val_metrics.box.mp:.4f}")
    print(f"      Recall   : {val_metrics.box.mr:.4f}")

    # ── SAO CHÉP best.pt VỀ THƯ MỤC GỐC ──
    print("\n[4/4] Sao chép best.pt về thư mục dự án...")
    # Tìm best.pt đệ quy từ thư mục runs/ (tránh lệch path)
    run_dir = Path("runs")
    best_src = find_best_pt(run_dir)

    if best_src and best_src.exists():
        dest = Path("best.pt")
        shutil.copy2(best_src, dest)
        print(f"[✓] best.pt đã được lưu tại: {dest.resolve()}")
        print(f"    Kích thước file: {dest.stat().st_size / 1024:.1f} KB")
    else:
        print(f"[⚠] Không tìm thấy best.pt, hãy kiểm tra thư mục: runs/train/")

    print("\n" + "═" * 62)
    print("  ✅  HOÀN THÀNH!")
    print()
    print("  Để dùng model đã train, mở pipeline.py và đổi:")
    print('  self.model_yolo = YOLO("best.pt")')
    print()
    print("  Sau đó chạy lại: python3 main.py")
    print("═" * 62 + "\n")


# ─── THÊM: CHẠY INFERENCE THỬ SAU KHI TRAIN ─────────────────────────────────

def quick_test():
    """
    Chạy inference thử trên ảnh val để kiểm tra kết quả.
    Kết quả lưu trong: runs/detect/
    """
    if not Path("best.pt").exists():
        print("[⚠] Chưa có best.pt, hãy chạy train() trước.")
        return

    print("\n[🔍] Chạy inference thử trên ảnh val...")
    model = YOLO("best.pt")
    val_imgs = list(Path("dataset/images/val").glob("*.jpg"))[:5]

    if not val_imgs:
        print("[⚠] Không tìm thấy ảnh val.")
        return

    results = model(
        [str(p) for p in val_imgs],
        save=True,
        conf=0.4,
        project="runs/detect",
        name="quick_test"
    )

    print(f"[✓] Kết quả inference lưu tại: runs/detect/quick_test/")
    for i, r in enumerate(results):
        n_boxes = len(r.boxes)
        print(f"    Ảnh {i+1}: phát hiện {n_boxes} vùng chữ")


# ─── ĐIỂM KHỞI ĐẦU ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Huấn luyện YOLOv8 phát hiện chữ Trung Quốc"
    )
    parser.add_argument(
        "--test-only",
        action="store_true",
        help="Chỉ chạy inference thử (bỏ qua bước train)"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=None,
        help="Ghi đè số epochs (ví dụ: --epochs 50)"
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=None,
        help="Ghi đè batch size (ví dụ: --batch 4)"
    )
    parser.add_argument(
        "--device",
        type=str,
        default=None,
        help="Thiết bị: mps | cpu | 0 (GPU)"
    )
    args = parser.parse_args()

    # Ghi đè config từ command line
    if args.epochs is not None:
        TRAIN_CONFIG["epochs"] = args.epochs
    if args.batch is not None:
        TRAIN_CONFIG["batch"] = args.batch
    if args.device is not None:
        TRAIN_CONFIG["device"] = args.device

    if args.test_only:
        quick_test()
    else:
        train()
        quick_test()
