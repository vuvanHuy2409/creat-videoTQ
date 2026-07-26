#!/usr/bin/env python3
"""
Auto-Translade-Video — Giao diện Desktop Premium & Hiện đại
Cửa sổ điều khiển quy trình lồng tiếng Anh/Trung/Hàn -> Việt/Nhật
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import os, sys, json, shutil, subprocess, threading, queue, re
from pathlib import Path

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
PYTHON   = sys.executable
OUTPUT_DIR = BASE_DIR / "output" / "VN"

# ─── BẢNG MÀU PREMIUM (Light Slate Theme) ────────────────────────────
BG            = "#F8FAFC"  # Nền chính (Slate 50)
WHITE         = "#FFFFFF"  # Nền card/nội dung
SIDEBAR       = "#FFFFFF"  # Nền thanh bên
PRIMARY       = "#4F46E5"  # Màu chủ đạo (Indigo 600)
PRIMARY_HOVER = "#4338CA"  # Indigo 700 khi hover
PRIMARY_LIGHT = "#EEF2F6"  # Indigo nhạt cho active item
TEXT          = "#0F172A"  # Chữ chính (Slate 900)
MUTED         = "#64748B"  # Chữ phụ (Slate 500)
BORDER        = "#E2E8F0"  # Viền thanh phân cách (Slate 200)
CARD_BOR      = "#E2E8F0"  # Viền thẻ dự án

# Trạng thái dự án & Wizard
SUCCESS       = "#10B981"  # Xanh lục (Emerald 500)
SUCCESS_BG    = "#ECFDF5"  # Emerald 50
WARN          = "#F59E0B"  # Vàng (Amber 500)
WARN_BG       = "#FFFBEB"  # Amber 50
DANGER        = "#EF4444"  # Đỏ (Red 500)
DANGER_BG     = "#FEF2F2"  # Red 50
INFO_BG       = "#EFF6FF"  # Blue 50

# Phân mục nhật ký
LOG_BG        = "#0F172A"  # Slate 900
LOG_FG        = "#F1F5F9"  # Slate 100

STEP_IDLE     = "#F1F5F9"
STEP_ACT      = "#DBEAFE"
STEPS         = ["1. Thiết lập", "2. Chạy ASR", "3. Dịch Thuật", "4. Lồng Tiếng", "5. Hoàn tất"]

# Typography
FF  = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
FH1 = (FF, 20, "bold")
FH2 = (FF, 14, "bold")
FH3 = (FF, 11, "bold")
FB  = (FF, 10)
FS  = (FF, 9)
FM  = ("Courier New" if sys.platform == "win32" else "Menlo", 10)


def load_env():
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def save_env(new_data):
    existing_lines = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    written = set()
    out = []
    for line in existing_lines:
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k = s.split("=")[0].strip()
            if k in new_data:
                out.append(f"{k}={new_data[k]}")
                written.add(k)
            else:
                out.append(line)
        else:
            out.append(line)
    for k, v in new_data.items():
        if k not in written:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def extract_thumbnail(video_path, out_path):
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-ss", "00:00:01", "-frames:v", "1", "-q:v", "2", str(out_path)],
            capture_output=True, timeout=12)
        return out_path.exists()
    except Exception:
        return False


def scan_projects():
    projects = []
    
    # 1. Quét dự án tiếng Nhật (pipeline.py trực tiếp trong output/)
    jp_dir = BASE_DIR / "output"
    if jp_dir.exists():
        for folder in jp_dir.iterdir():
            if folder.is_dir() and folder.name != "VN" and not folder.name.startswith("."):
                proj = {"path": folder, "name": folder.name, "lang": "JP", "lang_text": "Tiếng Nhật (JP)"}
                set_status_and_files(proj, folder, "transcript_jp.json")
                projects.append(proj)

    # 2. Quét dự án tiếng Việt (pipeline_vi.py trong output/VN/)
    vi_dir = BASE_DIR / "output" / "VN"
    if vi_dir.exists():
        for folder in vi_dir.iterdir():
            if folder.is_dir() and not folder.name.startswith("."):
                proj = {"path": folder, "name": folder.name, "lang": "VI", "lang_text": "Tiếng Việt (VI)"}
                set_status_and_files(proj, folder, "transcript_vi.json")
                projects.append(proj)

    # Sắp xếp mtime mới nhất
    projects.sort(key=lambda p: p["path"].stat().st_mtime, reverse=True)
    return projects


def set_status_and_files(proj, folder, trans_name):
    if (folder / "dubbed_video.mp4").exists():
        proj["status"] = "done"
    elif (folder / trans_name).exists():
        proj["status"] = "tts"
    elif (folder / "TRANSLATE_PENDING.txt").exists():
        proj["status"] = "pending"
    elif (folder / "transcript_original.json").exists():
        proj["status"] = "asr_done"
    else:
        proj["status"] = "running"
        
    thumb = folder / "thumbnail.jpg"
    if not thumb.exists():
        for f in folder.iterdir():
            if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and not f.name.startswith("dubbed"):
                extract_thumbnail(f, thumb)
                break
    proj["thumbnail"] = thumb if thumb.exists() else None
    proj["dubbed_video"] = folder / "dubbed_video.mp4" if (folder / "dubbed_video.mp4").exists() else None


def flat_sep(parent, **kw):
    return tk.Frame(parent, bg=BORDER, height=1, **kw)


# ─── NÚT FLAT HIỆN ĐẠI ───────────────────────────────────────────────
class Btn(tk.Label):
    def __init__(self, parent, text, command=None, bg=PRIMARY, fg=WHITE, font=FH3, padx=18, pady=8, **kw):
        super().__init__(parent, text=text, bg=bg, fg=fg, font=font, padx=padx, pady=pady, cursor="hand2", **kw)
        self.config(relief="flat", highlightthickness=0)
        self._cmd = command
        self._bg  = bg
        self._hbg = self._dark(bg)
        
        self.bind("<Button-1>", lambda e: command() if command else None)
        self.bind("<Enter>",    lambda e: self.config(bg=self._hbg))
        self.bind("<Leave>",    lambda e: self.config(bg=self._bg))

    @staticmethod
    def _dark(h):
        try:
            r = max(0, int(h[1:3], 16) - 22)
            g = max(0, int(h[3:5], 16) - 22)
            b = max(0, int(h[5:7], 16) - 22)
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return h
# ─── KHUNG CUỘN THẨM MỸ ──────────────────────────────────────────────
class ScrollFrame:
    def __init__(self, parent, bg=BG):
        self.outer = tk.Frame(parent, bg=bg)
        self._c = tk.Canvas(self.outer, bg=bg, highlightthickness=0)
        self._sb = ttk.Scrollbar(self.outer, orient="vertical", command=self._c.yview)
        self._c.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side="right", fill="y")
        self._c.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self._c, bg=bg)
        self._w = self._c.create_window((0, 0), window=self.inner, anchor="nw")
        
        self.inner.bind("<Configure>", self._on_configure)
        self._c.bind("<Configure>", self._on_canvas_configure)
        self._c.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_configure(self, event):
        self._c.configure(scrollregion=self._c.bbox("all"))

    def _on_canvas_configure(self, event):
        self._c.itemconfig(self._w, width=event.width)

    def _on_mousewheel(self, event):
        if self.outer.winfo_exists():
            self._c.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def reset_scroll(self):
        self._c.yview_moveto(0)


# ─── APP CHÍNH ───────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Auto-Translade-Video Studio")
        self.geometry("1200x780")
        self.minsize(1000, 640)
        self.configure(bg=BG)
        
        # Style Combobox hiện đại
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("TCombobox", fieldbackground=WHITE, background=BG, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        
        self._pages = {}
        self._cur   = None
        self._build_layout()
        self._build_menu()
        self._show("projects")

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        
        sys_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Hệ thống", menu=sys_menu)
        sys_menu.add_command(label="Kiểm tra toàn trạng hệ thống...", command=self._test_system_status)
        
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Công cụ AI", menu=tools_menu)
        tools_menu.add_command(label="Kiểm tra kết nối Gemini API...", command=self._test_gemini_api)
        
    def _test_gemini_api(self):
        from config import GOOGLE_API_KEY, CONTENT_MODEL_ID
        if not GOOGLE_API_KEY:
            messagebox.showerror("Lỗi", "Không tìm thấy GOOGLE_API_KEY. Vui lòng vào 'Cài đặt Hệ thống' để thêm API Key của bạn.")
            return
        if not GOOGLE_API_KEY.startswith("AIza"):
            messagebox.showwarning("Cảnh báo định dạng", "Mã của bạn không bắt đầu bằng 'AIza' (tiêu chuẩn của Google API Key).\n\nCó vẻ như bạn đang dùng Token xác thực (OAuth/Session Token) thay vì API Key. Hệ thống vẫn sẽ thử kết nối, nhưng khả năng cao sẽ gặp lỗi 401 UNAUTHENTICATED.")
            
        def _run_test():
            try:
                from config import CONTENT_MODEL_ID
                
                # Cập nhật theo chuẩn mới: Truyền thẳng qua HTTP REST để tránh lỗi của SDK google-genai
                if GOOGLE_API_KEY.startswith("AQ."):
                    import urllib.request
                    import json
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONTENT_MODEL_ID}:generateContent"
                    data = {"contents": [{"parts": [{"text": "Hãy nói 'API hoạt động rất tốt!' bằng tiếng Việt."}]}]}
                    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'))
                    req.add_header("Content-Type", "application/json")
                    req.add_header("x-goog-api-key", GOOGLE_API_KEY)
                    
                    with urllib.request.urlopen(req, timeout=15) as res:
                        resp_data = json.loads(res.read().decode('utf-8'))
                        ai_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
                        self.after(0, lambda: messagebox.showinfo("Kiểm tra thành công", f"Kết nối Gemini API (Chuẩn mới) thành công!\n\nAI trả lời: {ai_text}"))
                else:
                    from google import genai
                    client = genai.Client(api_key=GOOGLE_API_KEY)
                    response = client.models.generate_content(
                        model=CONTENT_MODEL_ID,
                        contents="Hãy nói 'API hoạt động rất tốt!' bằng tiếng Việt.",
                    )
                    self.after(0, lambda: messagebox.showinfo("Kiểm tra thành công", f"Kết nối Gemini API thành công!\n\nAI trả lời: {response.text}"))
                    
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'read'):
                    err_msg += "\n" + e.read().decode('utf-8')
                self.after(0, lambda err=err_msg: messagebox.showerror("Lỗi kết nối", f"Không thể kết nối tới Gemini API.\nChi tiết lỗi:\n{err}"))
                
        # Hiển thị thông báo đang test
        messagebox.showinfo("Đang kiểm tra", "Đang gửi yêu cầu test đến hệ thống của Google... Vui lòng đợi trong giây lát.")
        threading.Thread(target=_run_test, daemon=True).start()

    def _test_system_status(self):
        def _run_checks():
            results = []
            
            # 1. Kiểm tra FFmpeg
            try:
                subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
                results.append("✅ FFmpeg: Đã cài đặt và sẵn sàng")
            except Exception:
                results.append("❌ FFmpeg: CHƯA CÀI ĐẶT hoặc lỗi đường dẫn (Cần thiết để xử lý video)")

            # 2. Kiểm tra Thư viện AI (Torch)
            try:
                import torch
                gpu = "Có GPU (MPS/CUDA)" if (torch.backends.mps.is_available() or torch.cuda.is_available()) else "Chỉ dùng CPU"
                results.append(f"✅ PyTorch AI: Sẵn sàng ({gpu})")
            except ImportError:
                results.append("❌ PyTorch AI: Lỗi (Chưa cài đặt thư viện 'torch')")

            # 3. Kiểm tra EasyOCR
            try:
                import easyocr
                results.append("✅ EasyOCR: Sẵn sàng (Dùng để xoá chữ AI)")
            except ImportError:
                results.append("❌ EasyOCR: Lỗi (Chưa cài đặt)")

            # 4. Kiểm tra API Keys cơ bản
            from config import GOOGLE_API_KEY, VIETNAMESE_API_KEY
            if GOOGLE_API_KEY:
                results.append("✅ Gemini API Key: Đã cấu hình")
            else:
                results.append("⚠️ Gemini API Key: Chưa cấu hình (Tính năng tự động dịch sẽ không hoạt động)")
                
            if VIETNAMESE_API_KEY:
                results.append("✅ LucyLab TTS API Key: Đã cấu hình")
            else:
                results.append("⚠️ LucyLab TTS API Key: Chưa cấu hình (Giọng đọc tiếng Việt có thể không hoạt động)")

            # Hiển thị kết quả
            msg = "\n\n".join(results)
            self.after(0, lambda: messagebox.showinfo("Kết quả kiểm tra hệ thống", f"Tình trạng các thành phần hệ thống:\n\n{msg}"))

        messagebox.showinfo("Đang kiểm tra", "Hệ thống đang quét các thành phần và thư viện AI... Quá trình này mất khoảng vài giây.")
        threading.Thread(target=_run_checks, daemon=True).start()


    def _build_layout(self):
        # Sidebar thanh bên cực đẹp
        self.sb_frame = tk.Frame(self, bg=SIDEBAR, width=240, highlightbackground=BORDER, highlightthickness=1)
        self.sb_frame.pack(side="left", fill="y")
        self.sb_frame.pack_propagate(False)
        
        # Logo header của sidebar
        logo_container = tk.Frame(self.sb_frame, bg=SIDEBAR, padx=24, pady=24)
        logo_container.pack(fill="x")
        tk.Label(logo_container, text="A U T O - D U B", font=(FF, 14, "bold"), bg=SIDEBAR, fg=PRIMARY, anchor="w").pack(fill="x")
        tk.Label(logo_container, text="Lồng tiếng & Dịch thuật Video", font=FS, bg=SIDEBAR, fg=MUTED, anchor="w").pack(fill="x", pady=(2, 0))
        
        flat_sep(self.sb_frame).pack(fill="x", padx=16)
        
        # Sidebar Items
        self._nav = {}
        self._stripes = {}
        menu_items = [
            ("projects", "Quản lý Dự án"),
            ("new", "Tạo Dự án Mới"),
            ("settings", "Cài đặt Hệ thống")
        ]
        
        menu_container = tk.Frame(self.sb_frame, bg=SIDEBAR, pady=16)
        menu_container.pack(fill="both", expand=True)
        
        for key, label in menu_items:
            # Container của từng dòng menu
            item_frame = tk.Frame(menu_container, bg=SIDEBAR, height=48, cursor="hand2")
            item_frame.pack(fill="x", pady=2)
            item_frame.pack_propagate(False)
            
            # Vạch màu bên trái khi active
            stripe = tk.Frame(item_frame, bg=SIDEBAR, width=4)
            stripe.pack(side="left", fill="y")
            self._stripes[key] = stripe
            
            # Nhãn chữ
            lbl = tk.Label(item_frame, text=label, font=FB, bg=SIDEBAR, fg=MUTED, anchor="w", padx=18)
            lbl.pack(side="left", fill="both", expand=True)
            self._nav[key] = lbl
            
            # Event binding
            for widget in (item_frame, lbl):
                widget.bind("<Button-1>", lambda e, k=key: self._show(k))
                widget.bind("<Enter>",    lambda e, k=key, f=item_frame: self._on_menu_enter(k, f))
                widget.bind("<Leave>",    lambda e, k=key, f=item_frame: self._on_menu_leave(k, f))
            
        self.content = tk.Frame(self, bg=BG)
        self.content.pack(side="left", fill="both", expand=True)

    def _on_menu_enter(self, key, frame):
        if key != self._cur:
            frame.config(bg=PRIMARY_LIGHT)
            self._nav[key].config(bg=PRIMARY_LIGHT)

    def _on_menu_leave(self, key, frame):
        if key != self._cur:
            frame.config(bg=SIDEBAR)
            self._nav[key].config(bg=SIDEBAR)

    def _show(self, key):
        if self._cur:
            if self._cur in self._pages:
                self._pages[self._cur].pack_forget()
            # Reset menu trước đó về trạng thái thường
            self._nav[self._cur].master.config(bg=SIDEBAR)
            self._nav[self._cur].config(bg=SIDEBAR, fg=MUTED, font=FB)
            self._stripes[self._cur].config(bg=SIDEBAR)
            
        self._cur = key
        # Active menu hiện tại
        self._nav[key].master.config(bg=PRIMARY_LIGHT)
        self._nav[key].config(bg=PRIMARY_LIGHT, fg=PRIMARY, font=(FF, 10, "bold"))
        self._stripes[key].config(bg=PRIMARY)
        
        if key not in self._pages:
            cls = {"projects": ProjectsPage, "new": NewProjectPage, "settings": SettingsPage}[key]
            self._pages[key] = cls(self.content, self)
            
        self._pages[key].pack(fill="both", expand=True)
        if hasattr(self._pages[key], "on_show"):
            self._pages[key].on_show()

    def reset_new(self):
        if "new" in self._pages:
            self._pages["new"].destroy()
            del self._pages["new"]
        self._show("new")

    def continue_project(self, proj):
        """Mở trang Tạo Dự án Mới và tải lại dữ liệu từ dự án chưa hoàn thành."""
        if "new" in self._pages:
            self._pages["new"].destroy()
            del self._pages["new"]
        self._show("new")
        self._pages["new"].load_project(proj)


# ══════════════════════════════════════════════════════
# TRANG: QUẢN LÝ DỰ ÁN
# ══════════════════════════════════════════════════════

class ProjectsPage(tk.Frame):
    STATUS_INFO = {
        "done":     ("Hoàn thành", SUCCESS, SUCCESS_BG),
        "tts":      ("Tổng hợp giọng", WARN, WARN_BG),
        "pending":  ("Chờ dịch JSON", PRIMARY, INFO_BG),
        "asr_done": ("Tách âm hoàn tất", PRIMARY, PRIMARY_LIGHT),
        "running":  ("Đang phân tích", WARN, WARN_BG),
        "unknown":  ("Không xác định", MUTED, STEP_IDLE),
    }

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._ph = {}
        self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=BG, padx=40, pady=24)
        hdr.pack(fill="x")
        
        tk.Label(hdr, text="Thư viện Dự án", font=FH1, bg=BG, fg=TEXT).pack(side="left")
        Btn(hdr, "Tạo Dự án Mới", command=self.app.reset_new, bg=PRIMARY).pack(side="right")
        
        flat_sep(self).pack(fill="x", padx=40)
        
        self._sf = ScrollFrame(self, bg=BG)
        self._sf.outer.pack(fill="both", expand=True, padx=30, pady=12)
        self._grid = self._sf.inner

    def on_show(self):
        self._refresh()

    def _refresh(self):
        for w in self._grid.winfo_children():
            w.destroy()
        self._ph.clear()
        
        projects = scan_projects()
        if not projects:
            empty_frame = tk.Frame(self._grid, bg=BG)
            empty_frame.pack(expand=True, fill="both", pady=80)
            tk.Label(empty_frame, text="Chưa có dự án nào.", font=FH2, bg=BG, fg=MUTED).pack()
            tk.Label(empty_frame, text="Nhấp vào nút 'Tạo Dự án Mới' để bắt đầu quy trình lồng tiếng.", font=FB, bg=BG, fg=MUTED, pady=8).pack()
            return
            
        COLS = 3
        for i, proj in enumerate(projects):
            row, col = divmod(i, COLS)
            card = self._make_card(self._grid, proj)
            card.grid(row=row, column=col, padx=12, pady=12, sticky="nsew")
            
        for c in range(COLS):
            self._grid.columnconfigure(c, weight=1)

    def _make_card(self, parent, proj):
        # Card chính với viền bo mượt mà và hiệu ứng Hover
        card = tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        
        st = proj.get("status", "unknown")
        
        # Ảnh thumbnail của video gốc — có thể click để xem preview
        tbg = tk.Frame(card, bg="#F1F5F9", height=155)
        tbg.pack(fill="x")
        tbg.pack_propagate(False)
        
        thumb_loaded = False
        if HAS_PIL and proj["thumbnail"] and proj["thumbnail"].exists():
            try:
                img = Image.open(proj["thumbnail"])
                img.thumbnail((360, 155), Image.Resampling.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                self._ph[str(proj["path"])] = ph
                thumb_lbl = tk.Label(tbg, image=ph, bg="#F1F5F9", cursor="hand2")
                thumb_lbl.pack(expand=True)
                thumb_loaded = True
            except Exception:
                pass
        
        if not thumb_loaded:
            thumb_lbl = tk.Label(tbg, text="📺", font=(FF, 32), bg="#F1F5F9", fg=MUTED, cursor="hand2")
            thumb_lbl.pack(expand=True)
        
        # Overlay nút Play trên thumbnail nếu có video để xem
        _video_to_preview = proj["dubbed_video"] or self._find_source_video(proj)
        if _video_to_preview:
            # Nút play overlay
            _play_bg       = "#1E293B" if thumb_loaded else "#CBD5E1"
            _play_bg_hover = "#0F172A" if thumb_loaded else "#94A3B8"
            play_overlay = tk.Label(tbg, text="▶", font=(FF, 24, "bold"),
                                    bg=_play_bg,
                                    fg=WHITE, cursor="hand2", padx=10, pady=6)
            play_overlay.place(relx=0.5, rely=0.5, anchor="center")
            play_overlay.bind("<Button-1>", lambda e, p=proj: self._preview_video(p))
            thumb_lbl.bind("<Button-1>", lambda e, p=proj: self._preview_video(p))
            play_overlay.bind("<Enter>",  lambda e, w=play_overlay, hbg=_play_bg_hover: w.config(bg=hbg))
            play_overlay.bind("<Leave>",  lambda e, w=play_overlay, nbg=_play_bg: w.config(bg=nbg))

        # Phần nội dung
        info = tk.Frame(card, bg=WHITE, padx=18, pady=14)
        info.pack(fill="x")
        
        # Tiêu đề dự án (Tên folder)
        tk.Label(info, text=proj["name"], font=FH2, bg=WHITE, fg=TEXT, anchor="w", wraplength=250).pack(fill="x")
        
        # Badges ngôn ngữ & Trạng thái nằm cạnh nhau
        badge_row = tk.Frame(info, bg=WHITE, pady=10)
        badge_row.pack(fill="x")
        
        # Nhãn ngôn ngữ
        tk.Label(badge_row, text=f" {proj['lang_text']} ", font=FS, bg="#F1F5F9", fg=PRIMARY, padx=6, pady=3).pack(side="left")
        
        # Nhãn trạng thái
        lbl, color, bbg = self.STATUS_INFO.get(st, ("Không rõ", MUTED, STEP_IDLE))
        tk.Label(badge_row, text=f" {lbl} ", font=FS, bg=bbg, fg=color, padx=6, pady=3).pack(side="left", padx=(8, 0))
        
        # Banner "Chưa hoàn thành" nếu dự án đang dở
        if st != "done":
            banner = tk.Frame(card, bg=WARN_BG, padx=18, pady=8)
            banner.pack(fill="x")
            tk.Label(banner, text="⚠️  Dự án chưa hoàn thành — nhấn Tiếp tục để hoàn thiện",
                     font=FS, bg=WARN_BG, fg="#92400E", anchor="w").pack(fill="x")
        
        # Dòng các nút hành động dưới đáy
        btn_row = tk.Frame(card, bg=WHITE, padx=18, pady=12)
        btn_row.pack(fill="x")
        
        flat_sep(card).pack(fill="x", before=btn_row)
        
        if proj["dubbed_video"]:
            # Nút Xem Video (chỉ có khi đã có video hoàn chỉnh)
            pv = tk.Label(btn_row, text="🎬 Xem", font=FS, bg=WHITE, fg="#7C3AED", cursor="hand2")
            pv.pack(side="left")
            pv.bind("<Button-1>", lambda e, p=proj: self._preview_video(p))
            pv.bind("<Enter>", lambda e, w=pv: w.config(fg="#5B21B6"))
            pv.bind("<Leave>", lambda e, w=pv: w.config(fg="#7C3AED"))
            
            dl = tk.Label(btn_row, text="⬇ Tải xuống", font=FS, bg=WHITE, fg=PRIMARY, cursor="hand2")
            dl.pack(side="left", padx=(14, 0))
            dl.bind("<Button-1>", lambda e, p=proj: self._download(p))
            dl.bind("<Enter>", lambda e, w=dl: w.config(fg=PRIMARY_HOVER))
            dl.bind("<Leave>", lambda e, w=dl: w.config(fg=PRIMARY))
        elif st != "done":
            # Nút Tiếp tục cho dự án chưa xong
            cont = tk.Label(btn_row, text="▶ Tiếp tục", font=(FS[0], FS[1], "bold"),
                            bg=WARN_BG, fg=WARN, cursor="hand2", padx=10, pady=4)
            cont.pack(side="left")
            cont.bind("<Button-1>", lambda e, p=proj: self.app.continue_project(p))
            cont.bind("<Enter>", lambda e, w=cont: w.config(bg="#FDE68A"))
            cont.bind("<Leave>", lambda e, w=cont: w.config(bg=WARN_BG))
            
        tk.Frame(btn_row, bg=WHITE).pack(side="left", expand=True)
        
        d = tk.Label(btn_row, text="🗑 Xoá", font=FS, bg=WHITE, fg=DANGER, cursor="hand2")
        d.pack(side="right")
        d.bind("<Button-1>", lambda e, p=proj: self._delete(p))
        
        # Tạo hiệu ứng đổi màu viền khi hover chuột vào Card
        card.bind("<Enter>", lambda e, c=card: c.config(highlightbackground=PRIMARY))
        card.bind("<Leave>", lambda e, c=card: c.config(highlightbackground=BORDER))
        
        return card

    def _find_source_video(self, proj):
        """Tìm file video nguồn trong thư mục dự án."""
        folder = proj["path"]
        for f in folder.iterdir():
            if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and not f.name.startswith("dubbed"):
                return f
        return None

    def _preview_video(self, proj):
        """Mở cửa sổ popup xem video trước khi tải."""
        video_path = proj["dubbed_video"] or self._find_source_video(proj)
        if not video_path or not Path(video_path).exists():
            messagebox.showwarning("Không tìm thấy video", "Không tìm thấy tệp video để phát.")
            return
        VideoPlayerDialog(self, proj, Path(video_path))

    def _delete(self, proj):
        if messagebox.askyesno("Xoá dự án", f"Bạn có chắc chắn muốn xoá dự án '{proj['name']}'?\nThao tác này sẽ xoá bỏ hoàn toàn các tệp tin lưu trên đĩa."):
            shutil.rmtree(proj["path"], ignore_errors=True)
            self._refresh()

    def _download(self, proj):
        dst_dir = filedialog.askdirectory(title="Chọn thư mục đích để lưu video lồng tiếng")
        if dst_dir:
            dst = Path(dst_dir) / f"{proj['name']}_dubbed.mp4"
            try:
                shutil.copy2(proj["dubbed_video"], dst)
                messagebox.showinfo("Tải thành công", f"Đã xuất video đã lồng tiếng ra:\n{dst}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể sao chép file: {e}")


# ══════════════════════════════════════════════════════
# DIALOG: XEM VIDEO TRƯỚC KHI TẢI
# ══════════════════════════════════════════════════════

class VideoPlayerDialog:
    """Cửa sổ popup xem video — hiển thị thông tin + nút mở bằng player hệ thống."""

    def __init__(self, parent, proj, video_path: Path):
        self.proj = proj
        self.video_path = video_path

        dlg = tk.Toplevel(parent)
        dlg.title(f"🎬 Xem Video — {proj['name']}")
        dlg.configure(bg=WHITE)
        dlg.transient(parent)
        dlg.grab_set()
        dlg.resizable(False, False)
        self.dlg = dlg

        # ── Header ──
        hdr = tk.Frame(dlg, bg=PRIMARY, padx=24, pady=16)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"🎬  {proj['name']}", font=FH2, bg=PRIMARY, fg=WHITE, anchor="w").pack(fill="x")
        tk.Label(hdr, text=proj.get("lang_text", ""), font=FS, bg=PRIMARY, fg="#C7D2FE", anchor="w").pack(fill="x")

        # ── Thumbnail preview lớn ──
        thumb_frame = tk.Frame(dlg, bg="#0F172A", width=560, height=315)
        thumb_frame.pack(fill="x")
        thumb_frame.pack_propagate(False)

        thumb_loaded = False
        if HAS_PIL and proj.get("thumbnail") and Path(proj["thumbnail"]).exists():
            try:
                img = Image.open(proj["thumbnail"])
                img.thumbnail((560, 315), Image.Resampling.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                dlg._thumb_ph = ph  # giữ tham chiếu
                thumb_lbl = tk.Label(thumb_frame, image=ph, bg="#0F172A")
                thumb_lbl.pack(expand=True)
                thumb_loaded = True
            except Exception:
                pass

        if not thumb_loaded:
            tk.Label(thumb_frame, text="📺", font=(FF, 48), bg="#0F172A", fg="#334155").pack(expand=True)

        # ── Thông tin video ──
        info_frame = tk.Frame(dlg, bg=WHITE, padx=24, pady=16)
        info_frame.pack(fill="x")

        # Lấy thông tin kích thước file và đường dẫn
        try:
            size_mb = video_path.stat().st_size / (1024 * 1024)
            size_str = f"{size_mb:.1f} MB"
        except Exception:
            size_str = "N/A"

        st = proj.get("status", "unknown")
        status_info = ProjectsPage.STATUS_INFO
        st_label, st_color, st_bg = status_info.get(st, ("Không rõ", MUTED, STEP_IDLE))

        info_rows = [
            ("📁 Tên dự án:",  proj["name"]),
            ("🎙 Ngôn ngữ:",   proj.get("lang_text", "N/A")),
            ("📊 Trạng thái:", st_label),
            ("💾 Kích thước:", size_str),
            ("📂 Đường dẫn:",  str(video_path)),
        ]
        for label_text, value_text in info_rows:
            row = tk.Frame(info_frame, bg=WHITE)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label_text, font=(FF, 10, "bold"), bg=WHITE, fg=MUTED, width=16, anchor="w").pack(side="left")
            tk.Label(row, text=value_text, font=FB, bg=WHITE, fg=TEXT, anchor="w", wraplength=380, justify="left").pack(side="left", fill="x", expand=True)

        # Trạng thái badge màu
        st_badge = tk.Frame(info_frame, bg=st_bg, padx=10, pady=4)
        st_badge.pack(anchor="w", pady=(6, 0))
        tk.Label(st_badge, text=f"  {st_label}  ", font=(FF, 9, "bold"), bg=st_bg, fg=st_color).pack()

        flat_sep(dlg).pack(fill="x", padx=24, pady=(8, 0))

        # ── Nút hành động ──
        btn_frame = tk.Frame(dlg, bg=WHITE, padx=24, pady=16)
        btn_frame.pack(fill="x")

        Btn(btn_frame, "▶  Mở bằng trình phát hệ thống",
            command=self._open_system_player,
            bg=PRIMARY, padx=16, pady=8).pack(side="left")

        Btn(btn_frame, "📂  Mở thư mục chứa",
            command=self._open_folder,
            bg="#475569", padx=16, pady=8).pack(side="left", padx=(10, 0))

        if proj["dubbed_video"]:
            Btn(btn_frame, "⬇  Tải xuống",
                command=lambda: (dlg.destroy(), self._download_from_dialog()),
                bg=SUCCESS, padx=16, pady=8).pack(side="left", padx=(10, 0))

        Btn(btn_frame, "Đóng", command=dlg.destroy,
            bg=BORDER, fg=TEXT, padx=16, pady=8).pack(side="right")

        # Căn giữa cửa sổ
        dlg.update_idletasks()
        sw = dlg.winfo_screenwidth()
        sh = dlg.winfo_screenheight()
        w  = dlg.winfo_width()
        h  = dlg.winfo_height()
        dlg.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _open_system_player(self):
        """Mở video bằng trình phát mặc định của hệ điều hành."""
        try:
            if sys.platform == "darwin":   # macOS
                subprocess.Popen(["open", str(self.video_path)])
            elif sys.platform == "win32":  # Windows
                os.startfile(str(self.video_path))
            else:                          # Linux
                subprocess.Popen(["xdg-open", str(self.video_path)])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể mở video:\n{e}")

    def _open_folder(self):
        """Mở thư mục chứa video."""
        try:
            folder = self.video_path.parent
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            elif sys.platform == "win32":
                subprocess.Popen(["explorer", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể mở thư mục:\n{e}")

    def _download_from_dialog(self):
        """Tải video xuống từ dialog."""
        dst_dir = filedialog.askdirectory(title="Chọn thư mục đích để lưu video lồng tiếng")
        if dst_dir:
            dst = Path(dst_dir) / f"{self.proj['name']}_dubbed.mp4"
            try:
                shutil.copy2(self.proj["dubbed_video"], dst)
                messagebox.showinfo("Tải thành công", f"Đã xuất video đã lồng tiếng ra:\n{dst}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể sao chép file: {e}")


# ══════════════════════════════════════════════════════
# TRANG: TẠO DỰ ÁN MỚI
# ══════════════════════════════════════════════════════

class NewProjectPage(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app       = app
        self._work_dir = None
        self._dubbed   = None
        self._full_json= ""
        self._q        = queue.Queue()
        self._polling  = False
        self.sub_size_var = tk.StringVar(value="40 (Khuyên dùng)")
        self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=BG, padx=40, pady=20)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Thiết lập Tiến trình Lồng tiếng", font=FH1, bg=BG, fg=TEXT).pack(side="left")
        
        # Thanh tiến trình Wizard
        sb = tk.Frame(self, bg=BG, padx=40)
        sb.pack(fill="x", pady=(0, 14))
        self._slbls = []
        for i, s in enumerate(STEPS):
            lbl = tk.Label(sb, text=s, font=FS, bg=STEP_IDLE, fg=MUTED, padx=14, pady=6)
            lbl.pack(side="left", padx=4)
            self._slbls.append(lbl)
            if i < len(STEPS) - 1:
                tk.Label(sb, text="➔", font=FS, bg=BG, fg=MUTED).pack(side="left", padx=2)
                
        flat_sep(self).pack(fill="x", padx=40)
        
        self._sf = ScrollFrame(self, bg=BG)
        self._sf.outer.pack(fill="both", expand=True)
        body = self._sf.inner
        
        self._p = [
            self._mk_p1(body),
            self._mk_p2(body),
            self._mk_p3(body),
            self._mk_p4(body),
            self._mk_p5(body),
        ]
        self._goto(0)

    def _card(self, parent, title=""):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=40, pady=8)
        frame = tk.Frame(wrap, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x")
        inner = tk.Frame(frame, bg=WHITE, padx=24, pady=18)
        inner.pack(fill="x")
        if title:
            tk.Label(inner, text=title, font=FH2, bg=WHITE, fg=TEXT).pack(anchor="w", pady=(0, 12))
        return inner, wrap

    def _mk_p1(self, body):
        panel = tk.Frame(body, bg=BG)
        
        # 1. URL
        c, _ = self._card(panel, "Đường dẫn URL Video (YouTube / TikTok / Douyin / Bilibili...)")
        self.url_var = tk.StringVar()
        entry_url = tk.Entry(c, textvariable=self.url_var, font=FB, relief="flat", highlightbackground=BORDER, highlightthickness=1, bg=WHITE, fg=TEXT)
        entry_url.pack(fill="x", ipady=8)
        
        # 2. File cục bộ
        c2, _ = self._card(panel, "Hoặc tải lên tệp tin Video cục bộ")
        self.file_var = tk.StringVar()
        fr = tk.Frame(c2, bg=WHITE)
        fr.pack(fill="x")
        entry_file = tk.Entry(fr, textvariable=self.file_var, font=FB, relief="flat", highlightbackground=BORDER, highlightthickness=1, bg=WHITE, fg=TEXT)
        entry_file.pack(side="left", fill="x", expand=True, ipady=7)
        
        pick = tk.Label(fr, text=" Chọn file ", font=FS, bg=BG, fg=TEXT, cursor="hand2", padx=12, pady=7, relief="flat", highlightbackground=BORDER, highlightthickness=1)
        pick.pack(side="right", padx=(10, 0))
        pick.bind("<Button-1>", lambda e: self._pick_file())
        
        # 3. Cấu hình giọng đọc & ngôn ngữ
        c3, _ = self._card(panel, "Thông số cấu hình lồng tiếng")
        og = tk.Frame(c3, bg=WHITE)
        og.pack(fill="x")
        
        self.target_lang_var = tk.StringVar(value="Tiếng Việt (VI)")
        self.lang_var        = tk.StringVar(value="zh")
        self.voice_var       = tk.StringVar(value="male")
        self.bg_var          = tk.StringVar(value="demucs")
        self.sub_y_var       = tk.StringVar(value="780")
        self.sub_size_var    = tk.StringVar(value="40 (Khuyên dùng)")
        
        # Ngôn ngữ nguồn
        tk.Label(og, text="Ngôn ngữ gốc của video", font=FS, bg=WHITE, fg=MUTED).grid(row=0, column=0, padx=(0, 24), sticky="w")
        ttk.Combobox(og, textvariable=self.lang_var, values=["zh", "en", "ja", "vi", "ko"], state="readonly", width=14).grid(row=1, column=0, padx=(0, 24), sticky="w", pady=(6, 0))
        
        # Ngôn ngữ đích
        tk.Label(og, text="Lồng tiếng dịch sang", font=FS, bg=WHITE, fg=MUTED).grid(row=0, column=1, padx=(0, 24), sticky="w")
        cb_target = ttk.Combobox(og, textvariable=self.target_lang_var, values=["Tiếng Việt (VI)", "Tiếng Nhật (JP)"], state="readonly", width=18)
        cb_target.grid(row=1, column=1, padx=(0, 24), sticky="w", pady=(6, 0))
        cb_target.bind("<<ComboboxSelected>>", self._on_target_lang_change)
        
        # Giọng đọc
        tk.Label(og, text="Giọng đọc (Voice)", font=FS, bg=WHITE, fg=MUTED).grid(row=0, column=2, padx=(0, 24), sticky="w")
        self.cb_voice = ttk.Combobox(og, textvariable=self.voice_var, values=["male", "female"], state="readonly", width=20)
        self.cb_voice.grid(row=1, column=2, padx=(0, 24), sticky="w", pady=(6, 0))
        
        # Nhạc nền BGM
        tk.Label(og, text="Xử lý Nhạc nền (BGM)", font=FS, bg=WHITE, fg=MUTED).grid(row=0, column=3, padx=(0, 24), sticky="w")
        ttk.Combobox(og, textvariable=self.bg_var, values=["demucs", "duck", "none"], state="readonly", width=14).grid(row=1, column=3, padx=(0, 24), sticky="w", pady=(6, 0))
        
        # Biến trạng thái ẩn (không hiển thị UI)
        self.burn_subtitles_var = tk.BooleanVar(value=True)
        self.ocr_replace_var = tk.BooleanVar(value=False)
        
        # Nút Bắt đầu
        bf = tk.Frame(panel, bg=BG)
        bf.pack(fill="x", padx=40, pady=(16, 24))
        Btn(bf, "Khởi chạy quy trình tách tiếng (ASR)", command=self._start_asr).pack(side="left")
        
        # Hiệu ứng focus cho entry
        for e in (entry_url, entry_file):
            e.bind("<FocusIn>", lambda ev, widget=e: widget.config(highlightbackground=PRIMARY))
            e.bind("<FocusOut>", lambda ev, widget=e: widget.config(highlightbackground=BORDER))
            
        return panel

    def _on_target_lang_change(self, event):
        target = self.target_lang_var.get()
        if "Việt" in target:
            self.cb_voice.config(values=["male", "female"])
            self.voice_var.set("male")
        else:
            self.cb_voice.config(values=["ja-JP-KeitaNeural", "ja-JP-NanamiNeural", "ja-JP-NaokiNeural", "ja-JP-MayuNeural"])
            self.voice_var.set("ja-JP-KeitaNeural")

    def _mk_p2(self, body):
        panel = tk.Frame(body, bg=BG)
        c, _ = self._card(panel, "Nhật ký quá trình phân tích âm thanh & nhận dạng giọng nói (ASR)")
        self._asr_log = scrolledtext.ScrolledText(c, font=FM, bg=LOG_BG, fg=LOG_FG, height=18, relief="flat", state="disabled", wrap="word")
        self._asr_log.pack(fill="x")
        self._asr_st = tk.Label(panel, text="", font=FB, bg=BG, fg=MUTED, pady=6)
        self._asr_st.pack()
        
        # Thêm cấu hình vị trí phụ đề vào Bước 2
        sub_card, _ = self._card(panel, "Cấu hình vị trí & cỡ chữ phụ đề (Có thể thiết lập trong lúc đợi)")
        
        cb_burn = tk.Checkbutton(sub_card, text="Vẽ cứng phụ đề lên video (Burn Subtitles)",
                                 variable=self.burn_subtitles_var, font=FB, bg=WHITE, fg=TEXT, activebackground=WHITE)
        cb_burn.pack(anchor="w", pady=(10, 0))
        
        # Vị trí Y đã được tự động hoá hoàn toàn dựa vào toạ độ gốc nên không cần hiển thị nữa.
        
        size_frame = tk.Frame(sub_card, bg=WHITE)
        size_frame.pack(anchor="w", pady=(10, 0))
        tk.Label(size_frame, text="Cỡ chữ phụ đề (Khuyên dùng):", font=FB, bg=WHITE, fg=TEXT).pack(side="left")
        self.sub_size_combo = ttk.Combobox(
            size_frame,
            textvariable=self.sub_size_var,
            values=["35 (Nhỏ)", "40 (Khuyên dùng)", "45 (Vừa)", "50 (To)", "55", "60", "65", "70 (Lớn nhất)"],
            state="readonly",
            width=16
        )
        self.sub_size_combo.pack(side="left", padx=6)
        
        # Nút quay lại thiết lập khi có lỗi
        self.asr_back_btn = Btn(panel, "Quay lại Thiết lập", command=lambda: self._goto(0), bg="#475569")
        self.asr_back_btn.pack(pady=10)
        return panel

    def _mk_p3(self, body):
        panel = tk.Frame(body, bg=BG)
        
        # Preview ban đầu
        c1, _ = self._card(panel, "File dịch thuật gốc (JSON transcript)")
        pw = tk.Frame(c1, bg="#F8FAFC", highlightbackground=BORDER, highlightthickness=1)
        pw.pack(fill="x")
        self._json_prev = tk.Label(pw, text="", font=FM, bg="#F8FAFC", fg=TEXT, anchor="nw", justify="left", wraplength=720, padx=12, pady=12)
        self._json_prev.pack(fill="x")
        
        br = tk.Frame(c1, bg=WHITE)
        br.pack(fill="x", pady=(10, 0))
        Btn(br, "Sao chép JSON gốc", command=self._copy_json, padx=14, pady=6).pack(side="left")
        self._copy_ok = tk.Label(br, text="", font=FS, bg=WHITE, fg=SUCCESS)
        self._copy_ok.pack(side="left", padx=12)
        
        # Paste b
        c2, _ = self._card(panel, "Dịch thuật (Tuỳ chọn: AI hoặc Thủ công)")
        
        gemini_frame = tk.Frame(c2, bg=WHITE)
        gemini_frame.pack(fill="x", pady=(0, 10))
        
        tk.Label(gemini_frame, text="Lựa chọn 1: Dịch tự động bằng AI", font=FB, bg=WHITE, fg="#4338CA").pack(anchor="w")
        tk.Label(gemini_frame, text="Sử dụng Gemini AI để dịch tự động, kết quả sẽ tự động điền vào ô bên dưới để bạn có thể xem lại trước khi chạy.", font=FS, bg=WHITE, fg=MUTED).pack(anchor="w", pady=(0, 4))
        self._btn_gemini = Btn(gemini_frame, "🤖 Tự động dịch bằng Gemini AI", command=self._translate_with_gemini, bg="#8B5CF6", fg=WHITE, padx=14, pady=6)
        self._btn_gemini.pack(anchor="w")
        
        tk.Label(c2, text="Lựa chọn 2: Dịch thủ công (Hoặc chỉnh sửa bản dịch AI)", font=FB, bg=WHITE, fg="#4338CA").pack(anchor="w", pady=(10, 4))
        
        self._trans_in = scrolledtext.ScrolledText(c2, font=FM, bg="#F8FAFC", fg=TEXT, height=12, relief="flat", wrap="word")
        self._trans_in.pack(fill="x")
        
        # Tự động bật Xoá phụ đề gốc bằng AI, không cần hỏi ở giao diện nữa
        self.ai_blur_var = tk.BooleanVar(value=True)
        

        bf = tk.Frame(panel, bg=BG)
        bf.pack(fill="x", padx=40, pady=(16, 24))
        Btn(bf, "Khởi chạy Tổng hợp giọng nói (TTS) & Render Video", command=self._start_tts).pack(side="left")
        return panel



    def _mk_p4(self, body):
        panel = tk.Frame(body, bg=BG)
        c, _ = self._card(panel, "Nhật ký quá trình lồng tiếng (TTS) & Mix nhạc nền")
        self._tts_log = scrolledtext.ScrolledText(c, font=FM, bg=LOG_BG, fg=LOG_FG, height=18, relief="flat", state="disabled", wrap="word")
        self._tts_log.pack(fill="x")
        self._tts_st = tk.Label(panel, text="", font=FB, bg=BG, fg=MUTED, pady=6)
        self._tts_st.pack()
        
        # Nút quay lại chỉnh sửa dịch thuật
        self.tts_back_btn = Btn(panel, "Quay lại Dịch thuật", command=lambda: self._goto(2), bg="#475569")
        self.tts_back_btn.pack(pady=10)
        return panel

    def _mk_p5(self, body):
        panel = tk.Frame(body, bg=BG)
        c, _ = self._card(panel, "Video của bạn đã sẵn sàng!")
        tk.Label(c, text="Quá trình lồng tiếng đã hoàn tất thành công!", font=FH1, bg=WHITE, fg=SUCCESS).pack(pady=(16, 4))
        
        self._done_lbl = tk.Label(c, text="", font=FM, bg=WHITE, fg=MUTED, wraplength=700)
        self._done_lbl.pack(pady=4)
        
        br = tk.Frame(c, bg=WHITE, pady=16)
        br.pack()
        Btn(br, "Xem video", command=self._play_video, bg="#3b82f6", padx=14).pack(side="left", padx=6)
        Btn(br, "Tải tệp Video xuống", command=self._download_video, padx=14).pack(side="left", padx=6)
        Btn(br, "Dự án mới", command=self.app.reset_new, bg="#475569", padx=14).pack(side="left", padx=6)
        Btn(br, "Về danh sách", command=lambda: self.app._show("projects"), bg=BORDER, fg=TEXT, padx=14).pack(side="left", padx=6)
        return panel

    def _goto(self, step):
        for p in self._p:
            p.pack_forget()
        self._p[step].pack(fill="x")
        self._sf.reset_scroll()
        
        # Cập nhật màu các pill step chỉ số
        for i, lbl in enumerate(self._slbls):
            if i < step:
                lbl.config(bg=SUCCESS_BG, fg=SUCCESS, font=(FF, 9, "bold"))
            elif i == step:
                lbl.config(bg=STEP_ACT, fg=PRIMARY, font=(FF, 9, "bold"))
            else:
                lbl.config(bg=STEP_IDLE, fg=MUTED, font=FS)

    def _pick_file(self):
        path = filedialog.askopenfilename(
            title="Chọn video đầu vào",
            filetypes=[("Video File", "*.mp4 *.mkv *.webm *.mov *.avi"), ("All Files", "*.*")])
        if path:
            self.file_var.set(path)

    def _start_asr(self):
        url    = self.url_var.get().strip()
        fpath  = self.file_var.get().strip()
        target = self.target_lang_var.get()
        
        if not url and not fpath:
            messagebox.showwarning("Nhập liệu trống", "Vui lòng dán link URL hoặc chọn file video từ máy tính.")
            return
            
        script_name = "pipeline_vi.py" if "Việt" in target else "pipeline.py"
        cmd = [PYTHON, str(BASE_DIR / script_name)]
        
        if url:   cmd += ["--url", url]
        if fpath: cmd += ["--file", fpath]
        
        cmd += ["--source-lang", self.lang_var.get(),
                "--voice",       self.voice_var.get(),
                "--bg-mode",     self.bg_var.get()]
        if self.burn_subtitles_var.get():
            cmd += ["--burn-subtitles"]
        if self.ocr_replace_var.get():
            cmd += ["--ocr-replace"]
            
        # Luôn bật tính năng AI xoá phụ đề gốc
        cmd += ["--ai-blur"]
                
        self._goto(1)
        self._wlog(self._asr_log, f"$ {' '.join(cmd)}\n\n")
        self._asr_st.config(text="Đang tải video, tách kênh âm thanh và nhận dạng giọng nói (ASR)...", fg=WARN)
        self._start_poll()

        def _run():
            try:
                env = {**os.environ, **load_env()}
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(BASE_DIR), env=env)
                found = None
                
                # Tìm đường dẫn tuyệt đối từ dòng log "Output folder: ..."
                # Bắt cả dạng "Output folder:" và "Resuming work directory:"
                re_abs = re.compile(r"(?:Output folder|Resuming work directory):\s*(.+)")
                # Fallback: tìm theo pattern tương đối như trước
                re_pattern = r"output[/\\]VN[/\\]([\w]+_vi)" if "Việt" in target else r"output[/\\]([\d]+)\b"
                target_output_dir = OUTPUT_DIR if "Việt" in target else (BASE_DIR / "output")
                
                for line in proc.stdout:
                    self._q.put(("log_asr", line))
                    # Ưu tiên parse đường dẫn tuyệt đối
                    if found is None:
                        m_abs = re_abs.search(line)
                        if m_abs:
                            cand = Path(m_abs.group(1).strip())
                            if cand.is_dir():
                                found = cand
                    # Fallback regex tương đối
                    if found is None:
                        m = re.search(re_pattern, line)
                        if m:
                            cand = target_output_dir / m.group(1)
                            if cand.is_dir():
                                found = cand
                proc.wait()
                
                # Exit code != 0 chỉ báo lỗi thực sự (không phải translate_pending)
                if proc.returncode != 0:
                    self._q.put(("error", f"Quy trình ASR thất bại với mã lỗi (Exit code={proc.returncode}). Vui lòng xem log chi tiết."))
                    return
                
                if found is None and target_output_dir.exists():
                    dirs = sorted(target_output_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
                    dirs = [d for d in dirs if d.is_dir() and d.name != "VN" and not d.name.startswith(".")]
                    if dirs:
                        found = dirs[0]
                        
                self._q.put(("asr_done", found))
            except Exception as ex:
                self._q.put(("error", str(ex)))

        threading.Thread(target=_run, daemon=True).start()


    def _start_tts(self):
        raw = self._trans_in.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng nhập mã JSON đã được dịch vào khung văn bản.")
            return
            
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            messagebox.showerror("Lỗi định dạng", f"Văn bản dịch không phải định dạng JSON hợp lệ:\n{e}")
            return
            
        if not self._work_dir:
            messagebox.showerror("Lỗi", "Không tìm thấy thư mục làm việc của dự án ban đầu.")
            return
            
        target = self.target_lang_var.get()
        is_vi = "Việt" in target
        
        # Ghi file dịch
        trans_file_name = "transcript_vi.json" if is_vi else "transcript_jp.json"
        vi_path = self._work_dir / trans_file_name
        vi_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        
        # Tìm video nguồn
        src = self.file_var.get().strip()
        if not src:
            for f in self._work_dir.iterdir():
                if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and not f.name.startswith("dubbed"):
                    src = str(f)
                    break
                    
        script_name = "pipeline_vi.py" if is_vi else "pipeline.py"
        cmd = [PYTHON, str(BASE_DIR / script_name), "--resume", str(self._work_dir), "--voice", self.voice_var.get()]
        if src:
            cmd += ["--file", src]
        if self.burn_subtitles_var.get():
            cmd += ["--burn-subtitles"]
        if self.ocr_replace_var.get():
            cmd += ["--ocr-replace"]
        cmd += ["--subtitle-y", self.sub_y_var.get()]
        # Trích xuất số cỡ chữ từ chuỗi (ví dụ "38 (Rất to)" -> "38")
        sub_size_str = self.sub_size_var.get().split()[0]
        cmd += ["--subtitle-size", sub_size_str]
        # Truyền cờ AI blur nếu được bật
        if self.ai_blur_var.get():
            cmd += ["--ai-blur"]
            
        self._goto(3)
        self._wlog(self._tts_log, f"$ {' '.join(cmd)}\n\n")
        self._tts_st.config(text="Đang tổng hợp giọng nói AI và kết ghép nhạc nền...", fg=WARN)
        self._start_poll()

        def _run():
            try:
                env = {**os.environ, **load_env()}
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(BASE_DIR), env=env)
                for line in proc.stdout:
                    self._q.put(("log_tts", line))
                proc.wait()
                
                dubbed = self._work_dir / "dubbed_video.mp4"
                if proc.returncode == 0 and dubbed.exists() and dubbed.stat().st_size > 0:
                    self._q.put(("tts_done", dubbed))
                else:
                    self._q.put(("error", f"Đã xảy ra lỗi khi tạo video (Exit code={proc.returncode}). Tệp video đầu ra không hợp lệ hoặc trống."))
            except Exception as ex:
                self._q.put(("error", str(ex)))

        threading.Thread(target=_run, daemon=True).start()

    def _open_measure_dialog(self):
        """Mở hộp thoại chọn vị trí Y phụ đề, tự động lấy ảnh ngẫu nhiên."""
        video_path = None
        if self._work_dir and self._work_dir.exists():
            for f in self._work_dir.iterdir():
                if f.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov", ".avi") and not f.name.startswith("dubbed"):
                    video_path = f
                    break
        if not video_path and self.file_var.get().strip():
            candidate = Path(self.file_var.get().strip())
            if candidate.exists():
                video_path = candidate
        if not video_path:
            messagebox.showerror("Lỗi", "Không tìm thấy tệp video nguồn để đo vị trí.")
            return

        import cv2
        import random
        from PIL import Image as PILImage, ImageTk as PILImageTk, ImageDraw

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            messagebox.showerror("Lỗi", f"Không thể mở video: {video_path}")
            return
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        orig_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        orig_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        # Thu nhỏ vừa màn hình
        max_w, max_h = 1000, 560
        scale = min(max_w / orig_w, max_h / orig_h, 1.0)
        disp_w = int(orig_w * scale)
        disp_h = int(orig_h * scale)

        dialog = tk.Toplevel(self)
        dialog.title("📐 Kéo chọn vị trí đặt phụ đề (Ảnh ngẫu nhiên)")
        dialog.configure(bg=WHITE)
        dialog.transient(self)
        dialog.grab_set()

        def on_close():
            cap.release()
            dialog.destroy()
        dialog.protocol("WM_DELETE_WINDOW", on_close)
        hdr = tk.Frame(dialog, bg=PRIMARY, padx=20, pady=8)
        hdr.pack(fill="x")
        tk.Label(hdr,
                 text="⇑ Nhấn giữ + Kéo chuột trên video để chọn dải ngang (Y) đặt phụ đề. Bấm '🔄 Đổi ảnh ngẫu nhiên khác' nếu ảnh hiện tại không có chữ.",
                 font=FB, bg=PRIMARY, fg=WHITE).pack(anchor="w")

        canvas = tk.Canvas(dialog, width=disp_w, height=disp_h,
                           highlightthickness=0, cursor="sb_v_double_arrow", bg="#0F172A")
        canvas.pack()

        # Chọn frame ngẫu nhiên ban đầu từ 5% đến 95%
        start_f = int(total_frames * 0.05)
        end_f = int(total_frames * 0.95)
        if start_f >= end_f:
            start_f = 0
            end_f = max(0, total_frames - 1)
        init_frame = random.randint(start_f, end_f) if end_f > start_f else start_f

        state = {
            "frame_idx": init_frame,
            "y0": None, "y1": None,
            "rect_band": None, "line_top": None,
            "line_bot": None,
            "label_id": None,
            "tk_img": None,
            "disp_img": None
        }

        drag = {"y0": None, "y1": None}

        status_var = tk.StringVar(value="— Kéo chuột để vẽ dải ngang chọn vị trí phụ đề —")
        status_lbl = tk.Label(dialog, textvariable=status_var, font=FB, bg=WHITE, fg="#4338CA", pady=8)
        status_lbl.pack(fill="x", padx=20)

        def _to_orig_y(dy): return int(dy / scale)
        def _clamp(v, lo, hi): return max(lo, min(hi, v))

        def update_canvas_image(y0, y1):
            if state["disp_img"] is None:
                return
            temp_img = state["disp_img"].copy()
            if y0 is not None and y1 is not None:
                top = min(y0, y1)
                bot = max(y0, y1)
                if (bot - top) > 2:
                    overlay = PILImage.new("RGBA", temp_img.size, (0, 0, 0, 0))
                    draw = ImageDraw.Draw(overlay)
                    draw.rectangle([0, top, disp_w, bot], fill=(37, 99, 235, 80))
                    temp_img = PILImage.alpha_composite(temp_img.convert("RGBA"), overlay)
            
            state["tk_img"] = PILImageTk.PhotoImage(temp_img)
            canvas.delete("bg_img")
            canvas.create_image(0, 0, anchor="nw", image=state["tk_img"], tags="bg_img")
            canvas.image = state["tk_img"]
            canvas.tag_lower("bg_img")

        def load_and_render_frame():
            cap.set(cv2.CAP_PROP_POS_FRAMES, state["frame_idx"])
            ret, frame = cap.read()
            if not ret:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
            if not ret: return
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            disp_img = PILImage.fromarray(frame_rgb).resize((disp_w, disp_h), PILImage.Resampling.LANCZOS)
            state["disp_img"] = disp_img
            
            if state["y0"] is not None and state["y1"] is not None:
                update_canvas_image(state["y0"], state["y1"])
            else:
                update_canvas_image(None, None)
            draw_selection()

        def draw_selection():
            for key in ("rect_band", "line_top", "line_bot", "label_id"):
                if state[key]:
                    canvas.delete(state[key])
                    state[key] = None
            if state["y0"] is not None and state["y1"] is not None:
                top = min(state["y0"], state["y1"])
                bot = max(state["y0"], state["y1"])
                update_canvas_image(top, bot)
                state["line_top"] = canvas.create_line(0, top, disp_w, top, fill="#FBBF24", width=2)
                state["line_bot"] = canvas.create_line(0, bot, disp_w, bot, fill="#FDE68A", width=1, dash=(6, 3))
                oy0 = _to_orig_y(top)
                state["label_id"] = canvas.create_text(disp_w // 2, top - 10 if top > 20 else bot + 10, text=f" Y = {oy0} px ", fill="white", font=(FF, 10, "bold"))
            else:
                update_canvas_image(None, None)

        def on_press(e):
            drag["y0"] = e.y
            drag["y1"] = e.y
            state["y0"] = None
            state["y1"] = None
            update_canvas_image(None, None)
            draw_selection()

        def on_drag(e):
            if drag["y0"] is None: return
            y1 = _clamp(e.y, 0, disp_h)
            drag["y1"] = y1
            
            for key in ("rect_band", "line_top", "line_bot", "label_id"):
                if state[key]: canvas.delete(state[key])
            
            top = min(drag["y0"], y1)
            bot = max(drag["y0"], y1)
            update_canvas_image(top, bot)
            state["line_top"] = canvas.create_line(0, top, disp_w, top, fill="#2563EB", width=2, dash=(8, 4))
            state["line_bot"] = canvas.create_line(0, bot, disp_w, bot, fill="#93C5FD", width=1, dash=(4, 4))
            oy0 = _to_orig_y(top)
            oy1 = _to_orig_y(bot)
            status_var.set(f"➤  Dải phụ đề: Y bắt đầu = {oy0}px | Y kết thúc = {oy1}px | Cao = {oy1-oy0}px")

        def on_release(e):
            if drag["y0"] is None: return
            y1 = _clamp(e.y, 0, disp_h)
            state["y0"] = drag["y0"]
            state["y1"] = y1
            draw_selection()
            oy0 = _to_orig_y(min(drag["y0"], y1))
            oy1 = _to_orig_y(max(drag["y0"], y1))
            status_var.set(f"✓ Đã chọn dải: Y = {oy0}px — {oy1}px | Cao = {oy1-oy0}px | Nhấn Xác nhận để lưu")

        canvas.bind("<ButtonPress-1>",   on_press)
        canvas.bind("<B1-Motion>",        on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        ft = tk.Frame(dialog, bg=WHITE, pady=12)
        ft.pack(fill="x", padx=20)

        def save_and_close():
            if state["y0"] is None or state["y1"] is None:
                messagebox.showwarning("Chưa chọn", "Vui lòng kéo chọn dải ngang trước.")
                return
            top_y = _to_orig_y(min(state["y0"], state["y1"]))
            self.sub_y_var.set(str(top_y))
            dialog.destroy()
            cap.release()

        def randomize_frame():
            state["frame_idx"] = random.randint(start_f, end_f) if end_f > start_f else start_f
            load_and_render_frame()

        Btn(ft, "✓  Xác nhận & Lưu vị trí", command=save_and_close, bg=PRIMARY, padx=16, pady=8).pack(side="left")
        Btn(ft, "🔄  Đổi ảnh ngẫu nhiên khác", command=randomize_frame, bg="#2563EB", fg=WHITE, padx=12, pady=8).pack(side="left", padx=(10, 0))
        Btn(ft, "Hủy bộ", command=dialog.destroy, bg="#e2e8f0", fg=TEXT, padx=14, pady=8).pack(side="right")

        load_and_render_frame()

        dialog.update_idletasks()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

    def _start_poll(self):
        if not self._polling:
            self._polling = True
            self._poll()

    def _poll(self):
        stop = False
        try:
            while True:
                tag, data = self._q.get_nowait()
                if   tag == "log_asr":  self._wlog(self._asr_log, data)
                elif tag == "log_tts":  self._wlog(self._tts_log, data)
                elif tag == "asr_done":
                    self._work_dir = data
                    self._asr_st.config(text="ASR nhận diện hoàn tất!", fg=SUCCESS)
                    self._prep_translate()
                    stop = True
                elif tag == "tts_done":
                    self._dubbed = data
                    self._tts_st.config(text="Đã hoàn tất kết xuất video!", fg=SUCCESS)
                    self._go_done()
                    stop = True
                elif tag == "error":
                    self._asr_st.config(text=f"Có lỗi: {data}", fg=DANGER)
                    self._tts_st.config(text=f"Có lỗi: {data}", fg=DANGER)
                    messagebox.showerror("Lỗi Tiến trình", f"Lỗi xảy ra trong quá trình thực thi:\n\n{data}")
                    stop = True
        except queue.Empty:
            pass
            
        if stop:
            self._polling = False
        else:
            self.after(100, self._poll)

    def _prep_translate(self):
        """Đọc file JSON gốc, điền vào ô hiển thị, rồi chuyển sang Bước 3 (Dịch thuật)."""
        orig = self._work_dir / "transcript_original.json" if self._work_dir else None
        if orig and orig.exists():
            try:
                items = json.loads(orig.read_text(encoding="utf-8"))
                self._full_json = json.dumps(items, ensure_ascii=False, indent=2)
                preview  = items[:3] if isinstance(items, list) else list(items.values())[:3]
                total    = len(items) if isinstance(items, (list, dict)) else "?"
                prev_txt = json.dumps(preview, ensure_ascii=False, indent=2)
                self._json_prev.config(text=f"{prev_txt}\n... ({total} phân đoạn âm thanh được nhận diện)")
            except Exception as e:
                self._full_json = ""
                self._json_prev.config(text=f"Lỗi phân tích JSON gốc: {e}")
        else:
            self._full_json = ""
            self._json_prev.config(text="Không tìm thấy file kết quả transcript_original.json")

        # Tự động tải bản dịch đã có (nếu có) vào ô nhập liệu
        self._trans_in.delete("1.0", "end")
        target = self.target_lang_var.get()
        is_vi = "Việt" in target
        trans_file_name = "transcript_vi.json" if is_vi else "transcript_jp.json"
        trans_path = self._work_dir / trans_file_name if self._work_dir else None
        if trans_path and trans_path.exists():
            try:
                trans_items = json.loads(trans_path.read_text(encoding="utf-8"))
                trans_json_str = json.dumps(trans_items, ensure_ascii=False, indent=2)
                self._trans_in.insert("1.0", trans_json_str)
            except Exception:
                pass
        # Đảm bảo giọng đọc khớp ngôn ngữ đích
        if "Việt" in target:
            if self.voice_var.get() not in ["male", "female"]:
                self.voice_var.set("male")
        else:
            if not self.voice_var.get().startswith("ja-JP"):
                self.voice_var.set("ja-JP-KeitaNeural")

        # Tự động dịch và tiếp tục sang bước TTS lồng tiếng tự động
        self._goto(2)
        if not (trans_path and trans_path.exists()):
            self._translate_with_gemini(auto_start_tts=True)
        else:
            self._start_tts()

    def load_project(self, proj):
        """Tải dữ liệu từ dự án chưa hoàn thành để tiếp tục từ bước dịch thuật."""
        self._work_dir = proj["path"]
        # Đặt ngôn ngữ đích đúng
        if proj["lang"] == "VI":
            self.target_lang_var.set("Tiếng Việt (VI)")
        else:
            self.target_lang_var.set("Tiếng Nhật (JP)")
        # Gọi hàm chuẩn để điền dữ liệu và chuyển trang
        self._prep_translate()

    def _copy_json(self):
        if not self._full_json:
            messagebox.showwarning("Trống dữ liệu", "Không tìm thấy nội dung JSON nguồn.")
            return
        self.clipboard_clear()
        self.clipboard_append(self._full_json)
        self._copy_ok.config(text="Đã sao chép vào Clipboard!")
        self.after(2000, lambda: self._copy_ok.config(text=""))

    def _translate_with_gemini(self, auto_start_tts=False):
        if not self._full_json:
            messagebox.showwarning("Trống dữ liệu", "Không tìm thấy nội dung JSON nguồn.")
            return
            
        target = self.target_lang_var.get()
        target_lang = "Vietnamese" if "Việt" in target else "Japanese"
        
        self._btn_gemini.config(text="Đang dịch... (Vui lòng đợi)", state="disabled", bg="#9CA3AF")
        
        def _run_translation():
            try:
                from src.translator import translate_json_openai
                translated_json = translate_json_openai(self._full_json, target_lang)
                
                # Update UI in main thread
                def _update_ui():
                    self._trans_in.delete("1.0", "end")
                    self._trans_in.insert("1.0", translated_json)
                    self._btn_gemini.config(text="🤖 Tự động dịch bằng OpenAI AI", state="normal", bg="#8B5CF6")
                    if auto_start_tts:
                        self._start_tts()
                    else:
                        messagebox.showinfo("Thành công", "Đã dịch xong bằng OpenAI AI!")
                    
                self.after(0, _update_ui)
                
            except Exception as e:
                err_msg = str(e)
                def _show_err():
                    self._btn_gemini.config(text="🤖 Tự động dịch bằng OpenAI AI", state="normal", bg="#8B5CF6")
                    if auto_start_tts:
                        messagebox.showerror("Lỗi dịch AI", f"Không thể tự động dịch bằng OpenAI. Vui lòng thử lại hoặc dịch thủ công:\n\n{err_msg}")
                    else:
                        messagebox.showerror("Lỗi dịch AI", f"Không thể dịch bằng OpenAI:\n\n{err_msg}")
                self.after(0, _show_err)

        threading.Thread(target=_run_translation, daemon=True).start()

    def _go_done(self):
        self._done_lbl.config(text=str(self._dubbed) if self._dubbed else "")
        if self._work_dir and self._dubbed:
            thumb = self._work_dir / "thumbnail.jpg"
            if not thumb.exists():
                extract_thumbnail(self._dubbed, thumb)
        self._goto(4)
        
        # Phát âm thanh thông báo "ting" (chỉ chạy trên macOS)
        try:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        except Exception:
            pass

    def _play_video(self):
        if not self._dubbed or not self._dubbed.exists():
            messagebox.showerror("Lỗi", "Không tìm thấy video đích đã xử lý.")
            return
        try:
            subprocess.Popen(["open", str(self._dubbed)])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể mở video:\n{e}")

    def _download_video(self):
        if not self._dubbed or not self._dubbed.exists():
            messagebox.showerror("Lỗi", "Không tìm thấy video đích đã xử lý.")
            return
        dst_dir = filedialog.askdirectory(title="Chọn thư mục lưu video của bạn")
        if dst_dir:
            name = (self._work_dir.name if self._work_dir else "output") + "_dubbed.mp4"
            dst  = Path(dst_dir) / name
            try:
                shutil.copy2(self._dubbed, dst)
                messagebox.showinfo("Đã lưu", f"Video đã được lưu thành công vào:\n{dst}")
            except Exception as e:
                messagebox.showerror("Thất bại", f"Không thể lưu video: {e}")

    @staticmethod
    def _wlog(w, text):
        w.config(state="normal")
        w.insert("end", text)
        w.see("end")
        w.config(state="disabled")


# ══════════════════════════════════════════════════════
# TRANG: CẤU HÌNH HỆ THỐNG
# ══════════════════════════════════════════════════════

class SettingsPage(tk.Frame):
    _GROUPS = [
        ("Azure Speech API (Cần thiết cho ASR & TTS tiếng Nhật)", [
            ("AZURE_SPEECH_KEY",    "Speech API Key",  True),
            ("AZURE_SPEECH_REGION", "Region của dịch vụ", False),
        ]),
        ("LucyLab API — Tiếng Việt (Chỉ dùng nếu lồng tiếng Việt)", [
            ("VIETNAMESE_API_KEY",        "LucyLab API Key", True),
            ("VIETNAMESE_VOICEID_MALE",   "Mã giọng Nam (Male Voice ID)", False),
            ("VIETNAMESE_VOICEID_FEMALE", "Mã giọng Nữ (Female Voice ID)", False),
            ("LUCYLAB_API_URL",           "Đường dẫn LucyLab API URL", False),
            ("VIETNAMESE_TTS_MAX_SPEED",  "Tốc độ tối đa của TTS Việt", False),
        ]),
        ("OpenAI API (Dùng dịch thuật tự động)", [
            ("OPENAI_API_KEY", "OpenAI API Key", True),
            ("OPENAI_MODEL_ID", "Model OpenAI (vd: gpt-4o-mini)", False),
        ]),
        ("Google Gemini API (Tuỳ chọn — Tạo mô tả & ảnh thu nhỏ)", [
            ("GOOGLE_API_KEY", "Gemini API Key", True),
            ("IMAGE_MODEL_ID", "Model tạo ảnh (Image Model)", False),
            ("CONTENT_MODEL_ID", "Model văn bản (Content Model)", False),
        ]),

        ("Cấu hình xử lý tệp & thông số chung", [
            ("DEFAULT_SOURCE_LANG", "Mã ngôn ngữ nguồn mặc định", False),
            ("AUDIO_SAMPLE_RATE",   "Tần số lấy mẫu âm thanh (Hz)", False),
            ("AUDIO_SLOW_FACTOR",   "Tỉ lệ kéo dài/làm chậm âm thanh gốc", False),
            ("TTS_VOICE",           "Tên giọng đọc tiếng Nhật mặc định", False),
            ("TTS_MAX_SPEED_RATIO", "Giới hạn tốc độ tăng tối đa", False),
            ("OUTPUT_DIR",          "Thư mục chứa đầu ra mặc định", False),
        ]),
    ]

    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app   = app
        self._vars = {}
        self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=BG, padx=40, pady=24)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Cấu hình hệ thống (.env)", font=FH1, bg=BG, fg=TEXT).pack(side="left")
        Btn(hdr, "Lưu cấu hình", command=self._save, bg=PRIMARY).pack(side="right")
        
        flat_sep(self).pack(fill="x", padx=40)
        
        sf = ScrollFrame(self, bg=BG)
        sf.outer.pack(fill="both", expand=True)
        body = sf.inner
        
        env  = load_env()
        for gname, fields in self._GROUPS:
            wrap = tk.Frame(body, bg=BG)
            wrap.pack(fill="x", padx=40, pady=8)
            card = tk.Frame(wrap, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x")
            inner = tk.Frame(card, bg=WHITE, padx=24, pady=18)
            inner.pack(fill="x")
            
            tk.Label(inner, text=gname, font=FH2, bg=WHITE, fg=TEXT).pack(anchor="w", pady=(0, 14))
            for key, label, secret in fields:
                row = tk.Frame(inner, bg=WHITE)
                row.pack(fill="x", pady=6)
                tk.Label(row, text=label, font=FB, bg=WHITE, fg=MUTED, width=32, anchor="w").pack(side="left")
                
                var = tk.StringVar(value=env.get(key, ""))
                self._vars[key] = var
                
                entry = tk.Entry(row, textvariable=var, font=FB, relief="flat", highlightbackground=BORDER, highlightthickness=1, bg=WHITE, fg=TEXT)
                entry.pack(side="left", fill="x", expand=True, ipady=7, padx=(8, 0))
                
                # Hiệu ứng focus viền entry
                entry.bind("<FocusIn>", lambda ev, widget=entry: widget.config(highlightbackground=PRIMARY))
                entry.bind("<FocusOut>", lambda ev, widget=entry: widget.config(highlightbackground=BORDER))
                
                if secret:
                    entry.config(show="*")
                    sv = tk.BooleanVar(value=False)
                    def _toggle(e=entry, s=sv):
                        s.set(not s.get())
                        e.config(show="" if s.get() else "*")
                    tog = tk.Label(row, text="Hiện", font=FS, bg=WHITE, fg=PRIMARY, cursor="hand2", padx=10)
                    tog.pack(side="left")
                    tog.bind("<Button-1>", lambda ev, t=_toggle: t())
                    
        bot = tk.Frame(body, bg=BG)
        bot.pack(fill="x", padx=40, pady=(16, 24))
        Btn(bot, "Lưu cấu hình (.env)", command=self._save, padx=20, pady=10).pack(side="left")
        self._saved_lbl = tk.Label(bot, text="", font=FB, bg=BG, fg=SUCCESS)
        self._saved_lbl.pack(side="left", padx=14)

    def _test_gemini(self):
        api_key = self._vars.get("GOOGLE_API_KEY").get().strip()
        if not api_key:
            messagebox.showerror("Lỗi", "Vui lòng nhập Gemini API Key vào ô trống trước khi thử kết nối.")
            return
        if not api_key.startswith("AIza"):
            messagebox.showwarning("Cảnh báo định dạng", "Mã của bạn không bắt đầu bằng 'AIza'.\n\nĐây có thể là mã OAuth Token thay vì API Key. Hệ thống vẫn sẽ thử gửi lên Google, nhưng nếu gặp lỗi 401 thì bạn cần vào lại AI Studio để lấy đúng API Key nhé.")
            
        def _run_test():
            try:
                from config import CONTENT_MODEL_ID
                
                # Cập nhật theo chuẩn mới: Truyền thẳng qua HTTP REST để tránh lỗi của SDK google-genai
                if api_key.startswith("AQ."):
                    import urllib.request
                    import json
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{CONTENT_MODEL_ID}:generateContent"
                    data = {"contents": [{"parts": [{"text": "Hãy trả lời duy nhất một câu: 'Hoạt động tốt!' bằng tiếng Việt."}]}]}
                    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'))
                    req.add_header("Content-Type", "application/json")
                    req.add_header("x-goog-api-key", api_key)
                    
                    with urllib.request.urlopen(req, timeout=15) as res:
                        resp_data = json.loads(res.read().decode('utf-8'))
                        ai_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
                        self.after(0, lambda: messagebox.showinfo("Thành công", f"Gemini API (Chuẩn mới) phản hồi thành công!\n\nKết quả từ AI: {ai_text}"))
                else:
                    from google import genai
                    client = genai.Client(api_key=api_key)
                    response = client.models.generate_content(
                        model=CONTENT_MODEL_ID,
                        contents="Hãy trả lời duy nhất một câu: 'Hoạt động tốt!' bằng tiếng Việt.",
                    )
                    self.after(0, lambda: messagebox.showinfo("Thành công", f"Gemini API phản hồi thành công!\n\nKết quả từ AI: {response.text}"))
                    
            except Exception as e:
                err_msg = str(e)
                if hasattr(e, 'read'):
                    err_msg += "\n" + e.read().decode('utf-8')
                self.after(0, lambda err=err_msg: messagebox.showerror("Lỗi kết nối", f"Không thể kết nối đến Gemini.\n\nChi tiết lỗi:\n{err}"))
                
        messagebox.showinfo("Đang kiểm tra", "Đang gửi tín hiệu thử nghiệm đến Google Gemini... Vui lòng đợi.")
        import threading
        threading.Thread(target=_run_test, daemon=True).start()

    def _save(self):
        data = {k: v.get().strip() for k, v in self._vars.items() if v.get().strip()}
        save_env(data)
        self._saved_lbl.config(text="Đã cập nhật tệp tin cấu hình!")
        self.after(2000, lambda: self._saved_lbl.config(text=""))

    def on_show(self):
        env = load_env()
        for k, v in self._vars.items():
            v.set(env.get(k, ""))


if __name__ == "__main__":
    if not HAS_PIL:
        print("[INFO] Thư viện Pillow chưa cài - Ảnh xem trước (thumbnail) sẽ không hiển thị.")
        print("       Cài đặt bằng lệnh: pip install Pillow")
        
    app = App()
    app.mainloop()
