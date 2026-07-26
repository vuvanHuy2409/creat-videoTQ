import cv2
import os
import numpy as np
import subprocess
from src.utils import setup_logging

logger = setup_logging("ai_subtitle_blur")


def apply_ai_blur(
    video_path: str,
    output_path: str,
    ocr_every_n_frames: int = 2,   # OCR mỗi 2 frame để bắt phụ đề ngay khi xuất hiện
    blur_strength: int = 61,        # Gaussian kernel (lẻ, càng lớn càng mờ)
    feather_px: int = 20,           # Viền mờ dần ra ngoài box (px)
) -> bool:
    """
    Xoá phụ đề Trung Quốc bằng EasyOCR + Gaussian blur với viền feather.
    - Phát hiện ngay khi phụ đề xuất hiện (ocr mỗi 2 frame)
    - Blur chính xác vùng chữ + viền mờ dần tự nhiên
    """
    logger.info(f"AI subtitle blur: {video_path} → {output_path}")

    import easyocr
    import torch
    import warnings
    
    # Bỏ qua cảnh báo 'pin_memory' của PyTorch trên chip Apple Silicon (MPS)
    warnings.filterwarnings("ignore", category=UserWarning, message=".*pin_memory.*")

    try:
        use_gpu = torch.backends.mps.is_available() or torch.cuda.is_available()
        logger.info(f"Loading EasyOCR (GPU={use_gpu})...")
        reader_ocr = easyocr.Reader(['ch_sim'], gpu=use_gpu, cudnn_benchmark=False)
        logger.info("EasyOCR ready.")
    except Exception as e:
        logger.error(f"EasyOCR load failed: {e}")
        return False

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Cannot open video!")
        return False

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_fps    = cap.get(cv2.CAP_PROP_FPS) or 30
    w_orig = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h_orig = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    logger.info(f"Video {w_orig}×{h_orig} @ {video_fps:.1f}fps  {total_frames} frames")

    # ── ffmpeg nhận raw frame BGR từ stdin ───────────────────────────────────
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{w_orig}x{h_orig}", "-pix_fmt", "bgr24",
        "-r", str(video_fps), "-i", "-",
        "-i", video_path,
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "16",
        "-pix_fmt", "yuv420p", "-c:a", "copy",
        output_path,
    ]
    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # ── Tiền xử lý ảnh để OCR tốt hơn ──────────────────────────────────────
    def _preprocess_crop(crop):
        """Tăng contrast để EasyOCR nhận diện chữ rõ hơn."""
        gray  = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)

    # ── Nhận diện text, trả về toạ độ frame gốc (được scan toàn bộ khung hình) ──────────
    def _detect_text(frame):
        fh, fw = frame.shape[:2]

        # Scan toàn bộ frame (không cắt)
        img = frame.copy()

        # Scale lên nếu cần (EasyOCR tốt nhất ≥640px wide)
        scale = max(1.0, 640.0 / fw)
        if scale > 1.0:
            img = cv2.resize(img, (int(fw * scale), int(fh * scale)),
                             interpolation=cv2.INTER_CUBIC)

        img = _preprocess_crop(img)

        import re
        
        try:
            # Dùng readtext thay vì detect để thực sự ĐỌC chữ
            # Điều này giúp loại bỏ các vật thể linh tinh bị CRAFT detect nhầm thành chữ
            results = reader_ocr.readtext(
                img,
                min_size=10,
                text_threshold=0.35,  # Tăng ngưỡng nhận diện để tránh nhiễu
                low_text=0.30,
                link_threshold=0.35,
                canvas_size=1280,
                mag_ratio=1.0,
            )
        except Exception as e:
            logger.warning(f"[detect] {e}")
            return []

        raw = []
        for (bbox, text, prob) in results:
            # bbox là list 4 điểm: [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]
            xs = [pt[0] for pt in bbox]
            ys = [pt[1] for pt in bbox]
            
            gx1 = max(0, int(min(xs) / scale))
            gy1 = max(0, int(min(ys) / scale))
            gx2 = min(fw, int(max(xs) / scale))
            gy2 = min(fh, int(max(ys) / scale))
            
            # Lọc nhiễu CỰC KỲ KHẮT KHE: BẮT BUỘC phải có chứa ký tự tiếng Trung
            # Ký tự tiếng Trung nằm trong dải Unicode \u4e00-\u9fff
            # Bỏ qua hoàn toàn các vùng chỉ có nét thẳng/nhiễu bị AI nhận nhầm thành chữ tiếng Anh (l, I, t...)
            has_chinese = bool(re.search(r'[\u4e00-\u9fff]', text))
            
            # Nếu khung chứa chữ quá nhỏ thì bỏ qua
            if (gx2 - gx1) >= 8 and (gy2 - gy1) >= 6:
                if has_chinese:
                    raw.append((gx1, gy1, gx2, gy2))
                else:
                    logger.debug(f"[filter] Ignored noise: text='{text}', prob={prob:.2f}")

        if raw:
            logger.debug(f"[detect] {len(raw)} valid subtitle boxes found")
        return raw


    # ── Blur với viền mờ dần (feather mask) ─────────────────────────────────
    def _blur_with_feather(frame, x1, y1, x2, y2):
        """
        1. Tạo mask hình chữ nhật trắng đúng vùng chữ
        2. Blur mask bằng Gaussian → viền mờ dần ra ngoài
        3. Blur mạnh vùng ảnh tương ứng
        4. Blend: output = blur * mask + original * (1 - mask)
        Kết quả: chữ biến mất, viền hoà tan tự nhiên vào nền.
        """
        fh, fw = frame.shape[:2]
        fp = feather_px

        # Vùng expand đủ rộng để chứa feather
        rx1 = max(0,  x1 - fp)
        ry1 = max(0,  y1 - fp)
        rx2 = min(fw, x2 + fp)
        ry2 = min(fh, y2 + fp)
        if rx2 <= rx1 or ry2 <= ry1:
            return frame

        rh, rw = ry2 - ry1, rx2 - rx1

        # Mask: 1 ở vùng chữ, 0 ở vùng xung quanh, mờ dần ở viền
        mask = np.zeros((rh, rw), dtype=np.float32)
        # Tọa độ chữ trong hệ mask
        mx1 = x1 - rx1;  my1 = y1 - ry1
        mx2 = x2 - rx1;  my2 = y2 - ry1
        mx1 = max(0, mx1); my1 = max(0, my1)
        mx2 = min(rw, mx2); my2 = min(rh, my2)
        mask[my1:my2, mx1:mx2] = 1.0

        # Gaussian blur trên mask → viền feather
        ksize = fp * 2 + 1  # kernel phải lẻ
        mask  = cv2.GaussianBlur(mask, (ksize, ksize), fp / 2.5)
        mask  = np.clip(mask, 0.0, 1.0)

        # Blur mạnh vùng ảnh gốc (3 lần để chắc xoá sạch)
        bk      = blur_strength | 1   # đảm bảo lẻ
        roi_orig = frame[ry1:ry2, rx1:rx2].copy()
        roi_blur = cv2.GaussianBlur(roi_orig, (bk, bk), 0)
        roi_blur = cv2.GaussianBlur(roi_blur, (bk, bk), 0)
        roi_blur = cv2.GaussianBlur(roi_blur, (bk, bk), 0)

        # Blend theo mask
        mask3   = mask[:, :, np.newaxis]          # (rh, rw, 1) để broadcast
        blended = (roi_blur.astype(np.float32) * mask3
                   + roi_orig.astype(np.float32) * (1.0 - mask3))
        frame[ry1:ry2, rx1:rx2] = np.clip(blended, 0, 255).astype(np.uint8)
        return frame

    # ── Vòng lặp xử lý frame ────────────────────────────────────────────────
    frame_count      = 0
    cached_boxes     = []
    detected_y1_list = []
    detected_y2_list = []

    # PAD sát chữ (chỉ vài px để bao hết hẳn nét chữ)
    PAD_X = 5
    PAD_Y = 4

    logger.info("Processing frames...")
    while cap.isOpened():
        ret, frame = cap.read()
        if not ret:
            break
        frame_count += 1

        # OCR mỗi N frame
        if frame_count == 1 or frame_count % ocr_every_n_frames == 0:
            raw = _detect_text(frame)
            cached_boxes = raw
            for (x1, y1, x2, y2) in raw:
                if y1 > h_orig * 0.40:
                    detected_y1_list.append(y1)
                    detected_y2_list.append(y2)

        # Apply blur + feather lên từng box
        annotated = frame.copy()
        for (x1, y1, x2, y2) in cached_boxes:
            bx1 = max(0,     x1 - PAD_X)
            by1 = max(0,     y1 - PAD_Y)
            bx2 = min(w_orig, x2 + PAD_X)
            by2 = min(h_orig, y2 + PAD_Y)
            annotated = _blur_with_feather(annotated, bx1, by1, bx2, by2)

        # Gửi frame đã xử lý sang ffmpeg
        if ffmpeg_proc:
            try:
                ffmpeg_proc.stdin.write(annotated.tobytes())
            except Exception as e:
                logger.error(f"ffmpeg write error: {e}")
                break

        if frame_count % 100 == 0:
            logger.info(f"  {frame_count}/{total_frames} frames | boxes={len(cached_boxes)}")

    cap.release()
    if ffmpeg_proc:
        ffmpeg_proc.stdin.close()
        ffmpeg_proc.wait()

    # Lưu tọa độ Y trung tâm vùng phụ đề để đặt phụ đề dịch đúng chỗ
    if detected_y1_list and detected_y2_list:
        import statistics
        med_y1   = int(statistics.median(detected_y1_list))
        med_y2   = int(statistics.median(detected_y2_list))
        y_center = (med_y1 + med_y2) // 2
        y_pos_file = os.path.join(os.path.dirname(output_path), "subtitle_y_pos.txt")
        try:
            with open(y_pos_file, "w") as f:
                f.write(str(y_center))
            logger.info(f"Subtitle zone y1={med_y1} y2={med_y2} center={y_center} → {y_pos_file}")
        except Exception as e:
            logger.error(f"Cannot save subtitle_y_pos.txt: {e}")

    logger.info(f"Done → {output_path}")
    return True
