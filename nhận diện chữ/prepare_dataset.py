"""
prepare_dataset.py
══════════════════════════════════════════════════════════════════
Tạo dataset chữ Trung Quốc tổng hợp (Synthetic Dataset) cho YOLOv8.

Chiến lược:
  • Sinh ảnh nền ngẫu nhiên (gradient / noise / màu đặc)
  • Vẽ cụm chữ Trung Quốc (giản thể) lên ảnh ở vị trí ngẫu nhiên
  • Xuất nhãn định dạng YOLO:  class cx cy w h  (chuẩn hoá 0-1)
  • Chia 80% train / 10% val / 10% test

Kết quả:
  dataset/
    images/train/   *.jpg
    images/val/     *.jpg
    images/test/    *.jpg
    labels/train/   *.txt
    labels/val/     *.txt
    labels/test/    *.txt
══════════════════════════════════════════════════════════════════
"""

import os
import random
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ─── CẤU HÌNH ────────────────────────────────────────────────────────────────

OUTPUT_DIR   = Path("dataset")
IMG_SIZE     = (640, 640)          # Kích thước ảnh xuất (W × H)
NUM_IMAGES   = 2000                # Tổng số ảnh cần tạo
SPLIT_RATIO  = (0.80, 0.10, 0.10) # Train / Val / Test
BOXES_PER_IMG = (1, 5)            # Số vùng chữ mỗi ảnh (min, max)

# Tập chữ Trung Quốc mô phỏng văn bản thực tế
_VOCAB_PARTS = [
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对",
    "成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之",
    "进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业",
    "本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与",
    "关各重新线内数正心力理见发进着很快来他走过说看好吗呢啊哦哈嗯嘿喂哎",
    "呀哇噢啦吧呗咯唉哟嘛哩呵咦哼唔北京上海广州深圳天津重庆成都武汉西安",
    "杭州南京苏州宁波郑州长沙沈阳青岛大连厦门福州哈尔滨济南昆明南宁合肥",
    "石家庄长春太原贵阳南昌兰州海口银川呼和浩特西宁拉萨乌鲁木齐零一二三",
    "四五六七八九十百千万亿元角分厘毫公里米厘米毫米平方千克克吨升毫升摄",
    "氏度中国人民共和国中华中央政府党委市区县街道路号楼室单元户餐厅酒店",
    "超市商场银行医院学校公司工厂仓库停车场入口出口紧急安全注意危险禁止",
    "请勿严禁苹果华为小米三星联想戴尔惠普索尼佳能尼康松下海尔格力美的奥",
]
CHINESE_VOCAB = "".join(_VOCAB_PARTS)
CHINESE_CHARS = list(set(CHINESE_VOCAB))

# Màu sắc chữ (BGR tối và sáng để contrast tốt)
TEXT_COLORS_DARK   = [(10,10,10),(20,20,80),(80,10,10),(10,80,10),(0,0,120)]
TEXT_COLORS_BRIGHT = [(240,240,240),(255,255,100),(100,255,255),(255,100,100),(200,255,200)]

# ─── HÀM TIỆN ÍCH ────────────────────────────────────────────────────────────

def random_chinese_text(min_len: int = 2, max_len: int = 12) -> str:
    """Tạo chuỗi chữ Trung Quốc ngẫu nhiên."""
    length = random.randint(min_len, max_len)
    return "".join(random.choices(CHINESE_CHARS, k=length))


def get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """
    Tìm font hỗ trợ chữ Trung trên hệ thống.
    Thứ tự ưu tiên: macOS → Linux → fallback PIL default.
    """
    candidate_paths = [
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode MS.ttf",
        # Linux
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        # Windows (nếu cần cross-platform)
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in candidate_paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size=size)
            except Exception:
                continue
    # Fallback: PIL default (không hiển thị chữ Trung đẹp nhưng vẫn chạy)
    return ImageFont.load_default()


def make_background(w: int, h: int) -> Image.Image:
    """Tạo ảnh nền đa dạng: gradient, noise, màu đặc, texture."""
    mode = random.choice(["solid", "gradient", "noise", "mixed"])
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)

    if mode == "solid":
        color = tuple(random.randint(50, 230) for _ in range(3))
        draw.rectangle([0, 0, w, h], fill=color)

    elif mode == "gradient":
        c1 = tuple(random.randint(0, 200) for _ in range(3))
        c2 = tuple(random.randint(50, 255) for _ in range(3))
        for y in range(h):
            t = y / h
            r = int(c1[0] * (1 - t) + c2[0] * t)
            g = int(c1[1] * (1 - t) + c2[1] * t)
            b = int(c1[2] * (1 - t) + c2[2] * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b))

    elif mode == "noise":
        arr = np.random.randint(60, 200, (h, w, 3), dtype=np.uint8)
        img = Image.fromarray(arr)

    elif mode == "mixed":
        # Nền màu + một vài hình chữ nhật trang trí
        base = tuple(random.randint(40, 180) for _ in range(3))
        draw.rectangle([0, 0, w, h], fill=base)
        for _ in range(random.randint(3, 8)):
            x0, y0 = random.randint(0, w), random.randint(0, h)
            x1, y1 = x0 + random.randint(30, 200), y0 + random.randint(20, 120)
            col = tuple(random.randint(0, 255) for _ in range(3))
            alpha = random.randint(30, 100)
            overlay = Image.new("RGB", (w, h), col)
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rectangle([x0, y0, x1, y1], fill=alpha)
            img = Image.composite(overlay, img, mask)

    # Thêm blur nhẹ ngẫu nhiên
    if random.random() < 0.3:
        img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.3, 1.2)))

    return img


def draw_text_box(
    img: Image.Image,
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int, y: int,
    text_color: tuple,
    bg_color: tuple | None = None,
    padding: int = 4
) -> tuple[int, int, int, int]:
    """
    Vẽ text lên ảnh, trả về bounding box (x1, y1, x2, y2).
    bg_color: màu nền label (None = trong suốt).
    """
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((x, y), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    x1 = max(0, bbox[0] - padding)
    y1 = max(0, bbox[1] - padding)
    x2 = min(img.width,  bbox[2] + padding)
    y2 = min(img.height, bbox[3] + padding)

    if bg_color is not None:
        draw.rectangle([x1, y1, x2, y2], fill=bg_color)

    draw.text((x, y), text, font=font, fill=text_color)
    return x1, y1, x2, y2


def xyxy_to_yolo(x1, y1, x2, y2, img_w, img_h) -> tuple:
    """Chuyển bounding box sang định dạng YOLO (cx cy w h chuẩn hoá 0-1)."""
    cx = ((x1 + x2) / 2) / img_w
    cy = ((y1 + y2) / 2) / img_h
    bw  = (x2 - x1) / img_w
    bh  = (y2 - y1) / img_h
    return cx, cy, bw, bh


def apply_augmentation(img: Image.Image) -> Image.Image:
    """Augmentation nhẹ: xoay, độ sáng, nhiễu."""
    # Xoay nhẹ
    if random.random() < 0.3:
        angle = random.uniform(-5, 5)
        img = img.rotate(angle, resample=Image.BILINEAR, expand=False)

    # Điều chỉnh độ sáng
    if random.random() < 0.4:
        from PIL import ImageEnhance
        factor = random.uniform(0.6, 1.4)
        img = ImageEnhance.Brightness(img).enhance(factor)

    # Contrast
    if random.random() < 0.3:
        from PIL import ImageEnhance
        factor = random.uniform(0.7, 1.3)
        img = ImageEnhance.Contrast(img).enhance(factor)

    return img


# ─── SINH ẢNH & NHÃN ─────────────────────────────────────────────────────────

def generate_one_image(img_id: int, w: int, h: int):
    """
    Sinh 1 ảnh + nhãn YOLO.
    Trả về: (PIL Image, list[str] nhãn YOLO).
    """
    img = make_background(w, h)
    labels = []

    n_boxes = random.randint(*BOXES_PER_IMG)
    placed_boxes = []   # Tránh overlap quá nhiều

    for _ in range(n_boxes):
        # Cỡ chữ ngẫu nhiên
        font_size = random.randint(18, 52)
        font = get_font(font_size)

        text = random_chinese_text(
            min_len=random.randint(1, 4),
            max_len=random.randint(5, 14)
        )

        # Màu chữ & nền
        bright_bg = random.random() < 0.5
        if bright_bg:
            bg_col  = tuple(random.randint(180, 255) for _ in range(3))
            txt_col = random.choice(TEXT_COLORS_DARK)
        else:
            bg_col  = tuple(random.randint(0, 80) for _ in range(3))
            txt_col = random.choice(TEXT_COLORS_BRIGHT)

        # Không dùng nền label 30% trường hợp (chữ trên nền ảnh)
        use_bg = random.random() < 0.7

        # Vị trí ngẫu nhiên
        x = random.randint(5, max(5, w - font_size * len(text) - 10))
        y = random.randint(5, max(5, h - font_size * 2))

        x1, y1, x2, y2 = draw_text_box(
            img, text, font, x, y,
            text_color=txt_col,
            bg_color=bg_col if use_bg else None
        )

        # Bỏ qua box quá nhỏ hoặc nằm ngoài ảnh
        if x2 <= x1 or y2 <= y1 or x2 > w or y2 > h:
            continue

        # Chuyển sang YOLO format
        cx, cy, bw, bh = xyxy_to_yolo(x1, y1, x2, y2, w, h)
        if bw > 0 and bh > 0:
            labels.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
            placed_boxes.append((x1, y1, x2, y2))

    # Augmentation
    img = apply_augmentation(img)

    return img, labels


# ─── TẠO CẤU TRÚC THƯ MỤC ───────────────────────────────────────────────────

def setup_dirs(base: Path):
    splits = ["train", "val", "test"]
    for split in splits:
        (base / "images" / split).mkdir(parents=True, exist_ok=True)
        (base / "labels" / split).mkdir(parents=True, exist_ok=True)
    print(f"[✓] Tạo cấu trúc thư mục tại: {base.resolve()}")


def get_split(idx: int, total: int, ratio: tuple) -> str:
    train_end = int(total * ratio[0])
    val_end   = int(total * (ratio[0] + ratio[1]))
    if idx < train_end:
        return "train"
    elif idx < val_end:
        return "val"
    else:
        return "test"


# ─── ĐIỂM KHỞI ĐẦU ───────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  📊 Tạo Dataset Chữ Trung Quốc (Synthetic)")
    print(f"  Tổng ảnh: {NUM_IMAGES}  |  Kích thước: {IMG_SIZE}")
    print("=" * 60)

    setup_dirs(OUTPUT_DIR)
    W, H = IMG_SIZE

    counts = {"train": 0, "val": 0, "test": 0}

    for i in range(NUM_IMAGES):
        split = get_split(i, NUM_IMAGES, SPLIT_RATIO)
        img_name = f"img_{i:05d}.jpg"
        lbl_name = f"img_{i:05d}.txt"

        img, labels = generate_one_image(i, W, H)

        img_path = OUTPUT_DIR / "images" / split / img_name
        lbl_path = OUTPUT_DIR / "labels" / split / lbl_name

        img.save(str(img_path), "JPEG", quality=92)
        lbl_path.write_text("\n".join(labels), encoding="utf-8")

        counts[split] += 1

        if (i + 1) % 100 == 0 or i == NUM_IMAGES - 1:
            pct = (i + 1) / NUM_IMAGES * 100
            bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
            print(f"\r  [{bar}] {pct:5.1f}%  ({i+1}/{NUM_IMAGES})", end="", flush=True)

    print(f"\n\n[✓] Hoàn thành! Phân phối ảnh:")
    print(f"    Train : {counts['train']:>5} ảnh")
    print(f"    Val   : {counts['val']:>5} ảnh")
    print(f"    Test  : {counts['test']:>5} ảnh")
    print(f"\n  Dataset lưu tại: {OUTPUT_DIR.resolve()}")
    print(f"  Tiếp theo: python3 train.py")


if __name__ == "__main__":
    main()
