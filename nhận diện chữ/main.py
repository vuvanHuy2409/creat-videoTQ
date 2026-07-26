"""
main.py - Che chu trong video (Blur / Mau dat / Frame truoc) voi chuc nang Xuat Video
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import queue, threading, os
from itertools import cycle
import subprocess

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

import cv2
from pipeline import OCRPipeline


def cv2_to_tk(cv_img, max_w, max_h):
    if cv_img is None or not PIL_AVAILABLE:
        return None, 0, 0
    h, w = cv_img.shape[:2]
    scale = min(max_w / w, max_h / h, 1.0)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    resized = cv2.resize(cv_img, (nw, nh), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return ImageTk.PhotoImage(Image.fromarray(rgb)), nw, nh


class App:
    BG    = "#0d1117"
    PANEL = "#161b22"
    CARD  = "#21262d"
    BLUE  = "#388bfd"
    GREEN = "#3fb950"
    RED   = "#f85149"
    GOLD  = "#d29922"
    TEXT  = "#e6edf3"
    MUTED = "#8b949e"
    BORD  = "#30363d"
    CYAN  = "#39d353"

    _SPIN = cycle(["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"])

    def __init__(self, root):
        self.root = root
        self.root.title("Che Chu Trong Video  —  Text Cover")
        self.root.geometry("1300x820")
        self.root.configure(bg=self.BG)

        self.rq = queue.Queue()
        self.fq = queue.Queue(maxsize=4)
        self.pipeline = OCRPipeline(self.rq, self.fq)

        self.video_path   = None
        self.video_w = self.video_h = 1
        self._tk_img      = None
        self._img_offset  = (0, 0)
        self._img_size    = (1, 1)
        self.is_running   = False
        self._model_ready = False

        self._zone   = [0.0, 1.0, 0.0, 1.0]
        self._drag0  = None
        self._drag_id = None

        self._build_ui()
        self._ui_loop()
        threading.Thread(target=self.pipeline.load_models,
                         kwargs={"progress_callback": None},
                         daemon=True).start()

    # ──────────────────── BUILD UI ───────────────────────

    def _build_ui(self):
        # HEADER
        hdr = tk.Frame(self.root, bg=self.PANEL, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="🎭  Che Chữ Trong Video",
                 font=("Helvetica", 18, "bold"),
                 bg=self.PANEL, fg=self.TEXT).pack(side=tk.LEFT, padx=18)
        tk.Label(hdr, text="Blur  ·  Màu Đặc  ·  Frame Trước  —  Xuất Video chất lượng gốc",
                 font=("Helvetica", 10),
                 bg=self.PANEL, fg=self.MUTED).pack(side=tk.LEFT)
        self.badge = tk.Label(hdr, text="  Đang tải model...",
                              font=("Helvetica", 10, "bold"),
                              bg="#2d2600", fg="#e3b341", padx=10, pady=3)
        self.badge.pack(side=tk.RIGHT, padx=16)

        # BODY
        body = tk.Frame(self.root, bg=self.BG)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=(6, 0))
        body.columnconfigure(0, weight=4)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        self._build_video_col(body)
        self._build_ctrl_col(body)
        self._build_bottom()

    def _build_video_col(self, parent):
        col = tk.Frame(parent, bg=self.BG)
        col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        col.rowconfigure(1, weight=1)
        col.columnconfigure(0, weight=1)

        top = tk.Frame(col, bg=self.BG)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.video_lbl = tk.Label(
            top, text="  Chưa chọn video  —  Kéo chuột để khoanh vùng cần che",
            font=("Helvetica", 10), bg=self.BG, fg=self.MUTED, anchor="w")
        self.video_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Button(top, text="  📂  Chọn Video  ",
                  command=self._pick_video,
                  font=("Helvetica", 10, "bold"),
                  bg=self.BLUE, fg="white",
                  activebackground="#1f6feb",
                  relief="flat", cursor="hand2", padx=6, pady=5
                  ).pack(side=tk.RIGHT)

        # Canvas
        cf = tk.Frame(col, bg=self.BORD, bd=1)
        cf.grid(row=1, column=0, sticky="nsew")
        cf.rowconfigure(0, weight=1); cf.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(cf, bg="#010409",
                                highlightthickness=0, cursor="crosshair")
        self.canvas.grid(sticky="nsew")
        self._placeholder()
        self.canvas.bind("<ButtonPress-1>",  self._d_start)
        self.canvas.bind("<B1-Motion>",       self._d_move)
        self.canvas.bind("<ButtonRelease-1>", self._d_end)

        # Info bar
        ib = tk.Frame(col, bg=self.PANEL, pady=4)
        ib.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        self.fps_lbl = tk.Label(ib, text="FPS: --",
                                font=("Courier New", 10, "bold"),
                                bg=self.PANEL, fg=self.GREEN)
        self.fps_lbl.pack(side=tk.LEFT, padx=12)
        self.frame_lbl = tk.Label(ib, text="Frame: 0 / 0",
                                  font=("Courier New", 10),
                                  bg=self.PANEL, fg=self.MUTED)
        self.frame_lbl.pack(side=tk.LEFT, padx=6)
        self.box_lbl = tk.Label(ib, text="🎭  Vùng che: 0",
                                font=("Helvetica", 10, "bold"),
                                bg=self.PANEL, fg=self.CYAN)
        self.box_lbl.pack(side=tk.LEFT, padx=16)
        self.zone_lbl = tk.Label(ib, text="Zone: toàn frame",
                                 font=("Helvetica", 9),
                                 bg=self.PANEL, fg=self.MUTED)
        self.zone_lbl.pack(side=tk.RIGHT, padx=10)

        # Progress
        style = ttk.Style(); style.theme_use("clam")
        style.configure("G.Horizontal.TProgressbar",
                        troughcolor=self.CARD,
                        background=self.GREEN, thickness=5)
        self.prog = tk.DoubleVar()
        ttk.Progressbar(col, variable=self.prog, maximum=100,
                        style="G.Horizontal.TProgressbar"
                        ).grid(row=3, column=0, sticky="ew", pady=(3, 0))

    def _build_ctrl_col(self, parent):
        col = tk.Frame(parent, bg=self.PANEL)
        col.grid(row=0, column=1, sticky="nsew")
        col.columnconfigure(0, weight=1)

        def section(title):
            f = tk.LabelFrame(col, text=f"  {title}  ",
                              font=("Helvetica", 9, "bold"),
                              bg=self.PANEL, fg=self.MUTED,
                              bd=1, relief="groove")
            f.pack(fill=tk.X, padx=8, pady=(8, 2))
            return f

        # ══ CHE DO CHE ══════════════════════════════════
        mf = section("🎭  Chế Độ Che")

        self.mode_var = tk.StringVar(value="blur")
        modes = [
            ("blur",       "🌫️  Blur mờ lan dần",   "#58a6ff"),
            ("solid",      "⬛  Màu đặc + feather",  "#8b949e"),
            ("prev_frame", "🎞️  Lấy frame trước",    "#3fb950"),
        ]
        for val, label, color in modes:
            rb = tk.Radiobutton(
                mf, text=label, variable=self.mode_var, value=val,
                font=("Helvetica", 10, "bold"),
                bg=self.PANEL, fg=color,
                activebackground=self.CARD,
                selectcolor=self.CARD,
                indicatoron=0,
                bd=0, relief="flat",
                padx=10, pady=6,
                cursor="hand2",
                command=self._apply_mode)
            rb.pack(fill=tk.X, padx=6, pady=2)

        # ══ THAM SO HIEU UNG ══════════════════════════
        ef = section("🔧  Tham Số Hiệu Ứng")

        sl_cfg = dict(bg=self.PANEL, fg=self.TEXT,
                      troughcolor=self.CARD, highlightthickness=0,
                      orient=tk.HORIZONTAL, length=170,
                      showvalue=True, font=("Helvetica", 8))

        tk.Label(ef, text="Cường độ blur:", font=("Helvetica", 9),
                 bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=8, pady=(4,0))
        self.blur_var = tk.IntVar(value=55)
        tk.Scale(ef, variable=self.blur_var, from_=11, to=101,
                 command=lambda v: setattr(self.pipeline, "blur_strength",
                                           int(float(v)) | 1),
                 **{**sl_cfg, "resolution": 2}).pack(padx=4)

        tk.Label(ef, text="Feather lan ra (px):", font=("Helvetica", 9),
                 bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=8, pady=(4,0))
        self.feather_var = tk.IntVar(value=28)
        tk.Scale(ef, variable=self.feather_var, from_=4, to=80,
                 command=lambda v: setattr(self.pipeline, "feather_px", int(float(v))),
                 **{**sl_cfg, "resolution": 2}).pack(padx=4)

        # Mau nen (khi mode = solid)
        tk.Label(ef, text="Màu nền (chế độ Màu Đặc):", font=("Helvetica", 9),
                 bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=8, pady=(6,2))
        cf2 = tk.Frame(ef, bg=self.PANEL)
        cf2.pack(fill=tk.X, padx=6, pady=(0,6))
        solid_colors = [
            ("Đen",  (15,15,15),    "#111"),
            ("Trắng",(240,240,240), "#f0f0f0"),
            ("Xám",  (80,80,80),    "#505050"),
            ("Cam",  (20,100,220),  "#e08000"),
        ]
        for name, bgr, hex_c in solid_colors:
            tk.Button(cf2, text=name,
                      font=("Helvetica", 8),
                      bg=hex_c, fg="white" if hex_c != "#f0f0f0" else "#111",
                      relief="flat", cursor="hand2", padx=4, pady=3,
                      command=lambda c=bgr: setattr(self.pipeline, "solid_color", c)
                      ).pack(side=tk.LEFT, padx=2, expand=True, fill=tk.X)

        # ══ VUNG PHAT HIEN ════════════════════════════
        zf = section("📐  Vùng Phát Hiện")

        zsl = dict(bg=self.PANEL, fg=self.TEXT,
                   troughcolor=self.CARD, highlightthickness=0,
                   orient=tk.HORIZONTAL, length=170,
                   showvalue=True, font=("Helvetica", 8))
        self._zvars = {}
        for lbl, key, default in [
            ("Trên  %:", "yt", 0.0), ("Dưới %:", "yb", 1.0),
            ("Trái  %:", "xl", 0.0), ("Phải %:", "xr", 1.0),
        ]:
            tk.Label(zf, text=lbl, font=("Helvetica", 9),
                     bg=self.PANEL, fg=self.MUTED,
                     anchor="w").pack(anchor="w", padx=8, pady=(4,0))
            v = tk.DoubleVar(value=default)
            self._zvars[key] = v
            tk.Scale(zf, variable=v, from_=0.0, to=1.0,
                     command=lambda _: self._apply_zone(),
                     **{**zsl, "resolution": 0.01}).pack(padx=4)
        tk.Button(zf, text="Toàn frame",
                  command=self._reset_zone,
                  font=("Helvetica", 8),
                  bg=self.CARD, fg=self.TEXT,
                  relief="flat", cursor="hand2", padx=6, pady=3
                  ).pack(fill=tk.X, padx=8, pady=(4,8))

        # ══ DETECTION ════════════════════════════════
        df = section("⚙️  Detection")
        tk.Label(df, text="Ngưỡng:", font=("Helvetica", 9),
                 bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=8, pady=(4,0))
        self.thr_var = tk.DoubleVar(value=0.3)
        tk.Scale(df, variable=self.thr_var, from_=0.1, to=0.9,
                 command=lambda v: setattr(self.pipeline, "detect_threshold", float(v)),
                 **{**zsl, "resolution": 0.05}).pack(padx=4)
        tk.Label(df, text="Detect mỗi N frame:", font=("Helvetica", 9),
                 bg=self.PANEL, fg=self.MUTED).pack(anchor="w", padx=8, pady=(4,0))
        self.nfr_var = tk.IntVar(value=1)
        tk.Scale(df, variable=self.nfr_var, from_=1, to=8,
                 command=lambda v: setattr(self.pipeline, "ocr_every_n_frames", int(float(v))),
                 **{**zsl, "resolution": 1}).pack(padx=4, pady=(0,8))

    def _build_bottom(self):
        bar = tk.Frame(self.root, bg=self.PANEL, pady=8)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        
        self.btn_start = tk.Button(
            bar, text="▶   Xem Thử (Preview)",
            command=self._start,
            font=("Helvetica", 11, "bold"),
            bg=self.CARD, fg="#666",
            activebackground="#238636",
            relief="flat", cursor="hand2",
            padx=18, pady=9, state=tk.DISABLED)
        self.btn_start.pack(side=tk.LEFT, padx=14)

        self.btn_export = tk.Button(
            bar, text="💾   Xuất Video",
            command=self._export,
            font=("Helvetica", 11, "bold"),
            bg=self.BLUE, fg="white",
            activebackground="#1f6feb",
            relief="flat", cursor="hand2",
            padx=18, pady=9, state=tk.DISABLED)
        self.btn_export.pack(side=tk.LEFT, padx=4)

        self.btn_stop = tk.Button(
            bar, text="⏹   Dừng",
            command=self._stop,
            font=("Helvetica", 11, "bold"),
            bg=self.RED, fg="white",
            activebackground="#da3633",
            relief="flat", cursor="hand2",
            padx=18, pady=9, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=4)

        self.status = tk.Label(bar, text="  Đang tải model...",
                               font=("Helvetica", 10),
                               bg=self.PANEL, fg=self.GOLD)
        self.status.pack(side=tk.RIGHT, padx=14)

    # ──────────────────── LOGIC ──────────────────────────

    def _apply_mode(self):
        self.pipeline.cover_mode = self.mode_var.get()
        # Cap nhat mau nhan mode
        labels = {"blur": "🌫️ Blur mờ", "solid": "⬛ Màu đặc",
                  "prev_frame": "🎞️ Frame trước"}
        self.status.config(
            text=f"  Chế độ: {labels.get(self.pipeline.cover_mode, '')}",
            fg=self.TEXT)

    def _apply_zone(self):
        yt = self._zvars["yt"].get(); yb = self._zvars["yb"].get()
        xl = self._zvars["xl"].get(); xr = self._zvars["xr"].get()
        if yt >= yb: yb = min(1.0, yt + 0.05)
        if xl >= xr: xr = min(1.0, xl + 0.05)
        self._zone = [yt, yb, xl, xr]
        self.pipeline.set_subtitle_zone(yt, yb, xl, xr)
        self._redraw_zone()

    def _reset_zone(self):
        self._zone = [0.0, 1.0, 0.0, 1.0]
        for k, v in zip(["yt","yb","xl","xr"], self._zone):
            self._zvars[k].set(v)
        self._apply_zone()

    # ──────────────────── PICK VIDEO ─────────────────────

    def _pick_video(self):
        p = filedialog.askopenfilename(
            title="Chọn file video",
            filetypes=[("Video","*.mp4 *.avi *.mov *.mkv *.flv *.wmv"),
                       ("All","*.*")])
        if not p: return
        self.video_path = p
        cap = cv2.VideoCapture(p)
        self.video_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))  or 1
        self.video_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1
        ret, frame = cap.read(); cap.release()
        self.video_lbl.config(
            text=f"  {os.path.basename(p)}  [{self.video_w}×{self.video_h}]",
            fg=self.TEXT)
        if ret: self._show_static(frame)
        self._redraw_zone()
        if self._model_ready: self._enable_start()
        else: self.status.config(
            text="  Video đã chọn — đang chờ model...", fg=self.GOLD)

    def _show_static(self, frame):
        if not PIL_AVAILABLE: return
        cw = max(1, self.canvas.winfo_width()  or 900)
        ch = max(1, self.canvas.winfo_height() or 560)
        tk_img, nw, nh = cv2_to_tk(frame, cw, ch)
        if tk_img:
            self._tk_img = tk_img
            x0=(cw-nw)//2; y0=(ch-nh)//2
            self._img_offset=(x0,y0); self._img_size=(nw,nh)
            self.canvas.delete("all")
            self.canvas.create_image(cw//2,ch//2,image=tk_img,anchor="center")

    def _placeholder(self):
        self.canvas.delete("all")
        self.canvas.create_text(450,280,
            text="🎬  Chọn video\nrồi kéo chuột để chọn vùng cần che chữ",
            font=("Helvetica",14),fill=self.MUTED,justify="center")

    # ──────────────────── KEO CHUOT ──────────────────────

    def _cv(self,cx,cy):
        x0,y0=self._img_offset; nw,nh=self._img_size
        return (max(0.,min(1.,(cx-x0)/max(1,nw))),
                max(0.,min(1.,(cy-y0)/max(1,nh))))

    def _cp(self,px,py):
        x0,y0=self._img_offset; nw,nh=self._img_size
        return int(x0+px*nw), int(y0+py*nh)

    def _d_start(self,e):
        self._drag0=(e.x,e.y)
        if self._drag_id: self.canvas.delete(self._drag_id)

    def _d_move(self,e):
        if not self._drag0: return
        x0,y0=self._drag0
        if self._drag_id: self.canvas.delete(self._drag_id)
        self._drag_id=self.canvas.create_rectangle(
            x0,y0,e.x,e.y,outline="#58a6ff",width=2,dash=(6,3))

    def _d_end(self,e):
        if not self._drag0: return
        x0c,y0c=self._drag0; x1c,y1c=e.x,e.y
        self._drag0=None
        if x0c>x1c: x0c,x1c=x1c,x0c
        if y0c>y1c: y0c,y1c=y1c,y0c
        xl,yt=self._cv(x0c,y0c); xr,yb=self._cv(x1c,y1c)
        if (xr-xl)<0.02 or (yb-yt)<0.02:
            if self._drag_id: self.canvas.delete(self._drag_id)
            return
        self._zone=[yt,yb,xl,xr]
        for k,v in zip(["yt","yb","xl","xr"],[yt,yb,xl,xr]):
            self._zvars[k].set(v)
        self.pipeline.set_subtitle_zone(yt,yb,xl,xr)
        self._redraw_zone()

    def _redraw_zone(self):
        self.canvas.delete("zone_r"); self.canvas.delete("zone_t")
        if self._drag_id:
            self.canvas.delete(self._drag_id); self._drag_id=None
        yt,yb,xl,xr=self._zone
        cx0,cy0=self._cp(xl,yt); cx1,cy1=self._cp(xr,yb)
        self.canvas.create_rectangle(cx0,cy0,cx1,cy1,
            outline="#58a6ff",width=2,dash=(8,4),tags="zone_r")
        mode_txt={"blur":"Blur","solid":"Đặc","prev_frame":"Frame Trước"}
        m=mode_txt.get(self.mode_var.get() if hasattr(self,"mode_var") else "blur","")
        self.canvas.create_text(cx0+6,cy0+6,
            text=f"COVER ZONE [{m}]  {int((xr-xl)*100)}%×{int((yb-yt)*100)}%",
            anchor="nw",font=("Helvetica",8,"bold"),
            fill="#58a6ff",tags="zone_t")
        self.zone_lbl.config(
            text=f"y:{yt:.0%}–{yb:.0%}  x:{xl:.0%}–{xr:.0%}")

    # ──────────────────── POLLING ─────────────────────────

    def _ui_loop(self):
        ready = (self.pipeline.model_yolo is not None and
                 self.pipeline.reader_ocr  is not None)
        if ready and not self._model_ready:
            self._model_ready = True
            self.badge.config(text="✅  Model sẵn sàng",
                              bg="#0d2b1d", fg="#3fb950")
            if self.video_path: self._enable_start()
            else: self.status.config(
                text="  ✅ Model xong. Chọn file video.", fg=self.GREEN)
        if not ready:
            self.badge.config(
                text=f"[{next(self._SPIN)}] Đang tải...",
                bg="#2d2600",fg="#e3b341")
        try:
            while not self.fq.empty():
                d=self.fq.get_nowait()
                if d["type"]=="frame": self._show_frame(d)
        except queue.Empty: pass
        try:
            while not self.rq.empty():
                self._handle(self.rq.get_nowait())
        except queue.Empty: pass
        self.root.after(25, self._ui_loop)

    def _enable_start(self):
        self.btn_start.config(state=tk.NORMAL,bg=self.GREEN,fg="white")
        self.btn_export.config(state=tk.NORMAL)
        self.status.config(
            text="  ✅ Kéo chuột chọn vùng → Bắt Đầu Xem Thử hoặc Xuất Video!", fg=self.GREEN)

    # ──────────────────── START / STOP / EXPORT ───────────────────

    def _setup_pipeline_run(self):
        for q in (self.rq, self.fq):
            while not q.empty():
                try: q.get_nowait()
                except: pass
        self._apply_zone()
        self._apply_mode()
        self.pipeline.blur_strength     = int(self.blur_var.get()) | 1
        self.pipeline.feather_px        = int(self.feather_var.get())
        self.pipeline.detect_threshold  = self.thr_var.get()
        self.pipeline.ocr_every_n_frames= self.nfr_var.get()

    def _start(self):
        if not self.video_path:
            messagebox.showwarning("Cảnh báo","Chưa chọn video!"); return
        if not self._model_ready:
            messagebox.showinfo("Thông báo","Model chưa xong!"); return
            
        self._setup_pipeline_run()
        self.is_running = True
        self.btn_start.config(state=tk.DISABLED,bg=self.CARD,fg="#666")
        self.btn_export.config(state=tk.DISABLED,bg=self.CARD,fg="#666")
        self.btn_stop.config(state=tk.NORMAL)
        
        mode_txt={"blur":"🌫️ Blur mờ","solid":"⬛ Màu đặc","prev_frame":"🎞️ Frame trước"}
        self.status.config(
            text=f"  Đang XEM THỬ [{mode_txt.get(self.mode_var.get(),'')}]...",
            fg="#58a6ff")
        self.prog.set(0)
        self.pipeline.start(self.video_path)
        
    def _export(self):
        if not self.video_path:
            messagebox.showwarning("Cảnh báo","Chưa chọn video!"); return
        if not self._model_ready:
            messagebox.showinfo("Thông báo","Model chưa xong!"); return
            
        out_path = filedialog.asksaveasfilename(
            title="Lưu video đã che",
            defaultextension=".mp4",
            filetypes=[("MP4 Video", "*.mp4")]
        )
        if not out_path: return
        
        self._setup_pipeline_run()
        self.is_running = True
        self.btn_start.config(state=tk.DISABLED,bg=self.CARD,fg="#666")
        self.btn_export.config(state=tk.DISABLED,bg=self.CARD,fg="#666")
        self.btn_stop.config(state=tk.NORMAL)
        
        self.status.config(
            text=f"  ⏳ Đang XUẤT VIDEO tốc độ cao... (Giữ nguyên chất lượng & FPS)",
            fg=self.GOLD)
        self.prog.set(0)
        # Truyền output_path để pipeline chạy chế độ Xuất video (không sleep)
        self.pipeline.start(self.video_path, output_path=out_path)

    def _stop(self):
        self.pipeline.stop()
        self.is_running = False
        self.btn_stop.config(state=tk.DISABLED)
        self._enable_start()
        self.status.config(text="  ⏹ Đã dừng.", fg=self.MUTED)

    # ──────────────────── DISPLAY ────────────────────────

    def _show_frame(self, d):
        frame = d.get("frame")
        if frame is not None and PIL_AVAILABLE:
            cw = max(1, self.canvas.winfo_width()  or 900)
            ch = max(1, self.canvas.winfo_height() or 560)
            tk_img, nw, nh = cv2_to_tk(frame, cw, ch)
            if tk_img:
                self._tk_img = tk_img
                x0=(cw-nw)//2; y0=(ch-nh)//2
                self._img_offset=(x0,y0); self._img_size=(nw,nh)
                self.canvas.delete("all")
                self.canvas.create_image(cw//2,ch//2,
                                         image=tk_img,anchor="center")
                self._redraw_zone()
        fn=d.get("frame_no",0); tot=d.get("total",1)
        self.fps_lbl.config(text=f"FPS: {d.get('fps',0):.1f}")
        self.frame_lbl.config(text=f"Frame: {fn} / {tot}")
        self.box_lbl.config(text=f"🎭  Vùng che: {d.get('boxes',0)}")
        self.prog.set((fn/tot*100) if tot else 0)

    def _handle(self, msg):
        t=msg.get("type")
        if t=="stats":
            self.box_lbl.config(text=f"🎭  Vùng che: {msg.get('count',0)}")
        elif t=="merging_audio":
            self.status.config(text="  🎵 Đang ghép âm thanh gốc vào video xuất...", fg=self.CYAN)
        elif t=="export_done":
            self.is_running=False
            self.btn_stop.config(state=tk.DISABLED)
            self._enable_start()
            self.status.config(text="  ✅ Xong!",fg=self.GREEN)
            self.prog.set(100)
            out_path = msg.get("path")
            
            # Show the dialog box
            response = messagebox.askyesno(
                "Xuất Video Thành Công", 
                f"Video đã được lưu tại:\n{out_path}\n\nBạn có muốn mở video ngay bây giờ không?"
            )
            
            if response:
                # Mở video trên macOS
                subprocess.run(["open", out_path])
                
        elif t=="done":
            self.is_running=False
            self.btn_stop.config(state=tk.DISABLED)
            self._enable_start()
            self.status.config(text="  ✅ Xong!",fg=self.GREEN)
            self.prog.set(100)
        elif t=="error":
            messagebox.showerror("Lỗi",msg.get("message",""))
            self._stop()

    def on_close(self):
        self.pipeline.stop(); self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = App(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()
