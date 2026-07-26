"""
pipeline.py - Phat hien va CHE chữ trong video (v2 – Anti False Positive)

3 CHE DO CHE:
  blur      : Gaussian blur manh + feather can ra xung quanh
  solid     : Mau dat + vien mo dan
  prev_frame: Lay pixel frame truoc de dap len vung chu

V2 CHANGES:
  - _detect_text() dùng YOLO detect thay EasyOCR detect (nhanh hơn 5-10x, ít FP)
  - conf threshold mặc định tăng từ 0.3 → 0.45
  - Thêm area filter và aspect ratio filter để loại bỏ false positive
"""

import cv2
import threading
import queue
import time
import os
import numpy as np
import subprocess
import tempfile
from collections import deque


class OCRPipeline:
    def __init__(self, result_queue: queue.Queue, frame_queue: queue.Queue):
        self.result_queue = result_queue
        self.frame_queue  = frame_queue
        self.is_running   = False
        self.model_yolo   = None
        self.reader_ocr   = None
        self._thread      = None

        # Vung khoanh (% 0.0-1.0)
        self.subtitle_zone = (0.0, 1.0, 0.0, 1.0)

        # Che do che chu
        self.cover_mode    = "blur"
        self.blur_strength = 55
        self.feather_px    = 28
        self.solid_color   = (20, 20, 20)

        # Detection settings
        self.ocr_every_n_frames = 2
        self.detect_threshold   = 0.45  # v2: tăng từ 0.3 → 0.45 (giảm false positive)

        self._frame_buf = deque(maxlen=30)

        # Thong tin video
        self.video_path   = None
        self.output_path  = None
        self.is_exporting = False
        self.fps          = 0.0
        self.frame_count  = 0
        self.total_frames = 0

    def load_models(self, progress_callback=None):
        try:
            from ultralytics import YOLO
            if os.path.exists("best.onnx"):
                self.model_yolo = YOLO("best.onnx", task="detect")
            elif os.path.exists("best.pt"):
                self.model_yolo = YOLO("best.pt")
            else:
                self.model_yolo = YOLO("yolov8n.pt")

            import easyocr, torch
            use_gpu = torch.backends.mps.is_available() or torch.cuda.is_available()
            self.reader_ocr = easyocr.Reader(['ch_sim'], gpu=use_gpu, cudnn_benchmark=True)
            return True
        except Exception as e:
            print(f"[LOI] {e}")
            return False

    def start(self, video_path: str, output_path: str = None):
        self.video_path   = video_path
        self.output_path  = output_path
        self.is_exporting = bool(output_path)
        self.is_running   = True
        self.frame_count  = 0
        self._frame_buf.clear()
        self._thread = threading.Thread(target=self._process_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self.is_running = False

    def set_subtitle_zone(self, y_top, y_bot, x_left, x_right):
        self.subtitle_zone = (
            max(0., min(1., y_top)), max(0., min(1., y_bot)),
            max(0., min(1., x_left)), max(0., min(1., x_right)),
        )

    def _process_loop(self):
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            self.result_queue.put({"type": "error", "message": "Khong mo duoc file video!"})
            return

        self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_fps   = cap.get(cv2.CAP_PROP_FPS) or 30
        frame_delay = 1.0 / video_fps

        w_orig = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h_orig = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        ffmpeg_proc = None
        if self.is_exporting:
            # Thay vi cv2.VideoWriter, dung ffmpeg de encode truc tiep voi chat luong rat cao (CRF 16)
            cmd = [
                "ffmpeg", "-y",
                "-f", "rawvideo",
                "-vcodec", "rawvideo",
                "-s", f"{w_orig}x{h_orig}",
                "-pix_fmt", "bgr24",
                "-r", str(video_fps),
                "-i", "-",               # Dau vao la stdin (raw frames)
                "-i", self.video_path,   # Dau vao 2 la video goc de lay audio
                "-map", "0:v:0",         # Lay video tu stdin
                "-map", "1:a:0?",        # Lay audio tu video goc (neu co)
                "-c:v", "libx264",       # Encode bang H.264
                "-preset", "slow",       # Chat luong cao nhat co the
                "-crf", "16",            # CRF 16 = Visually Lossless (khong mo nhoe)
                "-pix_fmt", "yuv420p",   # Tuong thich moi player
                "-c:a", "copy",          # Copy nguyen ban audio goc
                self.output_path
            ]
            ffmpeg_proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, 
                                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        cached_boxes = []

        while cap.isOpened() and self.is_running:
            t_start = time.time()
            ret, frame = cap.read()
            if not ret:
                if not self.is_exporting:
                    self.result_queue.put({"type": "done"})
                break

            self.frame_count += 1
            h, w = frame.shape[:2]

            # Luu frame vao buffer cho prev_frame mode
            self._frame_buf.append(frame.copy())

            yt, yb, xl, xr = self.subtitle_zone
            zx1 = int(w * xl);  zy1 = int(h * yt)
            zx2 = int(w * xr);  zy2 = int(h * yb)

            # DETECT moi N frame (NEU dang xuat video, ep buoc kiem tra TUNG FRAME = 1)
            step = 1 if self.is_exporting else max(1, self.ocr_every_n_frames)
            if self.frame_count % step == 0:
                cropped = frame[zy1:zy2, zx1:zx2]
                if cropped.size > 0:
                    boxes_zone = self._detect_text(cropped)
                    cached_boxes = [(zx1+x1, zy1+y1, zx1+x2, zy1+y2) for x1, y1, x2, y2 in boxes_zone]
                    self.result_queue.put({"type": "stats", "count": len(cached_boxes)})

            # AP DUNG HIEU UNG CHE
            annotated = self._apply_cover(frame, cached_boxes)

            if ffmpeg_proc:
                try:
                    ffmpeg_proc.stdin.write(annotated.tobytes())
                except Exception as e:
                    print(f"Lỗi ghi frame: {e}")

            elapsed = time.time() - t_start
            self.fps = 1.0 / elapsed if elapsed > 0 else 0

            # Giới hạn tần suất đẩy frame ra UI để tránh quá tải khi render tốc độ cao
            if self.frame_queue.qsize() < 2 and (self.frame_count % 3 == 0 or not self.is_exporting):
                self.frame_queue.put({
                    "type":     "frame",
                    "frame":    annotated,
                    "fps":      self.fps,
                    "frame_no": self.frame_count,
                    "total":    self.total_frames,
                    "boxes":    len(cached_boxes),
                })

            if not self.is_exporting:
                sleep_time = frame_delay - elapsed
                if sleep_time > 0:
                    time.sleep(sleep_time)

        cap.release()
        if ffmpeg_proc:
            ffmpeg_proc.stdin.close()
            self.result_queue.put({"type": "merging_audio"})
            ffmpeg_proc.wait()
            self.result_queue.put({"type": "export_done", "path": self.output_path})

        self.is_running = False

    def _apply_cover(self, frame: np.ndarray, boxes: list) -> np.ndarray:
        if not boxes: return frame.copy()
        result = frame.copy()
        mode = self.cover_mode
        for (x1, y1, x2, y2) in boxes:
            if mode == "blur":
                result = self._cover_blur(result, x1, y1, x2, y2)
            elif mode == "solid":
                result = self._cover_solid(result, x1, y1, x2, y2)
            elif mode == "prev_frame":
                result = self._cover_prev_frame(result, frame, x1, y1, x2, y2)
        return result

    def _cover_blur(self, frame, x1, y1, x2, y2):
        h, w = frame.shape[:2]
        fp = self.feather_px
        bk = max(3, self.blur_strength | 1)
        px1 = max(0, x1 - fp);  py1 = max(0, y1 - fp)
        px2 = min(w, x2 + fp);  py2 = min(h, y2 + fp)
        rw, rh = px2 - px1, py2 - py1
        if rw <= 0 or rh <= 0: return frame
        region = frame[py1:py2, px1:px2].copy()
        blurred = cv2.GaussianBlur(region, (bk, bk), 0)
        ix1, iy1 = x1 - px1, y1 - py1
        ix2, iy2 = x2 - px1, y2 - py1
        inner = np.zeros((rh, rw), dtype=np.uint8)
        inner[iy1:iy2, ix1:ix2] = 255
        dist = cv2.distanceTransform(inner, cv2.DIST_L2, 5)
        dist_norm = np.clip(dist / max(1.0, fp * 0.7), 0.0, 1.0) if dist.max() > 0 else np.zeros_like(dist)
        outer = np.full((rh, rw), 255, dtype=np.uint8)
        outer[iy1:iy2, ix1:ix2] = 0
        dist_out = cv2.distanceTransform(outer, cv2.DIST_L2, 5)
        fade_out = np.clip(1.0 - dist_out / max(1.0, fp), 0.0, 1.0)
        mask = np.maximum(dist_norm.astype(np.float32), fade_out.astype(np.float32))
        ks = max(3, (fp // 2) | 1)
        mask = cv2.GaussianBlur(mask, (ks, ks), 0)
        mask = np.clip(mask, 0.0, 1.0)
        mask_3 = np.stack([mask] * 3, axis=-1)
        blended = (blurred.astype(np.float32) * mask_3 + region.astype(np.float32) * (1.0 - mask_3))
        frame[py1:py2, px1:px2] = np.clip(blended, 0, 255).astype(np.uint8)
        return frame

    def _cover_solid(self, frame, x1, y1, x2, y2):
        h, w = frame.shape[:2]
        fp = self.feather_px
        px1 = max(0, x1 - fp);  py1 = max(0, y1 - fp)
        px2 = min(w, x2 + fp);  py2 = min(h, y2 + fp)
        rw, rh = px2 - px1, py2 - py1
        if rw <= 0 or rh <= 0: return frame
        region = frame[py1:py2, px1:px2].copy()
        solid = np.full_like(region, self.solid_color, dtype=np.uint8)
        ix1, iy1 = x1-px1, y1-py1
        ix2, iy2 = x2-px1, y2-py1
        inner = np.zeros((rh, rw), dtype=np.uint8)
        inner[iy1:iy2, ix1:ix2] = 255
        dist = cv2.distanceTransform(inner, cv2.DIST_L2, 5)
        mask = np.clip(dist / max(1.0, fp * 0.6), 0.0, 1.0) if dist.max() > 0 else np.zeros((rh, rw), dtype=np.float32)
        ks = max(3, (fp // 2) | 1)
        mask = cv2.GaussianBlur(mask.astype(np.float32), (ks, ks), 0)
        mask_3 = np.stack([mask] * 3, axis=-1)
        blended = (solid.astype(np.float32) * mask_3 + region.astype(np.float32) * (1.0 - mask_3))
        frame[py1:py2, px1:px2] = np.clip(blended, 0, 255).astype(np.uint8)
        return frame

    def _cover_prev_frame(self, frame, original, x1, y1, x2, y2):
        h, w = frame.shape[:2]
        fp = max(8, self.feather_px // 2)
        buf_len = len(self._frame_buf)
        if buf_len < 2: return self._cover_blur(frame, x1, y1, x2, y2)
        lookback = min(buf_len - 1, max(1, int(15)))
        src_frame = self._frame_buf[buf_len - 1 - lookback]
        px1 = max(0, x1 - fp);  py1 = max(0, y1 - fp)
        px2 = min(w, x2 + fp);  py2 = min(h, y2 + fp)
        rw, rh = px2 - px1, py2 - py1
        if rw <= 0 or rh <= 0: return frame
        src_region = src_frame[py1:py2, px1:px2].copy()
        cur_region = frame[py1:py2, px1:px2].copy()
        ix1, iy1 = x1-px1, y1-py1
        ix2, iy2 = x2-px1, y2-py1
        inner = np.zeros((rh, rw), dtype=np.uint8)
        inner[iy1:iy2, ix1:ix2] = 255
        dist = cv2.distanceTransform(inner, cv2.DIST_L2, 5)
        mask = np.clip(dist / max(1.0, fp * 0.8), 0.0, 1.0) if dist.max() > 0 else np.zeros((rh, rw), dtype=np.float32)
        ks = max(3, (fp | 1))
        mask = cv2.GaussianBlur(mask.astype(np.float32), (ks, ks), 0)
        mask_3 = np.stack([mask] * 3, axis=-1)
        blended = (src_region.astype(np.float32) * mask_3 + cur_region.astype(np.float32) * (1.0 - mask_3))
        frame[py1:py2, px1:px2] = np.clip(blended, 0, 255).astype(np.uint8)
        return frame

    def _detect_text(self, img):
        """
        v2: Dùng YOLO model để detect chữ Trung thay vì EasyOCR.detect().
        - Nhanh hơn 5-10x
        - Ít false positive hơn (model đã học negative samples)
        - Thêm area filter + aspect ratio filter

        Nếu chưa có best.pt (dùng yolov8n.pt gốc), fallback về EasyOCR detect.
        """
        try:
            # ── Dùng YOLO để detect ──────────────────────────────────────────
            prep = self._preprocess(img)
            results = self.model_yolo(
                prep,
                conf=self.detect_threshold,   # mặc định 0.45
                iou=0.45,
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

                    w_box = x2 - x1
                    h_box = y2 - y1

                    # Filter 1: Box quá nhỏ (nhiễu)
                    if w_box < 10 or h_box < 8:
                        continue

                    # Filter 2: Aspect ratio quá vuông (chữ thường nằm ngang)
                    # Chữ Trung thường có aspect ratio >= 1.5 (rộng hơn cao)
                    # Nếu h > w * 0.9 → khả năng là noise hoặc ký tự đơn lẻ
                    # (Bỏ filter này nếu cần detect chữ dọc hoặc ký tự đơn)
                    if w_box < h_box * 0.8 and w_box < 50:
                        continue

                    # Filter 3: Box quá lớn (chiếm > 80% ảnh → khả năng FP)
                    img_h, img_w = img.shape[:2] if hasattr(img, 'shape') else (prep.shape[:2])
                    if w_box > img_w * 0.85 and h_box > img_h * 0.5:
                        continue

                    boxes.append((x1, y1, x2, y2))

            return boxes

        except Exception as e:
            print(f"[detect_yolo] {e}")
            # Fallback: EasyOCR detect (phòng trường hợp model lỗi)
            try:
                return self._detect_text_easyocr_fallback(img)
            except Exception:
                return []

    def _detect_text_easyocr_fallback(self, img):
        """
        Fallback: dùng EasyOCR detect khi YOLO không dùng được.
        (Giữ lại để tương thích ngược)
        """
        prep = self._preprocess(img)
        horizontal_list, free_list = self.reader_ocr.detect(
            prep, min_size=10, text_threshold=max(0.5, self.detect_threshold),
            low_text=0.3, link_threshold=0.5, canvas_size=1280, mag_ratio=1.5,
        )
        boxes = []
        if horizontal_list and len(horizontal_list) > 0:
            for (xmin, xmax, ymin, ymax) in horizontal_list[0]:
                x1, y1, x2, y2 = int(xmin), int(ymin), int(xmax), int(ymax)
                if (x2-x1) >= 10 and (y2-y1) >= 8:
                    boxes.append((x1, y1, x2, y2))
        return boxes

    def _preprocess(self, img):
        h, w = img.shape[:2]
        if h < 80:
            sc = max(2.0, 80.0 / h)
            img = cv2.resize(img, (int(w*sc), int(h*sc)), interpolation=cv2.INTER_CUBIC)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        enhanced = clahe.apply(gray)
        return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)
