"""
train_colab.py
══════════════════════════════════════════════════════════════════
File train ALL-IN-ONE cho Google Colab.

CÁCH SỬ DỤNG:
  1. Upload file này lên Google Colab
  2. Chọn Runtime → Change runtime type → T4 GPU
  3. Chạy từng cell bằng cách copy từng section vào Colab
     HOẶC chạy toàn bộ: !python train_colab.py

HOẶC dùng Colab notebook (khuyến nghị):
  - Mở Google Colab (colab.research.google.com)
  - File → New notebook
  - Copy từng SECTION vào cell riêng biệt
══════════════════════════════════════════════════════════════════

PHÂN TÍCH VẤN ĐỀ MODEL CŨ:
  ❌ Dataset synthetic đơn giản (nền gradient/màu đặc)
  ❌ Không có negative samples → model bắt mọi pattern
  ❌ Pipeline dùng easyocr.detect() → quá nhạy, bắt cả chữ ngoại
  ❌ Conf threshold 0.3 quá thấp → nhiều false positive
  ❌ Model yolov8n (nano) quá nhỏ cho text detection

GIẢI PHÁP V2:
  ✅ Dataset 3 loại (60% positive, 25% negative, 15% hard negative)
  ✅ Nền COCO thực tế (5000 ảnh cảnh vật) thay vì nền đơn giản
  ✅ Augmentation video thực: JPEG compress, motion blur, noise
  ✅ YOLOv8s (small) thay vì nano → tốt hơn ~10% mAP
  ✅ cls loss = 1.5 (x3 so với cũ) → phân biệt class rõ hơn
  ✅ Conf threshold = 0.45 → ít false positive hơn
  ✅ Pipeline cập nhật: dùng YOLO detect thay vì easyocr.detect
══════════════════════════════════════════════════════════════════
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 0 – Cài đặt thư viện (chạy 1 lần đầu tiên)
# ══════════════════════════════════════════════════════════════════
SECTION_0 = """
!pip install -q ultralytics==8.2.87
!pip install -q albumentations Pillow numpy tqdm

import torch
print(f"CUDA: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU: {torch.cuda.get_device_name(0)}")
    print(f"VRAM: {torch.cuda.get_device_properties(0).total_memory/1e9:.1f} GB")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 1 – Tải font chữ Trung + cài apt packages
# ══════════════════════════════════════════════════════════════════
SECTION_1 = """
import os, sys
from pathlib import Path

# Cài font chữ Trung Quốc
!apt-get install -y -qq fonts-noto-cjk fonts-wqy-zenhei

# Kiểm tra font
SYSTEM_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
AVAILABLE_FONTS = [f for f in SYSTEM_FONTS if os.path.exists(f)]
print(f"Tìm thấy {len(AVAILABLE_FONTS)} font:")
for f in AVAILABLE_FONTS:
    print(f"  {f}")

if not AVAILABLE_FONTS:
    raise RuntimeError("Không có font! Chạy lại apt-get")

FONT_PATH = AVAILABLE_FONTS[0]
print(f"\\nDùng font: {FONT_PATH}")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 2 – Tải COCO val2017 làm background
# ══════════════════════════════════════════════════════════════════
SECTION_2 = """
import urllib.request, zipfile
from pathlib import Path

BG_DIR = Path("/content/backgrounds")
BG_DIR.mkdir(exist_ok=True)

COCO_EXTRACT = BG_DIR / "val2017"
if not COCO_EXTRACT.exists():
    print("📥 Tải COCO val2017 (~1GB)...")
    urllib.request.urlretrieve(
        "http://images.cocodataset.org/zips/val2017.zip",
        "/content/val2017.zip",
        reporthook=lambda b,bs,t: print(f"\\r  {b*bs/1e6:.0f}/{t/1e6:.0f} MB", end="", flush=True)
    )
    print("\\n📦 Giải nén...")
    with zipfile.ZipFile("/content/val2017.zip", 'r') as zf:
        zf.extractall(str(BG_DIR))
    Path("/content/val2017.zip").unlink()
    print("✅ COCO val2017 sẵn sàng!")
else:
    print("✅ COCO val2017 đã có, bỏ qua tải")

BG_IMAGES = sorted(COCO_EXTRACT.glob("*.jpg"))
print(f"Background pool: {len(BG_IMAGES)} ảnh")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 3 – Định nghĩa hàm tạo dataset
# ══════════════════════════════════════════════════════════════════
SECTION_3 = """
import random, io, time
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

# ── Cấu hình ──────────────────────────────────────────────────────
TOTAL_IMAGES    = 5000    # Tổng số ảnh (T4 GPU: ~15-20 phút)
IMG_W, IMG_H    = 640, 640
RATIO_POSITIVE  = 0.60   # Ảnh có chữ Trung
RATIO_NEGATIVE  = 0.25   # Ảnh cảnh thuần (không chữ)
RATIO_HARD_NEG  = 0.15   # Ảnh chữ Latin/số (không phải chữ Trung)

BASE_DIR = Path("/content/chinese_dataset")
for split in ["train", "val", "test"]:
    (BASE_DIR / "images" / split).mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "labels" / split).mkdir(parents=True, exist_ok=True)

# ── Vocab chữ Trung ────────────────────────────────────────────────
CHINESE_CHARS = list(set(
    "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对"
    "成会可主发年动同工也能下过子说产种面而方后多定行学法所民得经十三之"
    "我不知道你在说什么我明白了请你等等谢谢再见对不起没关系你好吗我很好"
    "为什么你要这样做我不想他们不会相信的你说得对现在怎么办没有办法快走"
    "北京上海广州深圳天津重庆成都武汉西安杭州南京中国中华中央人民政府"
    "苹果华为小米三星联想戴尔惠普索尼佳能尼康松下海尔格力美的奥克斯"
    "零一二三四五六七八九十百千万亿元角分公里米厘米千克克吨升摄氏度"
    "注意安全请勿吸烟禁止入内出口入口紧急通道安全出口消防通道小心危险"
))

SAMPLE_SENTENCES = [
    "我不知道你在说什么", "请等一等", "谢谢你的帮助", "我们走吧",
    "你好吗", "对不起我来晚了", "没有关系", "我明白了", "你确定吗",
    "注意安全", "小心点", "不要走", "等等我", "快跑", "相信我",
    "深圳华为总部", "苹果发布会现场", "禁止入内闲人免进", "安全第一",
    "今天天气很好", "时间不多了", "我们必须走了", "他们来了",
]

HARD_NEG_TEXTS = [
    "HELLO", "EXIT", "STOP", "WARNING", "1234567890",
    "No.123", "HD 1080p", "FPS 60", "CCTV-1", "©2024",
    "Season 1", "Chapter 12", "ERROR", "OK", "CANCEL",
]

# ── Load fonts ─────────────────────────────────────────────────────
def load_fonts(font_path, size_min=16, size_max=60, step=2):
    fonts = {}
    for sz in range(size_min, size_max+1, step):
        try: fonts[sz] = ImageFont.truetype(font_path, size=sz)
        except: fonts[sz] = ImageFont.load_default()
    return fonts

FONT_SIZES = list(range(16, 61, 2))
FONTS = load_fonts(FONT_PATH)
print(f"✅ Loaded {len(FONTS)} font sizes")

# ── Background ─────────────────────────────────────────────────────
def get_bg(w, h):
    if BG_IMAGES and random.random() < 0.85:
        try:
            bg = Image.open(random.choice(BG_IMAGES)).convert("RGB")
            bw, bh = bg.size
            sc = max(w/bw, h/bh) * random.uniform(1.0, 1.3)
            bg = bg.resize((int(bw*sc), int(bh*sc)), Image.BICUBIC)
            x0 = random.randint(0, max(0, bg.width-w))
            y0 = random.randint(0, max(0, bg.height-h))
            bg = bg.crop((x0, y0, x0+w, y0+h))
            if bg.size != (w, h): bg = bg.resize((w, h), Image.BICUBIC)
            return bg
        except: pass
    # Synthetic fallback
    arr = np.random.randint(40, 200, (h, w, 3), dtype=np.uint8)
    return Image.fromarray(arr)

# ── Draw text ──────────────────────────────────────────────────────
def draw_text(img, text, font, x, y):
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((x, y), text, font=font)
    pad = random.randint(2, 8)
    x1, y1 = max(0, bbox[0]-pad), max(0, bbox[1]-pad)
    x2, y2 = min(img.width, bbox[2]+pad), min(img.height, bbox[3]+pad)

    style = random.choice(["dark_bg", "transparent", "outline", "colored_bg"])

    if style == "dark_bg":
        ov = Image.new("RGBA", img.size, (0,0,0,0))
        ImageDraw.Draw(ov).rectangle([x1,y1,x2,y2], fill=(0,0,0,random.randint(140,200)))
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        ImageDraw.Draw(img).text((x,y), text, font=font, fill=tuple(random.randint(220,255) for _ in range(3)))

    elif style == "outline":
        draw = ImageDraw.Draw(img)
        tc = tuple(random.randint(200,255) for _ in range(3))
        for dx,dy in [(-1,-1),(1,-1),(-1,1),(1,1),(0,-2),(0,2),(-2,0),(2,0)]:
            draw.text((x+dx,y+dy), text, font=font, fill=(10,10,10))
        draw.text((x,y), text, font=font, fill=tc)

    elif style == "colored_bg":
        bright = random.random() < 0.5
        bc = tuple(random.randint(180,255) for _ in range(3)) if bright else tuple(random.randint(0,80) for _ in range(3))
        tc = tuple(random.randint(0,60) for _ in range(3)) if bright else tuple(random.randint(200,255) for _ in range(3))
        ImageDraw.Draw(img).rectangle([x1,y1,x2,y2], fill=bc)
        ImageDraw.Draw(img).text((x,y), text, font=font, fill=tc)

    else:
        tc = tuple(random.randint(200,255) if random.random()<0.5 else random.randint(0,50) for _ in range(3))
        ImageDraw.Draw(img).text((x,y), text, font=font, fill=tc)

    return img, x1, y1, x2, y2

# ── Augmentation ───────────────────────────────────────────────────
def augment(img):
    # JPEG compression (most important for video simulation!)
    if random.random() < 0.5:
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=random.randint(40,85))
        buf.seek(0); img = Image.open(buf).copy()
    if random.random() < 0.5:
        img = ImageEnhance.Brightness(img).enhance(random.uniform(0.4,1.6))
    if random.random() < 0.4:
        img = ImageEnhance.Contrast(img).enhance(random.uniform(0.6,1.5))
    if random.random() < 0.25:  # Motion blur
        import cv2
        arr = np.array(img)
        sz = random.choice([3,5,7])
        k = np.zeros((sz,sz))
        if random.random()<0.5: k[sz//2,:] = 1/sz
        else: k[:,sz//2] = 1/sz
        img = Image.fromarray(cv2.filter2D(arr,-1,k))
    if random.random() < 0.3:  # Noise
        arr = np.array(img).astype(np.float32)
        img = Image.fromarray(np.clip(arr+np.random.normal(0,random.uniform(3,12),arr.shape),0,255).astype(np.uint8))
    return img

# ── Generate functions ─────────────────────────────────────────────
def yolo_fmt(x1,y1,x2,y2,W,H):
    return f"0 {(x1+x2)/2/W:.6f} {(y1+y2)/2/H:.6f} {(x2-x1)/W:.6f} {(y2-y1)/H:.6f}"

def gen_positive(W, H):
    img = get_bg(W, H)
    labels = []
    is_sub = random.random() < 0.60
    n = random.randint(1, 6)
    for _ in range(n):
        fsz = random.choice(FONT_SIZES)
        font = FONTS[fsz]
        text = random.choice(SAMPLE_SENTENCES) if (is_sub and random.random()<0.6) else "".join(random.choices(CHINESE_CHARS,k=random.randint(2,12)))
        est_w = fsz * len(text)
        if is_sub:
            x = max(5, (W-est_w)//2) if random.random()<0.6 else random.randint(5, max(5,W-est_w-10))
            y = random.randint(int(H*0.65), max(int(H*0.65), int(H*0.90)-fsz*2))
        else:
            x = random.randint(5, max(5,W-est_w-10))
            y = random.randint(5, max(5,H-fsz*2-10))
        img, x1,y1,x2,y2 = draw_text(img, text, font, x, y)
        if x2>x1 and y2>y1 and (x2-x1)>=8 and (y2-y1)>=6:
            labels.append(yolo_fmt(x1,y1,x2,y2,W,H))
    img = augment(img)
    return img.resize((W,H)), labels

def gen_negative(W, H):
    img = get_bg(W, H)
    return augment(img).resize((W,H)), []

def gen_hard_negative(W, H):
    img = get_bg(W, H)
    try:
        f = ImageFont.load_default()
        d = ImageDraw.Draw(img)
        for _ in range(random.randint(1,4)):
            d.text((random.randint(10,W-150), random.randint(10,H-40)),
                   random.choice(HARD_NEG_TEXTS), font=f,
                   fill=tuple(random.randint(0,255) for _ in range(3)))
    except: pass
    return augment(img).resize((W,H)), []

print("✅ Các hàm tạo dataset đã sẵn sàng")

# Test nhanh
test_img, test_lbl = gen_positive(640, 640)
print(f"Sample positive: {test_img.size}, {len(test_lbl)} boxes")
test_img.save("/content/sample_positive.jpg")
print("Lưu sample tại /content/sample_positive.jpg")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 4 – Sinh toàn bộ dataset
# ══════════════════════════════════════════════════════════════════
SECTION_4 = """
N_POS  = int(TOTAL_IMAGES * RATIO_POSITIVE)
N_NEG  = int(TOTAL_IMAGES * RATIO_NEGATIVE)
N_HARD = TOTAL_IMAGES - N_POS - N_NEG

print(f"🏗️ Tạo {TOTAL_IMAGES:,} ảnh:")
print(f"   Positive  : {N_POS:,} ({RATIO_POSITIVE*100:.0f}%)")
print(f"   Negative  : {N_NEG:,} ({RATIO_NEGATIVE*100:.0f}%)")
print(f"   Hard Neg  : {N_HARD:,} ({RATIO_HARD_NEG*100:.0f}%)")

all_tasks = ["pos"]*N_POS + ["neg"]*N_NEG + ["hard"]*N_HARD
random.shuffle(all_tasks)

TRAIN_END = int(TOTAL_IMAGES * 0.80)
VAL_END   = int(TOTAL_IMAGES * 0.90)

def get_split(i):
    if i < TRAIN_END: return "train"
    if i < VAL_END:   return "val"
    return "test"

t0 = time.time()
counts = {"train":0,"val":0,"test":0}
type_counts = {"pos":0,"neg":0,"hard":0}

for i, task in enumerate(all_tasks):
    split = get_split(i)
    name = f"img_{i:06d}"
    img_path = BASE_DIR / "images" / split / f"{name}.jpg"
    lbl_path = BASE_DIR / "labels" / split / f"{name}.txt"

    if task == "pos":
        img, labels = gen_positive(IMG_W, IMG_H)
    elif task == "neg":
        img, labels = gen_negative(IMG_W, IMG_H)
    else:
        img, labels = gen_hard_negative(IMG_W, IMG_H)

    img.save(str(img_path), "JPEG", quality=88)
    lbl_path.write_text("\\n".join(labels), encoding="utf-8")
    counts[split] += 1
    type_counts[task] += 1

    if (i+1)%50==0 or i==TOTAL_IMAGES-1:
        pct = (i+1)/TOTAL_IMAGES*100
        eta = (time.time()-t0)/(i+1)*(TOTAL_IMAGES-i-1)
        bar = "█"*int(pct//2) + "░"*(50-int(pct//2))
        print(f"\\r[{bar}] {pct:5.1f}% | {i+1}/{TOTAL_IMAGES} | ETA:{eta:.0f}s", end="", flush=True)

print(f"\\n\\n✅ Xong! {time.time()-t0:.0f}s")
for k,v in counts.items(): print(f"   {k}: {v}")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 5 – Tạo data.yaml
# ══════════════════════════════════════════════════════════════════
SECTION_5 = """
DATA_YAML = "/content/data_v2.yaml"

yaml_txt = f'''nc: 1
names:
  - chinese_text

path: {str(BASE_DIR)}
train: images/train
val:   images/val
test:  images/test
'''
with open(DATA_YAML, "w") as f:
    f.write(yaml_txt)

print("✅ data_v2.yaml đã tạo:")
print(yaml_txt)

for split in ["train","val","test"]:
    n = len(list((BASE_DIR/"images"/split).glob("*.jpg")))
    neg = sum(1 for f in (BASE_DIR/"labels"/split).glob("*.txt") if f.stat().st_size==0)
    print(f"   {split:6s}: {n} ảnh, {neg} negative ({neg/max(1,n)*100:.0f}%)")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 6 – Huấn luyện YOLOv8s
# ══════════════════════════════════════════════════════════════════
SECTION_6 = """
from ultralytics import YOLO
import torch, time

DEVICE = "0" if torch.cuda.is_available() else ("mps" if torch.backends.mps.is_available() else "cpu")
print(f"Device: {DEVICE}")

TRAIN_CFG = dict(
    data         = DATA_YAML,
    epochs       = 150,      # Giảm xuống 50 để test nhanh
    imgsz        = 640,
    batch        = 16,       # Giảm xuống 8 nếu OOM
    patience     = 30,
    workers      = 4,
    device       = DEVICE,
    cache        = True,

    optimizer    = "AdamW",
    lr0          = 5e-4,
    lrf          = 0.01,
    weight_decay = 0.001,
    warmup_epochs= 5,

    box          = 7.5,
    cls          = 1.5,      # ← KEY: Tăng 3x để phân biệt class rõ hơn
    dfl          = 1.5,

    degrees      = 3.0,
    translate    = 0.1,
    scale        = 0.4,
    shear        = 0.0,
    perspective  = 0.0,
    flipud       = 0.0,
    fliplr       = 0.0,      # ← KEY: Không lật chữ Trung
    mosaic       = 1.0,
    mixup        = 0.05,
    copy_paste   = 0.1,
    erasing      = 0.3,

    conf         = 0.45,     # ← KEY: Ngưỡng cao hơn giảm false positive
    iou          = 0.45,

    project      = "/content/runs/train",
    name         = "chinese_v2",
    save_period  = 25,
    plots        = True,
    verbose      = True,
)

print("\\n🚀 Bắt đầu train YOLOv8s...")
model = YOLO("yolov8s.pt")
t0 = time.time()
results = model.train(**TRAIN_CFG)
elapsed = time.time()-t0
h,m = divmod(int(elapsed),3600); m,s = divmod(m,60)
print(f"\\n✅ Xong sau {h:02d}h{m:02d}m{s:02d}s")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 7 – Đánh giá model
# ══════════════════════════════════════════════════════════════════
SECTION_7 = """
from pathlib import Path

best_pts = list(Path("/content/runs/train").rglob("best.pt"))
best_pt  = max(best_pts, key=lambda p: p.stat().st_mtime)
print(f"best.pt: {best_pt}")

eval_model = YOLO(str(best_pt))
val = eval_model.val(data=DATA_YAML, imgsz=640, conf=0.45, iou=0.45, device=DEVICE)

prec  = val.box.mp
rec   = val.box.mr
map50 = val.box.map50
map95 = val.box.map
f1    = 2*prec*rec/(prec+rec+1e-8)

print(f"\\n{'='*45}")
print(f"  mAP@50    : {map50:.4f}  (mục tiêu > 0.75)")
print(f"  mAP@50-95 : {map95:.4f}")
print(f"  Precision : {prec:.4f}  (mục tiêu > 0.80)")
print(f"  Recall    : {rec:.4f}  (mục tiêu > 0.70)")
print(f"  F1        : {f1:.4f}")
print(f"{'='*45}")

if prec < 0.75:
    print("  ⚠️  Precision thấp → Tăng conf lên 0.55 trong pipeline.py")
if rec < 0.65:
    print("  ⚠️  Recall thấp → Giảm conf hoặc thêm positive data")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 8 – Visualize inference
# ══════════════════════════════════════════════════════════════════
SECTION_8 = """
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np, random
from PIL import Image
from pathlib import Path

inf_model = YOLO(str(best_pt))
val_imgs  = list((BASE_DIR/"images"/"val").glob("*.jpg"))
samples   = random.sample(val_imgs, min(6, len(val_imgs)))

fig, axes = plt.subplots(2, 3, figsize=(18, 12))
fig.suptitle("YOLOv8s Chinese Text Detection v2 – Inference Test", fontsize=15, fontweight="bold")

for ax, img_path in zip(axes.flat, samples):
    r   = inf_model(str(img_path), conf=0.45, iou=0.45, verbose=False)[0]
    arr = np.array(Image.open(img_path))
    ax.imshow(arr)

    n_det = len(r.boxes) if r.boxes is not None else 0
    if n_det > 0:
        for box in r.boxes:
            x1,y1,x2,y2 = box.xyxy[0].cpu().numpy()
            c = float(box.conf[0])
            rect = mpatches.Rectangle((x1,y1),x2-x1,y2-y1,lw=2,ec="lime",fc="none")
            ax.add_patch(rect)
            ax.text(x1,y1-4,f"{c:.2f}",color="lime",fontsize=8,fontweight="bold")

    # Check label
    lbl = (BASE_DIR/"labels"/"val"/(img_path.stem+".txt"))
    gt  = len([l for l in lbl.read_text().strip().split("\\n") if l.strip()]) if lbl.exists() else 0
    tp  = gt>0 and n_det>0
    tn  = gt==0 and n_det==0
    fp  = gt==0 and n_det>0
    fn  = gt>0 and n_det==0
    status = "✅" if (tp or tn) else "❌"
    kind   = "NEG" if gt==0 else f"POS({gt}GT)"
    ax.set_title(f"{status} {kind} → {n_det} det", fontsize=10)
    ax.axis("off")

plt.tight_layout()
plt.savefig("/content/inference_v2.png", dpi=90)
plt.show()
print("✅ Ảnh lưu tại /content/inference_v2.png")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 9 – Export ONNX + Download
# ══════════════════════════════════════════════════════════════════
SECTION_9 = """
import zipfile
from pathlib import Path

# Export ONNX
print("🔄 Export ONNX...")
exp = YOLO(str(best_pt))
onnx_path = exp.export(format="onnx", imgsz=640, simplify=True, opset=17, dynamic=True)
print(f"✅ ONNX: {onnx_path}")

# Đóng gói
ZIP_PATH = "/content/chinese_model_v2.zip"
with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
    for name, path in [("best.pt", str(best_pt)), ("best.onnx", str(onnx_path))]:
        if path and Path(path).exists():
            zf.write(path, name)
            print(f"  ✅ {name} ({Path(path).stat().st_size/1e6:.1f} MB)")

print(f"\\n📦 ZIP: {ZIP_PATH} ({Path(ZIP_PATH).stat().st_size/1e6:.1f} MB)")

# Download về máy
try:
    from google.colab import files
    files.download(ZIP_PATH)
    print("⬇️ Đang tải về máy...")
except ImportError:
    print(f"ℹ️ File tại: {ZIP_PATH}")
"""

# ══════════════════════════════════════════════════════════════════
# ██  SECTION 10 – Hướng dẫn cập nhật pipeline.py
# ══════════════════════════════════════════════════════════════════
SECTION_10 = """
# Sau khi tải best.pt về máy, copy vào thư mục dự án rồi
# cập nhật hàm _detect_text() trong pipeline.py như sau:

PIPELINE_UPDATE = '''
def _detect_text(self, img):
    \"\"\"
    V2: Dùng YOLO detect thay vì EasyOCR detect.
    Nhanh hơn 5-10x, ít false positive hơn nhiều.
    \"\"\"
    try:
        results = self.model_yolo(
            img,
            conf=0.45,     # Tăng lên 0.55 nếu còn false positive
            iou=0.45,      # NMS threshold
            imgsz=640,
            verbose=False,
        )
        boxes = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy().astype(int)
                conf_val = float(box.conf[0])
                # Filter box quá nhỏ
                if conf_val >= 0.45 and (x2-x1) >= 10 and (y2-y1) >= 8:
                    boxes.append((x1, y1, x2, y2))
        return boxes
    except Exception as e:
        print(f"[detect_yolo] {e}")
        return []
'''

print(PIPELINE_UPDATE)
print()
print("Ngoài ra, trong __init__ của OCRPipeline, đổi:")
print("  self.detect_threshold = 0.45  # (từ 0.3)")
print()
print("Nếu vẫn còn false positive sau khi dùng model v2:")
print("  1. Tăng conf=0.55 hoặc 0.60")
print("  2. Thêm area filter: bỏ box nhỏ hơn 20x15px")
print("  3. Thêm aspect ratio filter: bỏ box gần hình vuông (chữ thường là rect ngang)")
"""

# ══════════════════════════════════════════════════════════════════
# ██  MAIN – Chạy toàn bộ nếu gọi trực tiếp
# ══════════════════════════════════════════════════════════════════

def print_section(num: int, title: str, code: str):
    """In hướng dẫn cho từng section."""
    print(f"\n{'='*70}")
    print(f"  SECTION {num} – {title}")
    print(f"{'='*70}")
    print(code)


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════════════════════╗")
    print("║  🀄  COLAB TRAIN GUIDE – YOLOv8s Chinese Text Detection v2       ║")
    print("╚══════════════════════════════════════════════════════════════════╝")
    print()
    print("CÁCH SỬ DỤNG FILE NÀY:")
    print("  1. Upload file này lên Google Colab")
    print("  2. Bật GPU: Runtime → Change runtime type → T4 GPU")
    print("  3. Tạo notebook mới, copy từng SECTION vào cell riêng")
    print("  4. Chạy từng cell theo thứ tự 0→10")
    print()
    print("HOẶC: Chạy trực tiếp trong Colab terminal:")
    print("  !python train_colab.py --run-all")
    print()
    print("Các section:")
    print("  Section 0:  Cài thư viện (pip install)")
    print("  Section 1:  Cài font chữ Trung (apt-get + kiểm tra)")
    print("  Section 2:  Tải COCO val2017 (~1GB) làm background thực tế")
    print("  Section 3:  Định nghĩa hàm tạo dataset (positive/negative/hard)")
    print("  Section 4:  Sinh 5000 ảnh dataset (~15-20 phút trên T4)")
    print("  Section 5:  Tạo data_v2.yaml")
    print("  Section 6:  Train YOLOv8s (~1-2 giờ trên T4, 150 epochs)")
    print("  Section 7:  Đánh giá mAP, Precision, Recall, F1")
    print("  Section 8:  Visualize inference (xem kết quả bằng hình)")
    print("  Section 9:  Export ONNX + Download model về máy")
    print("  Section 10: Hướng dẫn cập nhật pipeline.py")

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-all", action="store_true",
                        help="Chạy toàn bộ pipeline (chỉ dùng trên Colab)")
    parser.add_argument("--print-sections", action="store_true",
                        help="In code của tất cả sections")
    args = parser.parse_args()

    if args.print_sections:
        sections = [
            (0, "Cài thư viện", SECTION_0),
            (1, "Font chữ Trung", SECTION_1),
            (2, "COCO background", SECTION_2),
            (3, "Hàm tạo dataset", SECTION_3),
            (4, "Sinh dataset", SECTION_4),
            (5, "data.yaml", SECTION_5),
            (6, "Train YOLOv8s", SECTION_6),
            (7, "Đánh giá", SECTION_7),
            (8, "Visualize", SECTION_8),
            (9, "Export + Download", SECTION_9),
            (10, "Cập nhật pipeline.py", SECTION_10),
        ]
        for num, title, code in sections:
            print_section(num, title, code)

    elif args.run_all:
        # Thực thi toàn bộ pipeline
        print("\n🚀 Chạy toàn bộ pipeline...")
        import subprocess
        sections_code = [
            SECTION_0, SECTION_1, SECTION_2, SECTION_3,
            SECTION_4, SECTION_5, SECTION_6, SECTION_7,
            SECTION_8, SECTION_9,
        ]
        combined = "\n\n".join(sections_code)
        exec(combined)
