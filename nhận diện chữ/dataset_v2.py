"""
dataset_v2.py
══════════════════════════════════════════════════════════════════
Tạo dataset v2 CẢI TIẾN cho YOLOv8 – nhận diện chữ Trung Quốc

VẤN ĐỀ CỦA DATASET CŨ (prepare_dataset.py):
  ✗ Chỉ có positive samples → model không học cách TỪ CHỐI false positive
  ✗ Nền ảnh quá đơn giản (solid/gradient) → không reflect thực tế video
  ✗ Augmentation yếu, thiếu JPEG compression, motion blur
  ✗ Chữ xuất hiện ngẫu nhiên toàn ảnh → không học pattern subtitle

GIẢI PHÁP TRONG DATASET V2:
  ✅ Negative samples (25%): ảnh cảnh vật không có chữ → học từ chối FP
  ✅ Hard negative (15%): ảnh có text Latin/số → học phân biệt chữ Trung
  ✅ Positive có nền COCO thực tế (85%) hoặc synthetic (15%)
  ✅ Subtitle-style placement: 60% chữ ở vùng dưới ảnh (realistic)
  ✅ Video augmentation: JPEG compress, motion blur, noise, brightness
  ✅ 4 kiểu vẽ chữ: dark_bg, outline, colored_bg, transparent

CÁCH CHẠY:
  - Trên Colab: đặt COLAB_MODE = True, tải COCO tự động
  - Trên máy local: đặt COLAB_MODE = False, cung cấp BG_IMAGE_DIR
══════════════════════════════════════════════════════════════════
"""

import os
import io
import random
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

# ─── CHẾ ĐỘ CHẠY ──────────────────────────────────────────────────────────────
# True  = chạy trên Google Colab (tự tải COCO background)
# False = chạy local (tự cung cấp BG_IMAGE_DIR bên dưới)
COLAB_MODE = True

# Nếu COLAB_MODE = False, chỉ đường dẫn tới thư mục ảnh nền (JPEG/PNG)
BG_IMAGE_DIR = None   # ví dụ: "/path/to/your/background/images"

# ─── CẤU HÌNH DATASET ──────────────────────────────────────────────────────────
DATASET_CFG = dict(
    # Tổng số ảnh (Colab T4: ~15-20 phút cho 5000 ảnh)
    total_images         = 5000,

    # Kích thước ảnh output
    img_w                = 640,
    img_h                = 640,

    # Tỷ lệ 3 loại ảnh
    ratio_positive       = 0.60,   # chữ Trung trên nền thực
    ratio_negative       = 0.25,   # cảnh thuần không có chữ
    ratio_hard_negative  = 0.15,   # chữ Latin/số (không phải chữ Trung)

    # Split
    split_train          = 0.80,
    split_val            = 0.10,
    split_test           = 0.10,

    # Bounding boxes mỗi ảnh positive
    boxes_min            = 1,
    boxes_max            = 6,

    # Xác suất ảnh là "subtitle style" (chữ ở dưới ảnh)
    subtitle_probability = 0.60,

    # Cỡ chữ (pixel)
    font_size_min        = 16,
    font_size_max        = 60,

    # Output directory
    output_dir           = "./dataset_v2",
)

# ─── TẬP CHỮ TRUNG QUỐC ────────────────────────────────────────────────────────
_VOCAB_PARTS = [
    # Từ thông dụng
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对",
    "成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之",
    "进着等部度家电力里如水化高自二理起小物现实加量都两体制机当使点从业",
    "本去把性好应开它合还因由其些然前外天政四日那社义事平形相全表间样与",
    # Hội thoại phim/phụ đề
    "我不知道你在说什么我明白了请你等等谢谢再见对不起没关系你好吗我很好",
    "为什么你要这样做我不想他们不会相信的你说得对现在怎么办没有办法快走",
    "小心危险注意安全请勿吸烟禁止入内出口入口紧急通道安全出口消防通道",
    # Địa danh, thương hiệu
    "北京上海广州深圳天津重庆成都武汉西安杭州南京中国中华中央人民政府",
    "苹果华为小米三星联想戴尔惠普索尼佳能尼康松下海尔格力美的奥克斯",
    # Số, đơn vị
    "零一二三四五六七八九十百千万亿元角分公里米厘米千克克吨升摄氏度",
    # Câu dài (mô phỏng subtitle thực)
    "今天天气很好我们一起去公园吧好啊走吧你的朋友也来吗他今天有事来不了",
    "这个项目需要更多的时间和资源我们必须在截止日期前完成所有的工作任务",
    "根据最新研究显示人工智能技术已经在各个领域取得了突破性的进展和成就",
]
CHINESE_VOCAB = "".join(_VOCAB_PARTS)
CHINESE_CHARS = list(set(CHINESE_VOCAB))

# Câu mẫu gần giống subtitle phim thực
SAMPLE_SENTENCES = [
    "我不知道你在说什么", "请等一等", "谢谢你的帮助", "我们走吧",
    "这是什么地方", "你好吗", "对不起我来晚了", "没有关系",
    "这个怎么用", "我明白了", "你确定吗", "当然没问题",
    "注意安全", "小心点", "快一点", "不要走", "等等我",
    "这里是哪里", "我们在哪", "前面就是了", "已经到了",
    "现在几点了", "时间不多了", "我们必须走了", "快跑",
    "他们来了", "躲起来", "别说话", "听我说", "相信我",
    "中国北京上海", "深圳华为总部", "苹果发布会现场",
    "紧急通知全体员工", "禁止入内闲人免进", "安全第一",
    "今天是个好日子", "明天见", "我会记住的", "不用谢",
]

# Text Latin/số cho hard negative
HARD_NEG_TEXTS = [
    "HELLO WORLD", "EXIT 12", "STOP", "WARNING", "CAUTION",
    "1234567890", "ABC DEF GHI", "No.123", "Tel: 555-1234",
    "www.example.com", "HD 1080p", "FPS: 60", "©2024",
    "CCTV-1", "MTV", "00:12:34", "$99.99", "100%",
    "ERROR 404", "OK CANCEL", "ENTER", "BACK",
    "Season 1 Episode 3", "Chapter 12", "Part II",
    "Loading...", "Please wait", "Click here",
]


# ─── TÌM FONT CHỮ TRUNG ────────────────────────────────────────────────────────

def find_chinese_font():
    """Tìm font hỗ trợ chữ Trung Quốc trên hệ thống."""
    candidates = [
        # Colab / Linux
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        # macOS
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode MS.ttf",
        # Windows
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    raise RuntimeError(
        "Không tìm thấy font chữ Trung!\n"
        "  Trên Colab: !apt-get install -y fonts-noto-cjk fonts-wqy-zenhei\n"
        "  Trên macOS: font PingFang.ttc nên có sẵn"
    )


def load_fonts(font_path: str, size_min: int, size_max: int, step: int = 2) -> dict:
    """Load tất cả font sizes vào dict {size: font}."""
    fonts = {}
    for sz in range(size_min, size_max + 1, step):
        try:
            fonts[sz] = ImageFont.truetype(font_path, size=sz)
        except Exception:
            fonts[sz] = ImageFont.load_default()
    return fonts


# ─── QUẢN LÝ BACKGROUND ────────────────────────────────────────────────────────

def setup_coco_backgrounds(colab_mode: bool, bg_dir: str = None) -> list:
    """
    Chuẩn bị danh sách ảnh background:
    - Colab: tải COCO val2017 (~1GB, 5000 ảnh) tự động
    - Local: đọc từ BG_IMAGE_DIR
    """
    if colab_mode:
        import urllib.request
        import zipfile

        coco_extract = Path("/content/backgrounds/val2017")
        if not coco_extract.exists():
            print("📥 Tải COCO val2017 (~1GB) làm background...")
            coco_zip = Path("/content/val2017.zip")
            urllib.request.urlretrieve(
                "http://images.cocodataset.org/zips/val2017.zip",
                str(coco_zip),
                reporthook=lambda b, bs, t: print(
                    f"\r   {b*bs/1e6:.0f}/{t/1e6:.0f} MB", end="", flush=True
                ),
            )
            print("\n📦 Giải nén COCO...")
            with zipfile.ZipFile(coco_zip, "r") as zf:
                zf.extractall("/content/backgrounds")
            coco_zip.unlink()
            print("✅ COCO val2017 sẵn sàng!")
        else:
            print("✅ COCO val2017 đã có, bỏ qua tải lại")

        bg_list = sorted(coco_extract.glob("*.jpg"))
        print(f"   Background pool: {len(bg_list)} ảnh COCO")
        return bg_list

    elif bg_dir and os.path.isdir(bg_dir):
        bg_list = (
            list(Path(bg_dir).glob("*.jpg"))
            + list(Path(bg_dir).glob("*.jpeg"))
            + list(Path(bg_dir).glob("*.png"))
        )
        print(f"✅ Background pool local: {len(bg_list)} ảnh")
        return bg_list

    else:
        print("⚠️  Không có background pool → dùng nền synthetic (chất lượng thấp hơn)")
        return []


# ─── HÀM TẠO ẢNH ───────────────────────────────────────────────────────────────

def load_random_background(w: int, h: int, bg_images: list) -> Image.Image:
    """Load background ngẫu nhiên từ COCO hoặc tạo synthetic."""
    if bg_images and random.random() < 0.85:
        path = random.choice(bg_images)
        try:
            bg = Image.open(path).convert("RGB")
            bw, bh = bg.size
            scale = max(w / bw, h / bh) * random.uniform(1.0, 1.3)
            nw, nh = int(bw * scale), int(bh * scale)
            bg = bg.resize((nw, nh), Image.BICUBIC)
            x0 = random.randint(0, max(0, nw - w))
            y0 = random.randint(0, max(0, nh - h))
            bg = bg.crop((x0, y0, x0 + w, y0 + h))
            if bg.size != (w, h):
                bg = bg.resize((w, h), Image.BICUBIC)
            return bg
        except Exception:
            pass

    # Fallback: synthetic background
    mode = random.choice(["solid", "gradient", "noise"])
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    if mode == "solid":
        color = tuple(random.randint(30, 220) for _ in range(3))
        draw.rectangle([0, 0, w, h], fill=color)
    elif mode == "gradient":
        c1 = tuple(random.randint(0, 150) for _ in range(3))
        c2 = tuple(random.randint(100, 255) for _ in range(3))
        for y in range(h):
            t = y / h
            draw.line(
                [(0, y), (w, y)],
                fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)),
            )
    elif mode == "noise":
        arr = np.random.randint(50, 200, (h, w, 3), dtype=np.uint8)
        img = Image.fromarray(arr)
    return img


def xyxy_to_yolo(x1, y1, x2, y2, W, H) -> tuple:
    """Chuyển pixel bbox → YOLO format (cx cy w h, normalized)."""
    cx = (x1 + x2) / 2 / W
    cy = (y1 + y2) / 2 / H
    bw = (x2 - x1) / W
    bh = (y2 - y1) / H
    return cx, cy, bw, bh


def draw_text_on_image(
    img: Image.Image, text: str, font, x: int, y: int
) -> tuple[Image.Image, int, int, int, int]:
    """
    Vẽ chữ lên ảnh theo 4 style ngẫu nhiên.
    Trả về (img, x1, y1, x2, y2).
    """
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((x, y), text, font=font)
    pad = random.randint(2, 8)
    x1 = max(0, bbox[0] - pad)
    y1 = max(0, bbox[1] - pad)
    x2 = min(img.width, bbox[2] + pad)
    y2 = min(img.height, bbox[3] + pad)

    style = random.choice(["dark_bg", "transparent", "outline", "colored_bg"])

    if style == "dark_bg":
        # Nền đen mờ (phong cách subtitle phim)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        alpha = random.randint(140, 200)
        od.rectangle([x1, y1, x2, y2], fill=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        ImageDraw.Draw(img).text(
            (x, y), text, font=font,
            fill=tuple(random.randint(220, 255) for _ in range(3))
        )

    elif style == "outline":
        # Chữ trắng viền đen
        draw = ImageDraw.Draw(img)
        txt_color = tuple(random.randint(200, 255) for _ in range(3))
        for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1), (0, -2), (0, 2), (-2, 0), (2, 0)]:
            draw.text((x + dx, y + dy), text, font=font, fill=(10, 10, 10))
        draw.text((x, y), text, font=font, fill=txt_color)

    elif style == "colored_bg":
        # Nền màu (biển hiệu, banner)
        bright = random.random() < 0.5
        if bright:
            bg_col = tuple(random.randint(180, 255) for _ in range(3))
            txt_col = tuple(random.randint(0, 60) for _ in range(3))
        else:
            bg_col = tuple(random.randint(0, 80) for _ in range(3))
            txt_col = tuple(random.randint(200, 255) for _ in range(3))
        ImageDraw.Draw(img).rectangle([x1, y1, x2, y2], fill=bg_col)
        ImageDraw.Draw(img).text((x, y), text, font=font, fill=txt_col)

    else:  # transparent
        bright_bg = random.random() < 0.5
        txt_col = (
            (random.randint(200, 255),) * 3
            if bright_bg
            else (random.randint(0, 50),) * 3
        )
        ImageDraw.Draw(img).text((x, y), text, font=font, fill=txt_col)

    return img, x1, y1, x2, y2


def apply_video_augmentation(img: Image.Image) -> Image.Image:
    """
    Augmentation mô phỏng điều kiện video thực tế:
    - JPEG compression artifact (video codec)
    - Motion blur (chuyển động)
    - Brightness/contrast
    - Gaussian noise
    - Focus blur
    """
    # JPEG compression (quan trọng nhất!)
    if random.random() < 0.5:
        quality = random.randint(40, 85)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        buf.seek(0)
        img = Image.open(buf).copy()

    # Brightness
    if random.random() < 0.5:
        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.4, 1.6))

    # Contrast
    if random.random() < 0.4:
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.6, 1.5))

    # Motion blur
    if random.random() < 0.25:
        try:
            import cv2
            img_cv = np.array(img)
            size = random.choice([3, 5, 7])
            kernel = np.zeros((size, size))
            if random.random() < 0.5:
                kernel[size // 2, :] = 1.0 / size   # horizontal
            else:
                kernel[:, size // 2] = 1.0 / size   # vertical
            img_cv = cv2.filter2D(img_cv, -1, kernel)
            img = Image.fromarray(img_cv)
        except ImportError:
            pass

    # Gaussian noise
    if random.random() < 0.3:
        arr = np.array(img).astype(np.float32)
        noise = np.random.normal(0, random.uniform(3, 12), arr.shape)
        arr = np.clip(arr + noise, 0, 255).astype(np.uint8)
        img = Image.fromarray(arr)

    # Focus blur
    if random.random() < 0.2:
        img = img.filter(ImageFilter.GaussianBlur(radius=random.uniform(0.5, 1.5)))

    # Nhẹ xoay
    if random.random() < 0.15:
        angle = random.uniform(-3, 3)
        img = img.rotate(angle, resample=Image.BILINEAR, expand=False)

    return img


def random_chinese_text(use_sentence: bool = True) -> str:
    """Tạo text Trung: 60% câu mẫu (subtitle-like), 40% random."""
    if use_sentence and random.random() < 0.6:
        return random.choice(SAMPLE_SENTENCES)
    length = random.randint(2, random.randint(4, 14))
    return "".join(random.choices(CHINESE_CHARS, k=length))


# ─── GENERATOR FUNCTIONS ───────────────────────────────────────────────────────

def generate_positive_image(
    img_id: int, W: int, H: int, bg_images: list, fonts: dict, font_sizes: list
) -> tuple[Image.Image, list]:
    """
    POSITIVE: chữ Trung Quốc trên nền COCO thực tế.
    """
    img = load_random_background(W, H, bg_images)
    labels = []

    is_subtitle = random.random() < DATASET_CFG["subtitle_probability"]
    n_boxes = random.randint(DATASET_CFG["boxes_min"], DATASET_CFG["boxes_max"])

    for _ in range(n_boxes):
        font_size = random.choice(font_sizes)
        font = fonts[font_size]
        text = random_chinese_text(use_sentence=is_subtitle)

        # Vị trí chữ
        est_w = font_size * len(text)
        if is_subtitle:
            x_max = max(5, W - est_w - 10)
            x = random.randint(5, max(5, x_max))
            if random.random() < 0.6:  # căn giữa
                x = max(5, (W - est_w) // 2)
            y_min = int(H * 0.65)
            y_max = int(H * 0.92)
            y = random.randint(y_min, max(y_min, y_max - font_size * 2))
        else:
            x = random.randint(5, max(5, W - est_w - 10))
            y = random.randint(5, max(5, H - font_size * 2 - 10))

        img, x1, y1, x2, y2 = draw_text_on_image(img, text, font, x, y)

        if x2 <= x1 or y2 <= y1 or (x2 - x1) < 8 or (y2 - y1) < 6:
            continue

        cx, cy, bw, bh = xyxy_to_yolo(x1, y1, x2, y2, W, H)
        if 0 < bw < 1 and 0 < bh < 1:
            labels.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

    img = apply_video_augmentation(img)
    img = img.resize((W, H))
    return img, labels


def generate_negative_image(
    W: int, H: int, bg_images: list
) -> tuple[Image.Image, list]:
    """
    NEGATIVE: cảnh thực tế KHÔNG có chữ Trung.
    Label rỗng → dạy model từ chối false positive.
    """
    img = load_random_background(W, H, bg_images)
    img = apply_video_augmentation(img)
    img = img.resize((W, H))
    return img, []


def generate_hard_negative_image(
    W: int, H: int, bg_images: list
) -> tuple[Image.Image, list]:
    """
    HARD NEGATIVE: cảnh có text Latin/số nhưng KHÔNG có chữ Trung.
    Dạy model phân biệt chữ Trung với ký tự Latin.
    """
    img = load_random_background(W, H, bg_images)

    n_texts = random.randint(1, 4)
    try:
        lat_font = ImageFont.load_default()
        draw = ImageDraw.Draw(img)
        for _ in range(n_texts):
            txt = random.choice(HARD_NEG_TEXTS)
            x = random.randint(10, max(10, W - 150))
            y = random.randint(10, max(10, H - 40))
            color = tuple(random.randint(0, 255) for _ in range(3))
            draw.text((x, y), txt, font=lat_font, fill=color)
    except Exception:
        pass

    img = apply_video_augmentation(img)
    img = img.resize((W, H))
    return img, []


# ─── BUILD DATASET ─────────────────────────────────────────────────────────────

def build_dataset():
    """Main function: tạo toàn bộ dataset v2."""
    print("=" * 65)
    print("  📊 Tạo Dataset v2 – Anti False Positive")
    print("=" * 65)

    # Setup
    TOTAL = DATASET_CFG["total_images"]
    W, H  = DATASET_CFG["img_w"], DATASET_CFG["img_h"]
    OUT   = Path(DATASET_CFG["output_dir"])

    for split in ["train", "val", "test"]:
        (OUT / "images" / split).mkdir(parents=True, exist_ok=True)
        (OUT / "labels" / split).mkdir(parents=True, exist_ok=True)

    # Font
    print("\n[1/4] Chuẩn bị font chữ Trung...")
    font_path = find_chinese_font()
    print(f"      Font: {font_path}")
    font_sizes = list(range(
        DATASET_CFG["font_size_min"],
        DATASET_CFG["font_size_max"] + 1,
        2
    ))
    fonts = load_fonts(font_path, DATASET_CFG["font_size_min"], DATASET_CFG["font_size_max"])
    print(f"      Loaded {len(fonts)} font sizes")

    # Background images
    print("\n[2/4] Chuẩn bị ảnh background...")
    bg_images = setup_coco_backgrounds(COLAB_MODE, BG_IMAGE_DIR)

    # Tạo danh sách task
    N_POS  = int(TOTAL * DATASET_CFG["ratio_positive"])
    N_NEG  = int(TOTAL * DATASET_CFG["ratio_negative"])
    N_HARD = TOTAL - N_POS - N_NEG

    print(f"\n[3/4] Phân phối dataset:")
    print(f"      Positive  (chữ Trung)   : {N_POS:,} ảnh ({N_POS/TOTAL*100:.0f}%)")
    print(f"      Negative  (cảnh thuần)  : {N_NEG:,} ảnh ({N_NEG/TOTAL*100:.0f}%)")
    print(f"      Hard Neg  (text Latin)  : {N_HARD:,} ảnh ({N_HARD/TOTAL*100:.0f}%)")
    print(f"      Tổng cộng               : {TOTAL:,} ảnh")
    print()

    all_tasks = (["pos"] * N_POS + ["neg"] * N_NEG + ["hard"] * N_HARD)
    random.shuffle(all_tasks)

    split_train_end = int(TOTAL * DATASET_CFG["split_train"])
    split_val_end   = int(TOTAL * (DATASET_CFG["split_train"] + DATASET_CFG["split_val"]))

    def get_split(idx):
        if idx < split_train_end: return "train"
        elif idx < split_val_end: return "val"
        return "test"

    # Sinh ảnh
    print("[4/4] Sinh ảnh + nhãn YOLO...")
    counts = {"train": 0, "val": 0, "test": 0}
    type_counts = {"pos": 0, "neg": 0, "hard": 0}
    t0 = time.time()

    for idx, task_type in enumerate(all_tasks):
        split = get_split(idx)
        name = f"img_{idx:06d}"
        img_path = OUT / "images" / split / f"{name}.jpg"
        lbl_path = OUT / "labels" / split / f"{name}.txt"

        if task_type == "pos":
            img, labels = generate_positive_image(idx, W, H, bg_images, fonts, font_sizes)
        elif task_type == "neg":
            img, labels = generate_negative_image(W, H, bg_images)
        else:
            img, labels = generate_hard_negative_image(W, H, bg_images)

        img.save(str(img_path), "JPEG", quality=88)
        lbl_path.write_text("\n".join(labels), encoding="utf-8")

        counts[split] += 1
        type_counts[task_type] += 1

        # Progress
        if (idx + 1) % 50 == 0 or idx == TOTAL - 1:
            elapsed = time.time() - t0
            pct = (idx + 1) / TOTAL * 100
            eta = elapsed / (idx + 1) * (TOTAL - idx - 1)
            bar = "█" * int(pct // 2) + "░" * (50 - int(pct // 2))
            print(
                f"\r  [{bar}] {pct:5.1f}% | {idx+1}/{TOTAL} | ETA: {eta:.0f}s",
                end="",
                flush=True,
            )

    elapsed_total = time.time() - t0
    print(f"\n\n✅ Hoàn thành trong {elapsed_total:.0f}s!")
    print(f"\n  Phân phối:")
    print(f"    Train : {counts['train']:>5} ảnh")
    print(f"    Val   : {counts['val']:>5} ảnh")
    print(f"    Test  : {counts['test']:>5} ảnh")
    print(f"\n  Loại ảnh:")
    print(f"    Positive     : {type_counts['pos']:>5}")
    print(f"    Negative     : {type_counts['neg']:>5}")
    print(f"    Hard negative: {type_counts['hard']:>5}")
    print(f"\n  Dataset lưu tại: {OUT.resolve()}")
    print(f"  Tiếp theo: chạy train_v2.py với data_v2.yaml")

    # Tạo data.yaml
    yaml_path = OUT / "data_v2.yaml"
    yaml_content = f"""# Dataset v2 – Anti False Positive
nc: 1
names:
  - chinese_text

path: {OUT.resolve()}
train: images/train
val:   images/val
test:  images/test
"""
    yaml_path.write_text(yaml_content, encoding="utf-8")
    print(f"\n  data_v2.yaml đã tạo tại: {yaml_path}")

    return OUT, yaml_path


# ─── ĐIỂM KHỞI ĐẦU ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tạo dataset v2 chữ Trung Quốc")
    parser.add_argument("--total", type=int, default=None,
                        help="Tổng số ảnh (ghi đè DATASET_CFG)")
    parser.add_argument("--no-colab", action="store_true",
                        help="Chạy ở chế độ local (không tải COCO)")
    parser.add_argument("--bg-dir", type=str, default=None,
                        help="Thư mục ảnh nền (cho chế độ local)")
    args = parser.parse_args()

    if args.total:
        DATASET_CFG["total_images"] = args.total
    if args.no_colab:
        COLAB_MODE = False
    if args.bg_dir:
        BG_IMAGE_DIR = args.bg_dir

    build_dataset()
