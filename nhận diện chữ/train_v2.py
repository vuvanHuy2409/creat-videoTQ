"""
train_v2.py
══════════════════════════════════════════════════════════════════
Script huấn luyện YOLOv8 v2 – Tối ưu chống False Positive

CẢI TIẾN SO VỚI train.py CŨ:
  ✅ YOLOv8s thay vì nano (~10% mAP tốt hơn, phù hợp Colab T4)
  ✅ Negative samples trong dataset (25%) → giảm false positive mạnh
  ✅ cls loss weight cao hơn (1.5 vs 0.5) → phân biệt class rõ hơn
  ✅ Conf threshold 0.45 (từ 0.3) → precision cao hơn
  ✅ fliplr = 0.0 (không lật ngang chữ Trung)
  ✅ copy_paste + erasing augmentation
  ✅ Cache dataset vào RAM để tăng tốc
  ✅ Tự động export ONNX sau khi train

CÁCH CHẠY:
  python3 train_v2.py                     # Train đầy đủ
  python3 train_v2.py --epochs 50         # Train nhanh
  python3 train_v2.py --device cpu        # Dùng CPU
  python3 train_v2.py --test-only         # Chỉ inference test
  python3 train_v2.py --data dataset_v2/data_v2.yaml  # Dùng dataset v2
══════════════════════════════════════════════════════════════════
"""

import os
import sys
import shutil
import time
from pathlib import Path

# ─── KIỂM TRA ULTRALYTICS ─────────────────────────────────────────────────────
try:
    from ultralytics import YOLO
except ImportError:
    print("❌ Chưa cài ultralytics. Chạy: pip install ultralytics>=8.0.0")
    sys.exit(1)

# ─── CẤU HÌNH ─────────────────────────────────────────────────────────────────

# Model gốc để fine-tune
# Các lựa chọn: yolov8n (nano) | yolov8s (small) | yolov8m (medium)
BASE_MODEL = "yolov8s.pt"   # Small tốt hơn nano ~10% mAP

# File data.yaml (sẽ được tạo bởi dataset_v2.py)
DATA_YAML = "dataset_v2/data_v2.yaml"

# ─── HYPERPARAMETER V2 ────────────────────────────────────────────────────────
TRAIN_CONFIG = dict(
    # ── Cơ bản ──────────────────────────────────────────────────────────────
    epochs        = 150,          # Nhiều hơn để học negative samples
    imgsz         = 640,
    batch         = 16,           # Colab T4: 16 OK; Mac M-series: 8; CPU: 4
    patience      = 30,           # Early stopping nếu không cải thiện
    workers       = 4,
    cache         = True,         # Cache dataset → tăng tốc đáng kể

    # ── Thiết bị ──────────────────────────────────────────────────────────
    # Tự động chọn bên dưới (mps / 0 / cpu)
    # device      = "mps",

    # ── Optimizer ─────────────────────────────────────────────────────────
    optimizer     = "AdamW",
    lr0           = 5e-4,         # LR thấp hơn → ổn định hơn
    lrf           = 0.01,
    weight_decay  = 0.001,
    warmup_epochs = 5,            # Warm-up dài hơn

    # ── Loss weights ──────────────────────────────────────────────────────
    box           = 7.5,
    cls           = 1.5,          # ⬆️ Tăng từ 0.5 → 1.5 (phân biệt class tốt hơn)
    dfl           = 1.5,

    # ── Augmentation – tinh chỉnh cho text detection ──────────────────────
    hsv_h         = 0.015,
    hsv_s         = 0.7,
    hsv_v         = 0.4,
    degrees       = 3.0,          # ⬇️ Nhẹ hơn (text ít xoay)
    translate     = 0.1,
    scale         = 0.4,
    shear         = 0.0,          # 🚫 Không shear (méo chữ)
    perspective   = 0.0,
    flipud        = 0.0,          # 🚫 Không lật dọc
    fliplr        = 0.0,          # 🚫 Không lật ngang (chữ Trung có hướng)
    mosaic        = 1.0,          # ✅ Mosaic rất hiệu quả
    mixup         = 0.05,         # ⬇️ Giảm (dễ confuse class)
    copy_paste    = 0.1,          # ✅ Mới: copy-paste augmentation
    erasing       = 0.3,          # ✅ Mới: random erasing

    # ── Inference threshold ────────────────────────────────────────────────
    conf          = 0.45,         # ⬆️ Tăng từ 0.3 → 0.45 (giảm false positive)
    iou           = 0.45,         # NMS IoU threshold

    # ── Output ────────────────────────────────────────────────────────────
    project       = "runs/train",
    name          = "chinese_v2",
    save_period   = 20,
    plots         = True,
    verbose       = True,
)


# ─── HÀM TIỆN ÍCH ─────────────────────────────────────────────────────────────

def detect_device() -> str:
    """Tự động chọn device tốt nhất."""
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram_gb  = torch.cuda.get_device_properties(0).total_memory / 1e9
            print(f"[✓] GPU: {gpu_name} ({vram_gb:.1f} GB VRAM)")
            return "0"
        elif torch.backends.mps.is_available():
            print("[✓] Device: Apple Silicon MPS")
            return "mps"
        else:
            print("[!] Không có GPU, dùng CPU (train sẽ chậm hơn ~10-20x)")
            return "cpu"
    except Exception:
        return "cpu"


def check_dataset(data_yaml: str) -> bool:
    """Kiểm tra dataset đã được tạo chưa."""
    if not Path(data_yaml).exists():
        print(f"❌ Không tìm thấy {data_yaml}")
        print(f"   Hãy chạy trước: python3 dataset_v2.py")
        return False

    import yaml
    with open(data_yaml, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    dataset_path = Path(cfg.get("path", "."))
    train_dir = dataset_path / "images" / "train"
    val_dir   = dataset_path / "images" / "val"

    if not train_dir.exists() or not any(train_dir.iterdir()):
        print(f"❌ Thư mục train trống: {train_dir}")
        return False

    n_train = len(list(train_dir.glob("*.jpg")))
    n_val   = len(list(val_dir.glob("*.jpg"))) if val_dir.exists() else 0

    print(f"[✓] Dataset OK: {n_train:,} ảnh train | {n_val:,} ảnh val")

    # Kiểm tra tỷ lệ negative samples
    lbl_train = dataset_path / "labels" / "train"
    if lbl_train.exists():
        all_lbls = list(lbl_train.glob("*.txt"))
        empty_lbls = sum(1 for f in all_lbls if f.stat().st_size == 0)
        neg_pct = empty_lbls / max(1, len(all_lbls)) * 100
        print(f"[✓] Negative samples: {empty_lbls:,} ảnh ({neg_pct:.0f}%)")
        if neg_pct < 20:
            print(f"[⚠] Tỷ lệ negative thấp ({neg_pct:.0f}%). "
                  f"Khuyến nghị >= 25% để giảm false positive.")

    return True


def find_best_pt(runs_dir: str = "runs") -> Path | None:
    """Tìm best.pt mới nhất trong thư mục runs."""
    candidates = list(Path(runs_dir).rglob("best.pt"))
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return None


# ─── TRAIN ────────────────────────────────────────────────────────────────────

def train(data_yaml: str = DATA_YAML):
    print("\n" + "═" * 65)
    print("  🀄  TRAIN YOLOv8s v2 – PHÁT HIỆN CHỮ TRUNG QUỐC")
    print("  📌  Cải tiến chống False Positive")
    print("═" * 65)

    # Kiểm tra dataset
    if not check_dataset(data_yaml):
        return False

    # Chọn device
    device = detect_device()
    TRAIN_CONFIG["device"] = device

    # Điều chỉnh batch theo device
    if device == "cpu":
        if TRAIN_CONFIG["batch"] > 4:
            TRAIN_CONFIG["batch"] = 4
            print(f"[!] CPU mode: giảm batch → 4")
        TRAIN_CONFIG["cache"] = False  # Không cache trên CPU (ít RAM hơn)

    # Tóm tắt config
    print(f"\n{'─'*65}")
    print(f"  Model    : {BASE_MODEL}")
    print(f"  Dataset  : {data_yaml}")
    print(f"  Epochs   : {TRAIN_CONFIG['epochs']}")
    print(f"  Batch    : {TRAIN_CONFIG['batch']}")
    print(f"  ImgSz    : {TRAIN_CONFIG['imgsz']}×{TRAIN_CONFIG['imgsz']}")
    print(f"  Device   : {device}")
    print(f"  Conf thr : {TRAIN_CONFIG['conf']} (tăng để giảm false positive)")
    print(f"  cls loss : {TRAIN_CONFIG['cls']} (tăng để phân biệt class rõ hơn)")
    print(f"  fliplr   : {TRAIN_CONFIG['fliplr']} (không lật ngang chữ Trung)")
    print(f"{'─'*65}\n")

    # Tải model
    print("[1/4] Tải model gốc YOLOv8s...")
    model = YOLO(BASE_MODEL)

    # Train
    print("[2/4] Bắt đầu huấn luyện...\n")
    t_start = time.time()

    results = model.train(
        data=data_yaml,
        **TRAIN_CONFIG,
    )

    elapsed = time.time() - t_start
    h, m = divmod(int(elapsed), 3600)
    m, s = divmod(m, 60)
    print(f"\n[✓] Huấn luyện xong sau {h:02d}h {m:02d}m {s:02d}s")

    # Đánh giá trên val
    print("\n[3/4] Đánh giá trên tập val...")
    val_metrics = model.val()
    prec = val_metrics.box.mp
    rec  = val_metrics.box.mr
    map50 = val_metrics.box.map50
    map95 = val_metrics.box.map
    f1 = 2 * prec * rec / (prec + rec + 1e-8)

    print(f"\n  {'─'*40}")
    print(f"  {'Metric':<20} {'Giá trị':>10}")
    print(f"  {'─'*40}")
    print(f"  {'mAP@50':<20} {map50:>10.4f}")
    print(f"  {'mAP@50-95':<20} {map95:>10.4f}")
    print(f"  {'Precision':<20} {prec:>10.4f}")
    print(f"  {'Recall':<20} {rec:>10.4f}")
    print(f"  {'F1 Score':<20} {f1:>10.4f}")
    print(f"  {'─'*40}")

    # Đánh giá chất lượng
    if map50 >= 0.80:
        grade = "🟢 Tốt"
    elif map50 >= 0.65:
        grade = "🟡 Trung bình (thêm data hoặc fine-tune)"
    else:
        grade = "🔴 Cần cải thiện (kiểm tra dataset)"
    print(f"\n  Đánh giá tổng thể: {grade}")

    if prec < 0.75:
        print(f"  [⚠] Precision thấp → Tăng conf threshold trong pipeline.py")
    if rec < 0.65:
        print(f"  [⚠] Recall thấp → Giảm conf threshold hoặc thêm positive data")

    # Sao chép best.pt
    print("\n[4/4] Sao chép best.pt về thư mục gốc...")
    best_src = find_best_pt("runs")
    if best_src and best_src.exists():
        dest = Path("best.pt")
        shutil.copy2(best_src, dest)
        print(f"[✓] best.pt → {dest.resolve()} ({dest.stat().st_size/1024:.0f} KB)")
    else:
        print(f"[⚠] Không tìm thấy best.pt trong runs/")

    # Export ONNX
    print("\n[+] Export sang ONNX (tùy chọn, có thể bỏ qua lỗi)...")
    try:
        onnx_path = model.export(
            format="onnx",
            imgsz=640,
            simplify=True,
            opset=17,
            dynamic=True,
        )
        if onnx_path:
            onnx_dest = Path("best.onnx")
            shutil.copy2(str(onnx_path), onnx_dest)
            print(f"[✓] best.onnx → {onnx_dest.resolve()} ({onnx_dest.stat().st_size/1e6:.1f} MB)")
    except Exception as e:
        print(f"[!] Export ONNX thất bại (không bắt buộc): {e}")

    # Hướng dẫn tiếp theo
    print("\n" + "═" * 65)
    print("  ✅  HOÀN THÀNH!")
    print()
    print("  📋 Bước tiếp theo:")
    print("  1. Mở pipeline.py, cập nhật hàm _detect_text() để dùng YOLO:")
    print()
    print("     def _detect_text(self, img):")
    print("         results = self.model_yolo(img, conf=0.45, iou=0.45,")
    print("                                   imgsz=640, verbose=False)")
    print("         boxes = []")
    print("         for r in results:")
    print("             if r.boxes is None: continue")
    print("             for box in r.boxes:")
    print("                 x1,y1,x2,y2 = box.xyxy[0].cpu().numpy().astype(int)")
    print("                 boxes.append((x1,y1,x2,y2))")
    print("         return boxes")
    print()
    print("  2. Nếu vẫn còn false positive: tăng conf → 0.55 hoặc 0.60")
    print("  3. Nếu bỏ sót chữ quá nhiều: giảm conf → 0.35")
    print("═" * 65 + "\n")

    return True


# ─── INFERENCE TEST ────────────────────────────────────────────────────────────

def quick_test(model_path: str = "best.pt", data_yaml: str = DATA_YAML):
    """Chạy inference thử trên ảnh val + hiển thị thống kê."""
    if not Path(model_path).exists():
        print(f"[⚠] Không tìm thấy {model_path}. Hãy chạy train() trước.")
        return

    import yaml
    try:
        with open(data_yaml, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        val_dir = Path(cfg["path"]) / "images" / "val"
    except Exception:
        val_dir = Path("dataset_v2/images/val")

    val_imgs = list(val_dir.glob("*.jpg"))
    if not val_imgs:
        print(f"[⚠] Không tìm thấy ảnh val trong {val_dir}")
        return

    print(f"\n[🔍] Inference test trên {min(10, len(val_imgs))} ảnh val...")
    model = YOLO(model_path)
    sample = val_imgs[:10]

    results = model(
        [str(p) for p in sample],
        conf=0.45,
        iou=0.45,
        save=True,
        project="runs/detect",
        name="quick_test_v2",
        verbose=False,
    )

    # Thống kê false positive trên negative samples
    tp_count  = 0  # True positive (positive image, có detect)
    fn_count  = 0  # False negative (positive image, không detect)
    tn_count  = 0  # True negative (negative image, không detect)
    fp_count  = 0  # False positive (negative image, có detect)

    for img_path, r in zip(sample, results):
        lbl_path = val_dir.parent.parent / "labels" / "val" / (img_path.stem + ".txt")
        lbl_content = lbl_path.read_text().strip() if lbl_path.exists() else ""
        has_gt = len([l for l in lbl_content.split("\n") if l.strip()]) > 0
        has_det = len(r.boxes) > 0 if r.boxes is not None else False

        n_boxes = len(r.boxes) if r.boxes is not None else 0
        label = "POS" if has_gt else "NEG"
        status = "✅" if (has_gt == has_det) else "⚠️ "
        print(f"  {status} {label:3s} | GT={'Y' if has_gt else 'N'} | "
              f"Det={n_boxes} boxes | {img_path.name}")

        if has_gt and has_det:     tp_count += 1
        elif has_gt and not has_det: fn_count += 1
        elif not has_gt and not has_det: tn_count += 1
        else:                      fp_count += 1

    print(f"\n  Thống kê trên {len(sample)} ảnh test:")
    print(f"    ✅ True Positive  : {tp_count}")
    print(f"    ✅ True Negative  : {tn_count}")
    print(f"    ⚠️  False Positive : {fp_count}  ← cần giảm!")
    print(f"    ⚠️  False Negative : {fn_count}")
    print(f"\n[✓] Ảnh inference lưu tại: runs/detect/quick_test_v2/")


# ─── ĐIỂM KHỞI ĐẦU ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Train YOLOv8s v2 – Nhận diện chữ Trung Quốc (chống false positive)"
    )
    parser.add_argument("--data",      type=str,  default=DATA_YAML,
                        help=f"Đường dẫn data.yaml (default: {DATA_YAML})")
    parser.add_argument("--model",     type=str,  default=BASE_MODEL,
                        help="Model gốc (default: yolov8s.pt)")
    parser.add_argument("--epochs",    type=int,  default=None,
                        help="Ghi đè số epochs")
    parser.add_argument("--batch",     type=int,  default=None,
                        help="Ghi đè batch size")
    parser.add_argument("--device",    type=str,  default=None,
                        help="Device: mps | cpu | 0 (GPU)")
    parser.add_argument("--conf",      type=float, default=None,
                        help="Confidence threshold (default: 0.45)")
    parser.add_argument("--test-only", action="store_true",
                        help="Chỉ chạy inference test (bỏ qua train)")
    args = parser.parse_args()

    # Ghi đè config từ CLI
    BASE_MODEL = args.model
    if args.epochs is not None:  TRAIN_CONFIG["epochs"]  = args.epochs
    if args.batch  is not None:  TRAIN_CONFIG["batch"]   = args.batch
    if args.device is not None:  TRAIN_CONFIG["device"]  = args.device
    if args.conf   is not None:  TRAIN_CONFIG["conf"]    = args.conf

    if args.test_only:
        quick_test(data_yaml=args.data)
    else:
        success = train(data_yaml=args.data)
        if success:
            quick_test(data_yaml=args.data)
