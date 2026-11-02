#!/usr/bin/env python3
"""
Auto-Translade-Video — Giao diện Desktop Premium & Hiện đại
Cửa sổ điều khiển quy trình lồng tiếng Anh/Trung/Hàn -> Việt/Nhật
"""

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import os, sys, json, shutil, subprocess, threading, queue, re
from pathlib import Path
from src.subtitle_fonts import DEFAULT_FONT, DEFAULT_SIZE, get_font, validate_size
from src.subtitle_style import (STYLE_DEFAULTS, SUBTITLE_DEFAULTS, SubtitleStyle,
                                load_project_subtitles, save_project_subtitles)
from src.storage import Store
from src.preferences import load_preferences
from src.voice_registry import options as voice_options, default_reference, resolve as resolve_voice

try:
    from PIL import Image, ImageTk
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

BASE_DIR = Path(__file__).parent
ENV_PATH = BASE_DIR / ".env"
PYTHON   = sys.executable
OUTPUT_DIR = BASE_DIR / "output" / "VN"

# Shared colors and components for the warm, editorial studio UI.
from studio_ui import (
    BG, WHITE, SIDEBAR, SIDEBAR_HOVER, SIDEBAR_ACTIVE, SIDEBAR_TEXT,
    SIDEBAR_MUTED, PRIMARY, PRIMARY_HOVER, PRIMARY_LIGHT, TEXT, MUTED,
    BORDER, CARD_BOR, SUCCESS, SUCCESS_BG, WARN, WARN_BG, DANGER,
    DANGER_BG, INFO_BG, LOG_BG, LOG_FG, STEP_IDLE, STEP_ACT,
    FF, FH1, FH2, FH3, FB, FS, FM, Btn, entry, heading, StudioBanner,
    empty_art, rounded_rect, Choice,
)
STEPS = ["Nguồn video", "Nhận dạng", "Bản dịch", "Lồng tiếng", "Hoàn tất"]


def load_env():
    """SQLite owns preferences; .env supplies credentials and legacy imports."""
    return load_preferences(ENV_PATH)


def save_env(new_data):
    """Cập nhật .env nhưng giữ nguyên các dòng comment và thứ tự hiện có."""
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
    Store().save_settings(new_data)
    Store().seed_voices(load_env())


def extract_thumbnail(video_path, out_path):
    """Lấy một frame đại diện bằng FFmpeg để làm thumbnail dự án."""
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path),
             "-ss", "00:00:01", "-frames:v", "1", "-q:v", "2", str(out_path)],
            capture_output=True, timeout=12)
        return out_path.exists()
    except Exception:
        return False


def scan_projects():
    """Quét thư mục output và trả danh sách dự án Nhật/Việt mới nhất."""
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

    known = {str(project["path"].resolve()) for project in projects}
    for record in Store().projects():
        folder = Path(record["path"])
        if str(folder) not in known and folder.is_dir():
            language = record["language"]
            project = {"path": folder, "name": record["name"], "lang": "VI" if language == "vi" else "JP",
                       "lang_text": "Tiếng Việt (VI)" if language == "vi" else "Tiếng Nhật (JP)"}
            set_status_and_files(project, folder, f"transcript_{language}.json")
            projects.append(project)
    active_paths = Store().active_project_paths()
    for project in projects:
        if str(project["path"].resolve()) in active_paths:
            project["status"] = "running"
        language = "vi" if project["lang"] == "VI" else "jp"
        store = Store()
        project["database_id"] = store.sync_project(project["path"], language, project["status"])
    # Sắp xếp mtime mới nhất
    projects.sort(key=lambda p: p["path"].stat().st_mtime, reverse=True)
    return projects


def set_status_and_files(proj, folder, trans_name):
    """Suy ra trạng thái dự án và các file thumbnail/video đầu ra."""
    if (folder / "dubbed_video.mp4").exists():
        proj["status"] = "done"
    elif (folder / "TRANSLATE_PENDING.txt").exists():
        proj["status"] = "pending"
    elif (folder / trans_name).exists():
        proj["status"] = "tts"
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
    """Tạo đường kẻ phân cách mảnh dùng chung trong giao diện."""
    return tk.Frame(parent, bg=BORDER, height=1, **kw)


# ─── KHUNG CUỘN THẨM MỸ ──────────────────────────────────────────────
class ScrollFrame:
    """Khung chứa Canvas và scrollbar để cuộn các panel dài của GUI."""

    def __init__(self, parent, bg=BG):
        """Tạo canvas, vùng nội dung bên trong và các binding cuộn chuột."""
        self.outer = tk.Frame(parent, bg=bg)
        self._c = tk.Canvas(self.outer, bg=bg, highlightthickness=0)
        self._sb = ttk.Scrollbar(self.outer, orient="vertical", command=self._c.yview,
                                 style="Studio.Vertical.TScrollbar")
        self._c.configure(yscrollcommand=self._sb.set)
        self._sb.pack(side="right", fill="y")
        self._c.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self._c, bg=bg)
        self._w = self._c.create_window((0, 0), window=self.inner, anchor="nw")
        
        self.inner.bind("<Configure>", self._on_configure)
        self._c.bind("<Configure>", self._on_canvas_configure)
        self._wheel_bind = self._c.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.outer.bind("<Destroy>", self._on_destroy, add="+")

    def _on_configure(self, event):
        """Cập nhật vùng cuộn theo kích thước nội dung bên trong."""
        self._c.configure(scrollregion=self._c.bbox("all"))

    def _on_canvas_configure(self, event):
        """Ép vùng nội dung rộng bằng canvas để layout không bị co."""
        self._c.itemconfig(self._w, width=event.width)

    def _on_mousewheel(self, event):
        """Only scroll the visible panel under the pointer, including macOS deltas."""
        if not self.outer.winfo_exists() or not self.outer.winfo_ismapped():
            return
        widget = self.outer.winfo_containing(event.x_root, event.y_root)
        target = widget
        while target is not None and target is not self.outer:
            # Text editors and comboboxes retain their own scrolling behavior.
            if isinstance(target, (tk.Text, ttk.Combobox)):
                return
            target = getattr(target, "master", None)
        if target is not self.outer or not event.delta:
            return
        delta = -event.delta if sys.platform == "darwin" else -event.delta / 120
        self._c.yview_scroll(int(delta) or (-1 if event.delta > 0 else 1), "units")

    def _on_destroy(self, event):
        if event.widget is self.outer and self._wheel_bind:
            # Remove this callback only; other scroll frames keep their bindings.
            self.outer._root()._unbind(("bind", "all", "<MouseWheel>"), self._wheel_bind)
            self._wheel_bind = None

    def reset_scroll(self):
        """Đưa vị trí cuộn về đầu panel."""
        self._c.yview_moveto(0)


# ─── APP CHÍNH ───────────────────────────────────────────────────────
class App(tk.Tk):
    """Cửa sổ gốc quản lý sidebar và năm bước của quy trình lồng tiếng."""

    def __init__(self):
        """Khởi tạo theme, menu, các trang và mở trang quản lý dự án."""
        super().__init__()
        self.title("Auto-Dub Studio — Không gian sáng tạo")
        self.geometry("1320x860")
        self.minsize(1080, 720)
        self.configure(bg=BG)
        
        # Style controls đồng bộ với giao diện desktop.
        style = ttk.Style()
        style.theme_use('clam')
        style.configure(
            "Studio.TCombobox",
            fieldbackground=WHITE,
            background=WHITE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=PRIMARY,
            padding=(12, 9),
            font=FB,
        )
        style.map(
            "Studio.TCombobox",
            fieldbackground=[("readonly", WHITE), ("focus", WHITE)],
            foreground=[("readonly", TEXT)],
            bordercolor=[("focus", PRIMARY)],
            lightcolor=[("focus", PRIMARY)],
            darkcolor=[("focus", PRIMARY)],
        )
        style.configure(
            "Studio.Vertical.TScrollbar",
            background="#C9CDBE",
            troughcolor=BG,
            bordercolor=BG,
            arrowcolor=MUTED,
            width=10,
        )
        style.layout("Studio.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
            ]})
        ])
        
        self._pages = {}
        self._cur   = None
        self._build_layout()
        self._build_menu()
        self._show("projects")
        self.protocol("WM_DELETE_WINDOW", self._close_studio)
        self._bootstrap_after = self.after(250, self._bootstrap_storage)

    def _bootstrap_storage(self):
        self._bootstrap_after = None
        Store().recover_interrupted_runs()
        if not Store().counts()["artifacts"]:
            self._import_project_data()
            if "projects" in self._pages:
                self._pages["projects"]._refresh()

    def _close_studio(self):
        try:
            settings = self._pages.get("settings")
            if settings and settings.flush_autosave() is False:
                self._show("settings")
                return
            wizard = self._pages.get("new")
            if wizard:
                wizard.flush_translation_draft()
        except Exception as error:
            messagebox.showerror("Chưa lưu được dữ liệu", str(error))
            return
        self.destroy()

    def destroy(self):
        if getattr(self, "_bootstrap_after", None):
            self.after_cancel(self._bootstrap_after)
            self._bootstrap_after = None
        super().destroy()

    def _build_menu(self):
        """Tạo menu hệ thống và các lệnh kiểm tra dịch vụ AI."""
        menubar = tk.Menu(self)
        self.config(menu=menubar)
        
        sys_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Hệ thống", menu=sys_menu)
        sys_menu.add_command(label="Kiểm tra toàn trạng hệ thống...", command=self._test_system_status)
        
        tools_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Công cụ AI", menu=tools_menu)
        tools_menu.add_command(label="Kiểm tra kết nối Gemini API...", command=self._test_gemini_api)
        tools_menu.add_command(label="Nghe thử giọng đọc...", command=self.open_voice_preview)
        tools_menu.add_command(label="Phông chữ phụ đề...", command=self.open_subtitle_fonts)
        tools_menu.add_command(label="Thư viện giọng đọc...", command=self.open_voice_library)
        tools_menu.add_command(label="Dữ liệu SQLite...", command=self.open_database)

    def _apply_voice(self, language, reference, speed=None):
        current_settings = self._pages.get("settings")
        if current_settings and current_settings.flush_autosave() is False:
            return False
        environment = load_env()
        record = resolve_voice(language, reference, environment)
        values = {"DEFAULT_VOICE_VI" if language == "vi" else "DEFAULT_VOICE_JP": reference}
        if language == "vi" and reference in ("male", "female"):
            values["VOICE_TYPE"] = reference
        elif language == "jp" and not reference.startswith("voice:"):
            values["TTS_VOICE"] = record["provider_voice_id"]
        if speed is not None:
            values["TTS_SPEED"] = f"{speed:g}"
        save_env(values)
        if current_settings:
            current_settings.on_show()
        wizard = self._pages.get("new")
        if wizard:
            wizard._sync_voice_from_settings()
        return True

    def refresh_voice_library(self):
        environment = load_env()
        wizard = self._pages.get("new")
        if wizard:
            wizard._refresh_voice_options()
        preview = getattr(self, "_voice_preview", None)
        if preview and preview.winfo_exists():
            preview.refresh_voices(environment)

    def open_voice_library(self):
        settings = self._pages.get("settings")
        if settings and settings.flush_autosave() is False:
            return
        load_env()
        from storage_ui import VoiceLibraryDialog
        if getattr(self, "_voice_library", None) and self._voice_library.winfo_exists():
            self._voice_library.lift()
            return
        self._voice_library = VoiceLibraryDialog(self, self.refresh_voice_library,
                                                 self.open_voice_preview, self._apply_voice)

    def _import_project_data(self):
        from src.legacy_import import import_legacy
        return import_legacy(BASE_DIR, environment=load_env())["projects"]

    def open_database(self):
        from storage_ui import DatabaseDialog
        if getattr(self, "_database_dialog", None) and self._database_dialog.winfo_exists():
            self._database_dialog.refresh()
            self._database_dialog.lift()
            return
        self._database_dialog = DatabaseDialog(self, self._import_project_data)

    def open_subtitle_fonts(self):
        settings = self._pages.get("settings")
        if settings and settings.flush_autosave() is False:
            return
        if getattr(self, "_font_dialog", None) and self._font_dialog.winfo_exists():
            self._font_dialog.lift()
            return
        from subtitle_font_ui import SubtitleFontDialog
        def apply_selection(name, size, style):
            current_settings = self._pages.get("settings")
            if current_settings and current_settings.flush_autosave() is False:
                return False
            save_env({"SUBTITLE_FONT": name, "SUBTITLE_FONT_SIZE": str(size), **style.to_settings()})
            if current_settings:
                current_settings.on_show()
            wizard = self._pages.get("new")
            if wizard:
                wizard._sync_subtitle_from_settings()
            return True
        self._font_dialog = SubtitleFontDialog(self, load_env(), on_apply=apply_selection)

    def open_voice_preview(self, language="vi", voice=None):
        settings = self._pages.get("settings")
        if settings and settings.flush_autosave() is False:
            return
        from voice_preview_ui import VoicePreviewDialog
        if getattr(self, "_voice_preview", None) and self._voice_preview.winfo_exists():
            self._voice_preview.refresh_voices(load_env(), language, voice)
            self._voice_preview.lift()
            return
        def apply_selection(lang, selected_voice, speed):
            current_settings = self._pages.get("settings")
            if current_settings and current_settings.flush_autosave() is False:
                return False
            return self._apply_voice(lang, selected_voice, speed)
        self._voice_preview = VoicePreviewDialog(
            self, load_env(), language=language, voice=voice, on_apply=apply_selection,
            on_manage=self.open_voice_library,
        )
        
    def _test_gemini_api(self):
        """Kiểm tra kết nối Gemini ở background để không khóa giao diện."""
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
        """Kiểm tra FFmpeg, thư viện AI và các API key cơ bản."""
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
            import config as runtime_config
            vietnamese_key_count = len(getattr(runtime_config, "VIETNAMESE_API_KEYS", ()) or ())
            if GOOGLE_API_KEY:
                results.append("✅ Gemini API Key: Đã cấu hình")
            else:
                results.append("⚠️ Gemini API Key: Chưa cấu hình (chỉ ảnh/mô tả tuỳ chọn)")
                
            if VIETNAMESE_API_KEY or vietnamese_key_count:
                results.append(f"✅ LucyLab TTS API Key: Đã cấu hình ({vietnamese_key_count} key, có dự phòng tự động)")
            else:
                results.append("⚠️ LucyLab TTS API Key: Chưa cấu hình (Giọng đọc tiếng Việt có thể không hoạt động)")

            # Hiển thị kết quả
            msg = "\n\n".join(results)
            self.after(0, lambda: messagebox.showinfo("Kết quả kiểm tra hệ thống", f"Tình trạng các thành phần hệ thống:\n\n{msg}"))

        messagebox.showinfo("Đang kiểm tra", "Hệ thống đang quét các thành phần và thư viện AI... Quá trình này mất khoảng vài giây.")
        threading.Thread(target=_run_checks, daemon=True).start()


    def _build_layout(self):
        self.sb_frame = tk.Frame(self, bg=SIDEBAR, width=218)
        self.sb_frame.pack(side="left", fill="y")
        self.sb_frame.pack_propagate(False)
        brand = tk.Frame(self.sb_frame, bg=SIDEBAR)
        brand.pack(fill="x", padx=22, pady=(29, 30))
        mark = tk.Canvas(brand, width=36, height=38, bg=SIDEBAR, highlightthickness=0)
        mark.pack(side="left", padx=(0, 10))
        rounded_rect(mark, 0, 1, 36, 37, 10, fill=PRIMARY, outline=PRIMARY)
        for x, h in ((10, 10), (15, 20), (21, 14), (26, 7)):
            mark.create_line(x, 19-h/2, x, 19+h/2, fill=WHITE, width=2, capstyle="round")
        text = tk.Frame(brand, bg=SIDEBAR)
        text.pack(side="left")
        tk.Label(text, text="auto-dub", font=(FF, 19, "bold"), bg=SIDEBAR,
                 fg=TEXT).pack(anchor="w")
        tk.Label(text, text="YOUR VIDEO, REIMAGINED", font=(FF, 7, "bold"),
                 bg=SIDEBAR, fg=MUTED).pack(anchor="w", pady=(1, 0))
        Btn(self.sb_frame, "＋  Tạo video mới", command=self.reset_new,
            padx=20, pady=11).pack(fill="x", padx=18, pady=(0, 29))
        tk.Label(self.sb_frame, text="KHÔNG GIAN LÀM VIỆC", font=(FF, 8, "bold"),
                 bg=SIDEBAR, fg=SIDEBAR_MUTED).pack(anchor="w", padx=25, pady=(0, 10))
        self._nav, self._nav_frames, self._stripes = {}, {}, {}
        for key, icon, label in (("projects", "▦", "Thư viện video"),
                                 ("new", "↗", "Phòng lồng tiếng"),
                                 ("settings", "⚙", "Cài đặt")):
            frame = tk.Frame(self.sb_frame, bg=SIDEBAR, height=47, cursor="hand2",
                             highlightthickness=0)
            frame.pack(fill="x", padx=13, pady=3)
            frame.pack_propagate(False)
            stripe = tk.Frame(frame, bg=SIDEBAR, width=3)
            stripe.pack(side="left", fill="y", pady=12)
            glyph = tk.Label(frame, text=icon, font=(FF, 17), bg=SIDEBAR,
                             fg=SIDEBAR_MUTED, width=3)
            glyph.pack(side="left", padx=(4, 1))
            label_widget = tk.Label(frame, text=label, font=(FF, 11),
                                    bg=SIDEBAR, fg=SIDEBAR_MUTED, anchor="w")
            label_widget.pack(side="left", fill="both", expand=True)
            self._nav[key], self._nav_frames[key], self._stripes[key] = label_widget, frame, stripe
            for widget in (frame, glyph, label_widget):
                widget.bind("<Button-1>", lambda e, k=key: self._show(k))
                widget.bind("<Enter>", lambda e, k=key, f=frame: self._on_menu_enter(k, f))
                widget.bind("<Leave>", lambda e, k=key, f=frame: self._on_menu_leave(k, f))
            frame.configure(takefocus=True)
            frame.bind("<Return>", lambda e, k=key: self._show(k))
            frame.bind("<space>", lambda e, k=key: self._show(k))
        footer = tk.Frame(self.sb_frame, bg=SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=22, pady=24)
        flat_sep(footer).pack(fill="x", pady=(0, 18))
        tk.Label(footer, text="Được tạo cho câu chuyện của bạn.", font=(FF, 9),
                 bg=SIDEBAR, fg=MUTED, wraplength=168, justify="left").pack(anchor="w")
        tk.Label(footer, text="AUTO-DUB STUDIO  /  DESKTOP", font=(FF, 7, "bold"),
                 bg=SIDEBAR, fg=SIDEBAR_MUTED).pack(anchor="w", pady=(13, 0))
        workspace = tk.Frame(self, bg=BG)
        workspace.pack(side="left", fill="both", expand=True)
        self.topbar = tk.Frame(workspace, bg=BG, height=58)
        self.topbar.pack(fill="x", padx=32)
        self.topbar.pack_propagate(False)
        tk.Label(self.topbar, text="Workspace  /", font=FS, bg=BG, fg=MUTED).pack(side="left")
        self._page_title = tk.Label(self.topbar, text="Thư viện video", font=(FF, 10, "bold"), bg=BG, fg=TEXT)
        self._page_title.pack(side="left", padx=8)
        self._page_hint = tk.Label(self.topbar, text="", bg=BG)
        tk.Label(self.topbar, text="●  Lưu trên thiết bị", font=(FF, 9),
                 bg=BG, fg=MUTED).pack(side="right")
        flat_sep(workspace).pack(fill="x", padx=32)
        self.content = tk.Frame(workspace, bg=BG)
        self.content.pack(fill="both", expand=True)

    def _on_menu_enter(self, key, frame):
        """Đổi màu dòng menu khi con trỏ đi vào, trừ mục đang active."""
        if key != self._cur:
            frame.config(bg=SIDEBAR_HOVER)
            frame.config(highlightbackground=SIDEBAR_HOVER)
            self._nav[key].config(bg=SIDEBAR_HOVER)
            for child in frame.winfo_children():
                child.config(bg=SIDEBAR_HOVER)
                if isinstance(child, tk.Frame):
                    for grandchild in child.winfo_children():
                        grandchild.config(bg=SIDEBAR_HOVER)

    def _on_menu_leave(self, key, frame):
        """Khôi phục màu dòng menu khi con trỏ rời khỏi."""
        if key != self._cur:
            frame.config(bg=SIDEBAR)
            frame.config(highlightbackground=SIDEBAR)
            self._nav[key].config(bg=SIDEBAR)
            for child in frame.winfo_children():
                if child is not self._stripes[key]:
                    child.config(bg=SIDEBAR)
                    if isinstance(child, tk.Frame):
                        for grandchild in child.winfo_children():
                            grandchild.config(bg=SIDEBAR)

    def _show(self, key):
        """Chuyển sang trang được chọn và đồng bộ trạng thái menu."""
        # Ghi ngay lần lưu cấu hình đang chờ debounce trước khi rời trang.
        # Nhờ vậy voice/API vừa sửa sẽ có sẵn cho subprocess pipeline tiếp theo,
        # kể cả khi người dùng chuyển trang ngay lập tức.
        if self._cur == "settings" and self._cur in self._pages:
            page = self._pages[self._cur]
            if hasattr(page, "flush_autosave"):
                if page.flush_autosave() is False:
                    return
        if self._cur == "new" and "new" in self._pages:
            self._pages["new"].flush_translation_draft()
        if self._cur:
            if self._cur in self._pages:
                self._pages[self._cur].pack_forget()
            # Reset menu trước đó về trạng thái thường
            previous_frame = self._nav_frames.get(self._cur, self._nav[self._cur].master)
            previous_frame.config(bg=SIDEBAR, highlightbackground=SIDEBAR)
            self._nav[self._cur].config(bg=SIDEBAR, fg=SIDEBAR_MUTED, font=FB)
            self._stripes[self._cur].config(bg=SIDEBAR)
            for child in previous_frame.winfo_children():
                if child is not self._stripes[self._cur]:
                    child.config(bg=SIDEBAR)
                    if isinstance(child, tk.Frame):
                        for grandchild in child.winfo_children():
                            grandchild.config(bg=SIDEBAR)
            
        self._cur = key
        # Active menu hiện tại
        active_frame = self._nav_frames.get(key, self._nav[key].master)
        active_frame.config(bg=SIDEBAR_ACTIVE, highlightbackground=SIDEBAR_ACTIVE)
        self._nav[key].config(bg=SIDEBAR_ACTIVE, fg=TEXT, font=(FF, 11, "bold"))
        self._stripes[key].config(bg=PRIMARY)
        for child in active_frame.winfo_children():
            child.config(bg=SIDEBAR_ACTIVE)
            if isinstance(child, tk.Frame):
                for grandchild in child.winfo_children():
                    grandchild.config(bg=SIDEBAR_ACTIVE)

        page_titles = {
            "projects": ("Thư viện video", "Theo dõi tiến độ và quản lý video đầu ra"),
            "new": ("Phòng lồng tiếng", "Thiết lập nguồn, dịch thuật và lồng tiếng theo quy trình"),
            "settings": ("Cài đặt", "Quản lý API, voice và tham số xử lý"),
        }
        if hasattr(self, "_page_title"):
            title, hint = page_titles[key]
            self._page_title.config(text=title)
            self._page_hint.config(text=hint)
        
        if key not in self._pages:
            cls = {"projects": ProjectsPage, "new": NewProjectPage, "settings": SettingsPage}[key]
            self._pages[key] = cls(self.content, self)
            
        self._pages[key].pack(fill="both", expand=True)
        if hasattr(self._pages[key], "on_show"):
            self._pages[key].on_show()

    def reset_new(self):
        """Xóa instance trang tạo dự án cũ và dựng lại form trống."""
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
    """Trang hiển thị dự án, trạng thái xử lý, preview và thao tác tải xuống."""
    STATUS_INFO = {
        "done":     ("Hoàn thành", SUCCESS, SUCCESS_BG),
        "tts":      ("Tổng hợp giọng", WARN, WARN_BG),
        "pending":  ("Chờ bản dịch", PRIMARY, INFO_BG),
        "asr_done": ("Đã nhận dạng", PRIMARY, PRIMARY_LIGHT),
        "running":  ("Đang xử lý", WARN, WARN_BG),
        "unknown":  ("Không xác định", MUTED, STEP_IDLE),
    }

    def __init__(self, parent, app):
        """Khởi tạo trang thư viện và dựng layout card."""
        super().__init__(parent, bg=BG)
        self.app = app
        self._ph = {}
        self._build()

    def _build(self):
        self._projects = []
        self._columns = 0
        self._resize_after = None
        self._search = tk.StringVar()
        self._filter = "all"
        self._search.trace_add("write", lambda *_: self._render_projects(reset_scroll=True))
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=32, pady=(24, 22))
        heading(hdr, "THƯ VIỆN CỦA BẠN", "Những câu chuyện đang tạo.",
                "Tất cả video và hành trình chuyển ngữ, ở cùng một nơi.").pack(side="left")
        StudioBanner(self).pack(fill="x", padx=32, pady=(0, 24))
        title_row = tk.Frame(self, bg=BG)
        title_row.pack(fill="x", padx=32, pady=(0, 12))
        tk.Label(title_row, text="Dự án của bạn", font=FH2, bg=BG, fg=TEXT).pack(side="left")
        self._summary = tk.Label(title_row, text="", font=FS, bg=BG, fg=MUTED)
        self._summary.pack(side="left", padx=14)
        Btn(title_row, "↻  Làm mới", command=self._refresh, bg=BG, fg=MUTED,
            padx=8, pady=5, font=FS).pack(side="right")
        tools = tk.Frame(self, bg=BG)
        tools.pack(fill="x", padx=32, pady=(0, 14))
        self._filter_buttons = {}
        for key, label in (("all", "Tất cả"), ("pending", "Chờ dịch"), ("active", "Đang làm"), ("done", "Hoàn tất")):
            btn = Btn(tools, label, command=lambda k=key: self._set_filter(k),
                      bg=TEXT if key == "all" else BG,
                      fg=WHITE if key == "all" else MUTED, font=FS, padx=13, pady=7)
            btn.pack(side="left", padx=(0, 5))
            self._filter_buttons[key] = btn
        search_box = tk.Frame(tools, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        search_box.pack(side="right")
        tk.Label(search_box, text="⌕", font=(FF, 18), bg=WHITE, fg=MUTED).pack(side="left", padx=(9, 2))
        search = tk.Entry(search_box, textvariable=self._search, font=FS, bg=WHITE,
                          fg=TEXT, insertbackground=PRIMARY, relief="flat", width=21, bd=7)
        search.pack(side="left")
        search.bind("<Escape>", lambda e: self._search.set(""))
        search.bind("<FocusIn>", lambda e: search_box.config(highlightbackground=PRIMARY))
        search.bind("<FocusOut>", lambda e: search_box.config(highlightbackground=BORDER))
        self._search_hint = tk.Label(search_box, text="Tìm dự án…", font=FS,
                                     bg=WHITE, fg=MUTED, cursor="xterm")
        self._search_hint.place(x=33, rely=.5, anchor="w")
        self._search_hint.bind("<Button-1>", lambda e: search.focus_set())
        def update_hint(*_):
            if self._search.get() or search.focus_get() is search:
                self._search_hint.place_forget()
            else:
                self._search_hint.place(x=33, rely=.5, anchor="w")
        self._search.trace_add("write", update_hint)
        search.bind("<FocusIn>", update_hint, add="+")
        search.bind("<FocusOut>", update_hint, add="+")
        self._sf = ScrollFrame(self, bg=BG)
        self._sf.outer.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        self._grid = self._sf.inner
        self._sf._c.bind("<Configure>", self._on_grid_resize, add="+")
        self.bind("<Destroy>", self._cancel_resize, add="+")

    def _cancel_resize(self, event):
        if event.widget is self and self._resize_after:
            self.after_cancel(self._resize_after)
            self._resize_after = None

    def _set_filter(self, key):
        self._filter = key
        for name, button in self._filter_buttons.items():
            button.config(bg=TEXT if name == key else BG, fg=WHITE if name == key else MUTED)
        self._render_projects(reset_scroll=True)

    def _on_grid_resize(self, event):
        columns = 3 if event.width >= 990 else 2 if event.width >= 580 else 1
        if columns != self._columns:
            if self._resize_after:
                self.after_cancel(self._resize_after)
            self._resize_after = self.after(100, self._render_projects)

    def on_show(self):
        """Làm mới danh sách ngay khi người dùng quay lại trang."""
        self._refresh()

    def _refresh(self):
        self._projects = scan_projects()
        done = sum(project.get("status") == "done" for project in self._projects)
        self._summary.config(text=f"{len(self._projects)} dự án  ·  {done} hoàn tất")
        self._render_projects()

    def _render_projects(self, reset_scroll=False):
        self._resize_after = None
        for widget in self._grid.winfo_children():
            widget.destroy()
        self._ph.clear()
        width = self._sf._c.winfo_width()
        self._columns = cols = 3 if width >= 990 else 2 if width >= 580 else 1
        for column in range(3):
            self._grid.columnconfigure(column, weight=1 if column < cols else 0,
                                       uniform="project" if column < cols else "", minsize=0)
        query = self._search.get().strip().casefold()
        projects = []
        for project in self._projects:
            status = project.get("status")
            if query and query not in (project["name"] + " " + project["lang_text"]).casefold():
                continue
            if self._filter == "active" and status in ("done", "pending"):
                continue
            if self._filter in ("pending", "done") and status != self._filter:
                continue
            projects.append(project)
        if not projects:
            empty = tk.Frame(self._grid, bg=WHITE, highlightbackground=BORDER,
                              highlightthickness=1, padx=20, pady=24)
            empty.grid(row=0, column=0, columnspan=cols, sticky="ew", padx=8, pady=5)
            empty_art(empty).pack()
            has_projects = bool(self._projects)
            tk.Label(empty, text="Không tìm thấy dự án" if has_projects else "Câu chuyện đầu tiên bắt đầu từ đây.",
                     font=FH2, bg=WHITE, fg=TEXT).pack(pady=(10, 6))
            tk.Label(empty, text="Thử tên khác hoặc chọn lại bộ lọc." if has_projects else "Thêm một video. Chọn giọng đọc. Tạo phiên bản của riêng bạn.",
                     font=FB, bg=WHITE, fg=MUTED).pack()
            Btn(empty, "Xóa bộ lọc" if has_projects else "＋  Tạo video đầu tiên",
                command=self._clear_filters if has_projects else self.app.reset_new,
                pady=9).pack(pady=(18, 0))
        else:
            for i, project in enumerate(projects):
                card = self._make_card(self._grid, project)
                card.grid(row=i // cols, column=i % cols, padx=8, pady=(3, 16), sticky="nsew")
        if reset_scroll:
            self._sf.reset_scroll()

    def _clear_filters(self):
        self._search.set("")
        self._set_filter("all")

    def _make_card(self, parent, proj):
        card = tk.Frame(parent, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        status = proj.get("status", "unknown")
        status_label, status_color, status_bg = self.STATUS_INFO.get(status, ("Chưa xác định", MUTED, STEP_IDLE))
        preview = tk.Frame(card, bg=LOG_BG, height=172)
        preview.pack(fill="x", padx=8, pady=(8, 0))
        preview.pack_propagate(False)
        thumb_loaded = False
        if HAS_PIL and proj.get("thumbnail") and proj["thumbnail"].exists():
            try:
                with Image.open(proj["thumbnail"]) as original:
                    image = original.copy()
                image.thumbnail((480, 172), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(image)
                self._ph[str(proj["path"])] = photo
                thumb = tk.Label(preview, image=photo, bg=LOG_BG, cursor="hand2")
                thumb.pack(expand=True)
                thumb_loaded = True
            except (OSError, ValueError):
                pass
        if not thumb_loaded:
            thumb = tk.Label(preview, text="▷", font=(FF, 36), bg=LOG_BG, fg="#BCCA9C", cursor="hand2")
            thumb.pack(expand=True)
        tk.Label(preview, text="VI" if proj["lang"] == "VI" else "JP", bg=WHITE, fg=TEXT,
                 font=(FF, 8, "bold"), padx=7, pady=4).place(x=10, y=10)
        video = proj["dubbed_video"] or self._find_source_video(proj)
        if video:
            thumb.bind("<Button-1>", lambda e: self._preview_video(proj))
            play = tk.Label(preview, text="▶", font=(FF, 16), bg=LOG_BG, fg=WHITE,
                            cursor="hand2", padx=10, pady=6)
            play.place(relx=.5, rely=.5, anchor="center")
            play.bind("<Button-1>", lambda e: self._preview_video(proj))
        info = tk.Frame(card, bg=WHITE, padx=16, pady=14)
        info.pack(fill="x")
        name = tk.Label(info, text=proj["name"], font=(FF, 12, "bold"), bg=WHITE,
                         fg=TEXT, anchor="w", justify="left", wraplength=260)
        name.pack(fill="x")
        info.bind("<Configure>", lambda e: name.config(wraplength=max(150, e.width-32)))
        try:
            import datetime
            updated = datetime.datetime.fromtimestamp(proj["path"].stat().st_mtime).strftime("%d.%m.%Y")
        except OSError:
            updated = ""
        tk.Label(info, text=f"{proj['lang_text']}  ·  {updated}", font=(FF, 9),
                 bg=WHITE, fg=MUTED, anchor="w").pack(fill="x", pady=(6, 12))
        tk.Label(info, text=f"●  {status_label}", font=(FF, 9), bg=status_bg,
                 fg=status_color, padx=9, pady=5).pack(anchor="w")
        flat_sep(card).pack(fill="x", padx=16)
        actions = tk.Frame(card, bg=WHITE, padx=10, pady=8)
        actions.pack(fill="x", side="bottom")
        if proj["dubbed_video"]:
            Btn(actions, "Xem video", command=lambda: self._preview_video(proj),
                bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=10, pady=7).pack(side="left")
            Btn(actions, "Tải xuống", command=lambda: self._download(proj),
                bg=WHITE, fg=TEXT, font=FS, padx=8, pady=7).pack(side="left", padx=4)
        else:
            Btn(actions, "Tiếp tục  ↗", command=lambda: self.app.continue_project(proj),
                bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=12, pady=7).pack(side="left")
        Btn(actions, "Xóa", command=lambda: self._delete(proj), bg=WHITE,
            fg=DANGER, font=(FF, 9), padx=7, pady=7).pack(side="right")
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
        """Xóa toàn bộ thư mục dự án sau khi người dùng xác nhận."""
        if messagebox.askyesno("Xoá dự án", f"Bạn có chắc chắn muốn xoá dự án '{proj['name']}'?\nThao tác này sẽ xoá bỏ hoàn toàn các tệp tin lưu trên đĩa."):
            shutil.rmtree(proj["path"], ignore_errors=True)
            if not proj["path"].exists():
                Store().mark_project_deleted(proj["path"])
            self._refresh()

    def _download(self, proj):
        """Sao chép video đã lồng tiếng sang thư mục người dùng chọn."""
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
        """Dựng popup thông tin/thumbnail và các nút mở hoặc tải video."""
        self.proj = proj
        self.video_path = video_path

        dlg = tk.Toplevel(parent)
        dlg.title(f"Auto-Dub  |  {proj['name']}")
        dlg.configure(bg=BG)
        dlg.transient(parent)
        dlg.grab_set()
        dlg.resizable(False, False)
        self.dlg = dlg

        # ── Header thông tin dự án ──
        hdr = tk.Frame(dlg, bg=BG, padx=24, pady=20)
        hdr.pack(fill="x")
        tk.Label(hdr, text=f"XEM TRƯỚC  /  {proj['name']}", font=(FF, 13, "bold"),
                 bg=BG, fg=TEXT, anchor="w").pack(fill="x")
        tk.Label(hdr, text=proj.get("lang_text", ""), font=FS, bg=BG,
                 fg=SIDEBAR_MUTED, anchor="w").pack(fill="x", pady=(3, 0))

        # ── Thumbnail preview lớn ──
        thumb_frame = tk.Frame(dlg, bg=LOG_BG, width=560, height=315)
        thumb_frame.pack(fill="x")
        thumb_frame.pack_propagate(False)

        thumb_loaded = False
        if HAS_PIL and proj.get("thumbnail") and Path(proj["thumbnail"]).exists():
            try:
                img = Image.open(proj["thumbnail"])
                img.thumbnail((560, 315), Image.Resampling.LANCZOS)
                ph = ImageTk.PhotoImage(img)
                dlg._thumb_ph = ph  # giữ tham chiếu
                thumb_lbl = tk.Label(thumb_frame, image=ph, bg=LOG_BG)
                thumb_lbl.pack(expand=True)
                thumb_loaded = True
            except Exception:
                pass

        if not thumb_loaded:
            tk.Label(thumb_frame, text="▷", font=(FF, 48), bg=LOG_BG, fg=SIDEBAR_MUTED).pack(expand=True)

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
            ("Dự án",  proj["name"]),
            ("Ngôn ngữ",   proj.get("lang_text", "N/A")),
            ("Trạng thái", st_label),
            ("Dung lượng", size_str),
            ("Đường dẫn",  str(video_path)),
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

        Btn(btn_frame, "▶  Phát video",
            command=self._open_system_player,
            bg=PRIMARY, padx=16, pady=8).pack(side="left")

        Btn(btn_frame, "Mở thư mục",
            command=self._open_folder,
            bg=LOG_BG, padx=16, pady=8).pack(side="left", padx=(10, 0))

        if proj["dubbed_video"]:
            Btn(btn_frame, "Tải xuống",
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
    """Trang wizard nhập nguồn, chạy ASR, dịch thủ công và khởi động TTS."""

    def __init__(self, parent, app):
        """Khởi tạo trạng thái wizard, hàng đợi log và năm panel giao diện."""
        super().__init__(parent, bg=BG)
        self.app       = app
        self._work_dir = None
        self._dubbed   = None
        self._full_json= ""
        self._q        = queue.Queue()
        self._polling  = False
        self._draft_after = None
        self._loading_translation = False
        self._draft_last_text = None
        env = load_env()
        self.sub_font_var = tk.StringVar(value=env.get("SUBTITLE_FONT") or DEFAULT_FONT)
        self.sub_size_var = tk.StringVar(value=env.get("SUBTITLE_FONT_SIZE") or str(DEFAULT_SIZE))
        self.sub_style_vars = {key: tk.StringVar(master=self, value=env.get(key, value))
                               for key, value in STYLE_DEFAULTS.items()}
        self._subtitle_custom = False
        self._subtitle_summary = tk.StringVar()
        self.sub_font_var.trace_add("write", self._update_subtitle_summary)
        self.sub_size_var.trace_add("write", self._update_subtitle_summary)
        for variable in self.sub_style_vars.values():
            variable.trace_add("write", self._update_subtitle_summary)
        self._update_subtitle_summary()
        self._build()

    def _build(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=32, pady=(24, 23))
        heading(hdr, "PHÒNG LỒNG TIẾNG", "Một video. Một phiên bản mới.",
                "Đi từng bước để chuyển ngữ và hoàn thiện video của bạn.").pack(side="left")
        steps = tk.Frame(self, bg=BG)
        steps.pack(fill="x", padx=32, pady=(0, 18))
        self._slbls = []
        for i, label in enumerate(STEPS):
            steps.columnconfigure(i, weight=1, uniform="step")
            block = tk.Frame(steps, bg=BG)
            block.grid(row=0, column=i, sticky="ew", padx=(0, 5))
            lbl = tk.Label(block, text=f"{i+1:02d}  {label}", font=(FF, 10), bg=STEP_IDLE,
                           fg=MUTED, padx=8, pady=11, anchor="w")
            lbl.pack(fill="x")
            self._slbls.append(lbl)
        self._sf = ScrollFrame(self, bg=BG)
        self._sf.outer.pack(fill="both", expand=True, pady=(0, 8))
        body = self._sf.inner
        self._p = [self._mk_p1(body), self._mk_p2(body), self._mk_p3(body),
                   self._mk_p4(body), self._mk_p5(body)]
        self._goto(0)

    def _card(self, parent, title=""):
        wrap = tk.Frame(parent, bg=BG)
        wrap.pack(fill="x", padx=32, pady=(0, 14))
        frame = tk.Frame(wrap, bg=WHITE, highlightbackground=BORDER, highlightthickness=1)
        frame.pack(fill="x")
        inner = tk.Frame(frame, bg=WHITE, padx=22, pady=19)
        inner.pack(fill="x")
        if title:
            tk.Label(inner, text=title, font=FH2, bg=WHITE, fg=TEXT,
                     anchor="w").pack(anchor="w", pady=(0, 14))
        return inner, wrap

    def _mk_p1(self, body):
        panel = tk.Frame(body, bg=BG)
        source, _ = self._card(panel, "Bắt đầu với video của bạn")
        self.url_var, self.file_var = tk.StringVar(), tk.StringVar()
        tk.Label(source, text="Dán liên kết video", font=FH3, bg=WHITE, fg=TEXT).pack(anchor="w")
        tk.Label(source, text="YouTube, TikTok, Douyin, Bilibili và nhiều nền tảng khác.",
                 font=FS, bg=WHITE, fg=MUTED).pack(anchor="w", pady=(3, 9))
        entry(source, self.url_var).pack(fill="x")
        local = tk.Frame(source, bg=WHITE)
        local.pack(fill="x", pady=(15, 0))
        tk.Label(local, text="hoặc video từ máy", font=FS, bg=WHITE, fg=MUTED).pack(side="left", padx=(0, 12))
        entry(local, self.file_var).pack(side="left", fill="x", expand=True)
        Btn(local, "Chọn video…", command=self._pick_file, bg=SIDEBAR,
            fg=TEXT, font=FS, padx=14, pady=9).pack(side="left", padx=(10, 0))
        options, _ = self._card(panel, "Chọn cách video cất lời")
        saved = load_env()
        voice_type = saved.get("VOICE_TYPE", "").strip().lower()
        self.target_lang_var = tk.StringVar(value="Tiếng Việt (VI)")
        self.lang_var = tk.StringVar(value="zh")
        self.voice_var = tk.StringVar(value=default_reference("vi", saved))
        self.bg_var = tk.StringVar(value="demucs")
        self.sub_y_var = tk.StringVar(value="780")
        self._saved_japanese_voice = saved.get("TTS_VOICE", "").strip() or "ja-JP-KeitaNeural"
        grid = tk.Frame(options, bg=WHITE)
        grid.pack(fill="x")
        voice_refs, voice_labels = voice_options("vi", saved)
        fields = [
            ("Ngôn ngữ gốc", self.lang_var, ["zh", "en", "ja", "vi", "ko"]),
            ("Chuyển ngữ sang", self.target_lang_var, ["Tiếng Việt (VI)", "Tiếng Nhật (JP)"]),
            ("Giọng đọc", self.voice_var, voice_refs),
            ("Nhạc nền", self.bg_var, ["demucs", "duck", "none"]),
        ]
        for i, (label, variable, values) in enumerate(fields):
            grid.columnconfigure(i, weight=1, uniform="option")
            cell = tk.Frame(grid, bg=WHITE)
            cell.grid(row=0, column=i, sticky="ew", padx=(0, 12 if i < 3 else 0))
            tk.Label(cell, text=label, font=FS, bg=WHITE, fg=MUTED).pack(anchor="w", pady=(0, 7))
            combo = Choice(cell, textvariable=variable, values=values,
                                  labels=voice_labels if i == 2 else None,
                                  state="readonly", width=10, font=FS, style="Studio.TCombobox")
            combo.pack(fill="x")
            if i == 1:
                combo.bind("<<ComboboxSelected>>", self._on_target_lang_change)
            elif i == 2:
                self.cb_voice = combo
        audition = tk.Frame(options, bg=WHITE)
        audition.pack(fill="x", pady=(12, 0))
        Btn(audition, "▶  Nghe thử giọng đọc", command=lambda: self.app.open_voice_preview(
            "vi" if "Việt" in self.target_lang_var.get() else "jp", self.voice_var.get()),
            bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=13, pady=8).pack(side="left")
        self._speed_hint = tk.Label(audition, text="", font=FS, bg=WHITE, fg=MUTED)
        self._speed_hint.pack(side="left", padx=12)
        Btn(audition, "＋  Thư viện giọng", command=self.app.open_voice_library,
            bg=WHITE, fg=TEXT, font=FS, padx=10, pady=8).pack(side="right")
        flat_sep(options).pack(fill="x", pady=(20, 14))
        tk.Label(options, text="Làm sạch phụ đề cũ", font=FH3, bg=WHITE, fg=TEXT).pack(anchor="w")
        self.burn_subtitles_var = tk.BooleanVar(value=True)
        self.ocr_replace_var = tk.BooleanVar(value=True)
        self.ai_blur_var = tk.BooleanVar(value=False)
        for label, variable, command in (
            ("Xóa chữ bằng OCR và đặt phụ đề vào vị trí gốc", self.ocr_replace_var, self._on_ocr_replace_toggle),
            ("Làm mờ bằng AI (phương án thay thế)", self.ai_blur_var, self._on_ai_blur_toggle),
        ):
            tk.Checkbutton(options, text=label, variable=variable, command=command,
                           font=FB, bg=WHITE, fg=TEXT, activebackground=WHITE,
                           selectcolor=WHITE, highlightthickness=0, cursor="hand2").pack(anchor="w", pady=(8, 0))
        tk.Label(options, text="Vùng xử lý OCR: 20–40% tính từ đáy video.",
                 font=FS, bg=WHITE, fg=MUTED).pack(anchor="w", pady=(8, 0))
        fonts, _ = self._card(panel, "Phông chữ phụ đề")
        self._font_shortcut(fonts)
        actions = tk.Frame(panel, bg=BG)
        actions.pack(fill="x", padx=32, pady=(2, 20))
        Btn(actions, "Phân tích video  →", command=self._start_asr, padx=24, pady=11).pack(side="right")
        tk.Label(actions, text="Tiếp theo: nhận dạng lời nói và tạo bản dịch.", font=FS,
                 bg=BG, fg=MUTED).pack(side="left", pady=8)
        return panel

    def _on_target_lang_change(self, event):
        self._refresh_voice_options(prefer_default=True)

    def _refresh_voice_options(self, prefer_default=False):
        environment = load_env()
        language = "vi" if "Việt" in self.target_lang_var.get() else "jp"
        references, labels = voice_options(language, environment)
        self.cb_voice.config(values=references, labels=labels)
        if prefer_default or self.voice_var.get() not in references:
            self.voice_var.set(default_reference(language, environment))

    def _on_ocr_replace_toggle(self):
        """Chọn OCR replace thì tắt AI blur để tránh xử lý video hai lần."""
        if self.ocr_replace_var.get():
            self.ai_blur_var.set(False)

    def _on_ai_blur_toggle(self):
        """Chọn AI blur thì tắt OCR replace để chỉ có một nhánh làm sạch ảnh."""
        if self.ai_blur_var.get():
            self.ocr_replace_var.set(False)

    def _sync_voice_from_settings(self):
        """Áp dụng voice mặc định mới lưu cho lần chạy pipeline kế tiếp."""
        env = load_env()
        if hasattr(self, "_speed_hint"):
            self._speed_hint.config(text=f"Tốc độ cơ bản: {env.get('TTS_SPEED') or '1.0'}×")
        self._refresh_voice_options(prefer_default=True)

    def on_show(self):
        # Trang có thể vẫn tồn tại trong lúc người dùng sửa Settings.
        self._sync_voice_from_settings()
        self._sync_subtitle_from_settings()

    def _update_subtitle_summary(self, *_):
        background = self.sub_style_vars["SUBTITLE_BG_COLOR"].get()
        if self.sub_style_vars["SUBTITLE_BG_ENABLED"].get() == "0":
            background = "Tắt nền"
        self._subtitle_summary.set(f"{self.sub_font_var.get()} · {self.sub_size_var.get()} px · Nền: {background}")

    def _sync_subtitle_from_settings(self):
        if self._subtitle_custom:
            return
        env = load_env()
        if self._work_dir:
            env = load_project_subtitles(self._work_dir, env)
        self.sub_font_var.set(env.get("SUBTITLE_FONT") or DEFAULT_FONT)
        self.sub_size_var.set(env.get("SUBTITLE_FONT_SIZE") or str(DEFAULT_SIZE))
        for key, var in self.sub_style_vars.items():
            var.set(env.get(key, STYLE_DEFAULTS[key]))

    def _subtitle_settings(self):
        style = SubtitleStyle.from_settings({key: var.get() for key, var in self.sub_style_vars.items()})
        return {"SUBTITLE_FONT": get_font(self.sub_font_var.get()).name,
                "SUBTITLE_FONT_SIZE": str(validate_size(self.sub_size_var.get())), **style.to_settings()}

    def _open_project_subtitles(self):
        from subtitle_font_ui import SubtitleFontDialog
        if getattr(self, "_project_font_dialog", None) and self._project_font_dialog.winfo_exists():
            self._project_font_dialog.lift()
            return
        def apply_selection(name, size, style):
            values = {"SUBTITLE_FONT": name, "SUBTITLE_FONT_SIZE": str(size), **style.to_settings()}
            if self._work_dir:
                save_project_subtitles(self._work_dir, values)
            self._subtitle_custom = True
            self.sub_font_var.set(name)
            self.sub_size_var.set(str(size))
            for key, var in self.sub_style_vars.items():
                var.set(values[key])
            return True
        dialog = self._project_font_dialog = SubtitleFontDialog(self.app, self._subtitle_settings(), apply_selection)
        dialog.title("Phụ đề của dự án — Auto-Dub Studio")
        dialog.grab_set()

    def _font_shortcut(self, parent):
        row = tk.Frame(parent, bg=WHITE)
        row.pack(fill="x")
        tk.Label(row, textvariable=self._subtitle_summary, font=FB, bg=WHITE, fg=TEXT).pack(side="left")
        Btn(row, "Phông, màu & nền phụ đề", command=self._open_project_subtitles,
            bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=12, pady=8).pack(side="right")

    def _subtitle_cli_args(self):
        font = get_font(self.sub_font_var.get())
        size = validate_size(self.sub_size_var.get())
        style = SubtitleStyle.from_settings({key: var.get() for key, var in self.sub_style_vars.items()})
        if self._work_dir:
            save_project_subtitles(self._work_dir, self._subtitle_settings())
        return ["--subtitle-font", font.name, "--subtitle-size", str(size), *style.cli_args()]

    def _mk_p2(self, body):
        """Tạo panel log ASR và tùy chọn burn subtitle/cỡ chữ."""
        panel = tk.Frame(body, bg=BG)
        c, _ = self._card(panel, "Đang lắng nghe video của bạn")
        self._asr_log = scrolledtext.ScrolledText(c, font=FM, bg=LOG_BG, fg=LOG_FG, height=15, relief="flat", state="disabled", wrap="word", padx=12, pady=10)
        self._asr_log.pack(fill="x")
        self._asr_st = tk.Label(panel, text="", font=FB, bg=BG, fg=MUTED, pady=6)
        self._asr_st.pack()
        
        # Thêm cấu hình vị trí phụ đề vào Bước 2
        sub_card, _ = self._card(panel, "Phụ đề khi xuất video")
        
        cb_burn = tk.Checkbutton(sub_card, text="Vẽ cứng phụ đề lên video (Burn Subtitles)",
                                 variable=self.burn_subtitles_var, font=FB, bg=WHITE, fg=TEXT,
                                 activebackground=WHITE, selectcolor=WHITE)
        cb_burn.pack(anchor="w", pady=(10, 0))
        
        self._font_shortcut(sub_card)
        
        # Nút quay lại thiết lập khi có lỗi
        self.asr_back_btn = Btn(panel, "Quay lại Thiết lập", command=lambda: self._goto(0), bg=LOG_BG)
        self.asr_back_btn.pack(pady=10)
        return panel

    def _mk_p3(self, body):
        """Tạo panel xem transcript và nhập JSON dịch thủ công."""
        panel = tk.Frame(body, bg=BG)
        
        # Preview ban đầu
        c1, _ = self._card(panel, "Lời thoại đã nhận dạng")
        pw = tk.Frame(c1, bg=BG, highlightbackground=BORDER, highlightthickness=1)
        pw.pack(fill="x")
        self._json_prev = tk.Label(pw, text="", font=FM, bg=BG, fg=TEXT, anchor="nw", justify="left", wraplength=720, padx=12, pady=12)
        self._json_prev.pack(fill="x")
        
        br = tk.Frame(c1, bg=WHITE)
        br.pack(fill="x", pady=(10, 0))
        Btn(br, "Sao chép JSON gốc", command=self._copy_json, padx=14, pady=6).pack(side="left")
        self._copy_ok = tk.Label(br, text="", font=FS, bg=WHITE, fg=SUCCESS)
        self._copy_ok.pack(side="left", padx=12)
        
        # Dịch thủ công để không phát sinh chi phí gọi API dịch.
        c2, _ = self._card(panel, "Viết bản dịch của bạn")
        tk.Label(
            c2,
            text=(
                "Hãy giữ nguyên id, text, start, end, duration và điền bản dịch "
                "vào trường text_vi hoặc text_jp của từng phân đoạn. "
                "Nên viết ngắn gọn để vừa thời lượng đọc."
            ),
            font=FS,
            bg=WHITE,
            fg=MUTED,
            justify="left",
            wraplength=760,
        ).pack(anchor="w", pady=(0, 8))
        self._translation_status = tk.Label(
            c2,
            text="Đang chuẩn bị mẫu JSON dịch...",
            font=FS,
            bg=WHITE,
            fg=PRIMARY,
            anchor="w",
        )
        self._translation_status.pack(fill="x", pady=(0, 8))
        
        self._trans_in = scrolledtext.ScrolledText(c2, font=FM, bg=BG, fg=TEXT, height=14, relief="flat", wrap="word", padx=16, pady=14, insertbackground=PRIMARY, highlightthickness=1, highlightbackground=BORDER, highlightcolor=PRIMARY)
        self._trans_in.pack(fill="x")
        self._trans_in.bind("<<Modified>>", self._translation_changed)

        copy_row = tk.Frame(c2, bg=WHITE)
        copy_row.pack(fill="x", pady=(10, 0))
        Btn(copy_row, "Sao chép mẫu JSON dịch", command=self._copy_translation_template, padx=14, pady=6).pack(side="left")
        Btn(copy_row, "Lịch sử bản dịch", command=self._open_translation_history,
            bg=WHITE, fg=PRIMARY, font=FS, padx=12, pady=6).pack(side="left", padx=8)

        bf = tk.Frame(panel, bg=BG)
        bf.pack(fill="x", padx=40, pady=(16, 24))
        Btn(bf, "Tạo giọng đọc & xuất video  →", command=self._start_tts).pack(side="left")
        return panel



    def _mk_p4(self, body):
        """Tạo panel log quá trình TTS và ghép nhạc nền."""
        panel = tk.Frame(body, bg=BG)
        c, _ = self._card(panel, "Đang tạo giọng đọc và hoàn thiện video")
        self._tts_log = scrolledtext.ScrolledText(c, font=FM, bg=LOG_BG, fg=LOG_FG, height=15, relief="flat", state="disabled", wrap="word", padx=12, pady=10)
        self._tts_log.pack(fill="x")
        self._tts_st = tk.Label(panel, text="", font=FB, bg=BG, fg=MUTED, pady=6)
        self._tts_st.pack()
        
        # Nút quay lại chỉnh sửa dịch thuật
        self.tts_back_btn = Btn(panel, "Quay lại Dịch thuật", command=lambda: self._goto(2), bg=LOG_BG)
        self.tts_back_btn.pack(pady=10)
        return panel

    def _mk_p5(self, body):
        panel = tk.Frame(body, bg=BG)
        card, _ = self._card(panel)
        tk.Label(card, text="✓", font=(FF, 42), bg=WHITE, fg=SUCCESS).pack(pady=(18, 12))
        tk.Label(card, text="Một phiên bản mới đã sẵn sàng.", font=FH1, bg=WHITE, fg=TEXT).pack()
        tk.Label(card, text="Video đã được lồng tiếng và kết xuất thành công.", font=FB,
                 bg=WHITE, fg=MUTED).pack(pady=(8, 20))
        self._done_lbl = tk.Label(card, text="", font=FS, bg=WHITE, fg=MUTED, wraplength=680)
        self._done_lbl.pack(pady=(0, 16))
        actions = tk.Frame(card, bg=WHITE)
        actions.pack(pady=(0, 18))
        Btn(actions, "▶  Xem video", command=self._play_video, padx=18).pack(side="left", padx=5)
        Btn(actions, "Tải video xuống", command=self._download_video, bg=SIDEBAR,
            fg=TEXT, padx=18).pack(side="left", padx=5)
        more = tk.Frame(card, bg=WHITE)
        more.pack(pady=(0, 18))
        Btn(more, "＋  Tạo video khác", command=self.app.reset_new, bg=WHITE,
            fg=TEXT, font=FS).pack(side="left", padx=5)
        Btn(more, "Về thư viện", command=lambda: self.app._show("projects"), bg=WHITE,
            fg=MUTED, font=FS).pack(side="left", padx=5)
        return panel

    def _goto(self, step):
        for panel in self._p:
            panel.pack_forget()
        self._p[step].pack(fill="x")
        self._sf.reset_scroll()
        for i, label in enumerate(self._slbls):
            if i < step:
                label.config(text=f"✓  {STEPS[i]}", bg=SUCCESS_BG, fg=SUCCESS, font=(FF, 10, "bold"))
            elif i == step:
                label.config(text=f"{i+1:02d}  {STEPS[i]}", bg=PRIMARY_LIGHT, fg=PRIMARY, font=(FF, 10, "bold"))
            else:
                label.config(text=f"{i+1:02d}  {STEPS[i]}", bg=STEP_IDLE, fg=MUTED, font=(FF, 10))

    def _pick_file(self):
        """Mở hộp chọn file và đưa đường dẫn video vào form."""
        path = filedialog.askopenfilename(
            title="Chọn video đầu vào",
            filetypes=[("Video File", "*.mp4 *.mkv *.webm *.mov *.avi"), ("All Files", "*.*")])
        if path:
            self.file_var.set(path)

    def _start_asr(self):
        """Khởi chạy pipeline ASR trong thread, đồng thời stream log lên GUI."""
        url    = self.url_var.get().strip()
        fpath  = self.file_var.get().strip()
        target = self.target_lang_var.get()
        try:
            subtitle_options = self._subtitle_cli_args()
        except (ValueError, OSError) as error:
            messagebox.showerror("Phông phụ đề", str(error))
            return
        
        if not url and not fpath:
            messagebox.showwarning("Nhập liệu trống", "Vui lòng dán link URL hoặc chọn file video từ máy tính.")
            return
            
        script_name = "pipeline_vi.py" if "Việt" in target else "pipeline.py"
        cmd = [PYTHON, str(BASE_DIR / script_name)]
        cmd += subtitle_options
        
        if url:   cmd += ["--url", url]
        if fpath: cmd += ["--file", fpath]
        
        cmd += ["--source-lang", self.lang_var.get(),
                "--voice",       self.voice_var.get(),
                "--bg-mode",     self.bg_var.get()]
        if self.burn_subtitles_var.get():
            cmd += ["--burn-subtitles"]
        if "Việt" in target and self.ocr_replace_var.get():
            cmd += ["--ocr-replace"]
            
        if self.ai_blur_var.get():
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
        """Kiểm tra JSON dịch, lưu file rồi khởi chạy pipeline resume cho TTS."""
        raw = self._trans_in.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning("Thiếu dữ liệu", "Vui lòng điền JSON bản dịch thủ công vào khung văn bản.")
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
        target_field = "text_vi" if is_vi else "text_jp"

        # Kiểm tra trước khi ghi file/chạy TTS để không tạo audio từ JSON
        # thiếu câu hoặc sai timeline.
        source_path = self._work_dir / "transcript_original.json"
        try:
            source_items = json.loads(source_path.read_text(encoding="utf-8"))
        except Exception as exc:
            messagebox.showerror("Lỗi", f"Không đọc được transcript gốc:\n{exc}")
            return
        if not isinstance(source_items, list):
            messagebox.showerror("Lỗi", "transcript_original.json phải là một mảng JSON các phân đoạn.")
            return
        from src.translate_pending import has_complete_translation
        if not has_complete_translation(data, target_field, source_items):
            messagebox.showwarning(
                "Bản dịch chưa hoàn tất",
                (
                    f"Cần một mảng JSON có đủ {len(source_items) if isinstance(source_items, list) else 'tất cả'} "
                    f"phân đoạn và trường {target_field} không rỗng ở mọi phân đoạn.\n\n"
                    "Giữ nguyên id/start/end/duration rồi chỉ điền phần dịch."
                ),
            )
            return
        
        try:
            subtitle_options = self._subtitle_cli_args()
        except (ValueError, OSError) as error:
            messagebox.showerror("Phông phụ đề", str(error))
            return

        # Ghi file dịch
        trans_file_name = "transcript_vi.json" if is_vi else "transcript_jp.json"
        vi_path = self._work_dir / trans_file_name
        vi_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        store = Store()
        project_id = store.sync_project(self._work_dir, "vi" if is_vi else "jp")
        store.save_draft(project_id, raw, "vi" if is_vi else "jp", origin="tts-submit", dirty=False)
        self._draft_last_text = self._trans_in.get("1.0", "end-1c")
        
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
        if is_vi and self.ocr_replace_var.get():
            cmd += ["--ocr-replace"]
        cmd += ["--subtitle-y", self.sub_y_var.get()]
        cmd += subtitle_options
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
                           highlightthickness=0, cursor="sb_v_double_arrow", bg=LOG_BG)
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
        status_lbl = tk.Label(dialog, textvariable=status_var, font=FB, bg=WHITE, fg=PRIMARY, pady=8)
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
        Btn(ft, "↻  Đổi khung hình", command=randomize_frame, bg=PRIMARY, fg=WHITE, padx=12, pady=8).pack(side="left", padx=(10, 0))
        Btn(ft, "Hủy", command=dialog.destroy, bg=SIDEBAR, fg=TEXT, padx=14, pady=8).pack(side="right")

        load_and_render_frame()

        dialog.update_idletasks()
        sw = dialog.winfo_screenwidth()
        sh = dialog.winfo_screenheight()
        dw = dialog.winfo_width()
        dh = dialog.winfo_height()
        dialog.geometry(f"+{(sw - dw) // 2}+{(sh - dh) // 2}")

    def _start_poll(self):
        """Bắt đầu vòng polling hàng đợi log nếu chưa chạy."""
        if not self._polling:
            self._polling = True
            self._poll()

    def _poll(self):
        """Đọc sự kiện từ worker và cập nhật log/trạng thái trên main thread."""
        stop = False
        try:
            while True:
                tag, data = self._q.get_nowait()
                if   tag == "log_asr":  self._wlog(self._asr_log, data)
                elif tag == "log_tts":  self._wlog(self._tts_log, data)
                elif tag == "asr_done":
                    self._work_dir = data
                    save_project_subtitles(self._work_dir, self._subtitle_settings())
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
        items = []
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

        self._loading_translation = True
        # Tải bản dịch đã có (nếu có) và ghép vào mẫu. Nếu chưa có, tạo mẫu
        # với trường đích rỗng để người dùng tự dịch ngay trên desktop.
        self._trans_in.delete("1.0", "end")
        target = self.target_lang_var.get()
        is_vi = "Việt" in target
        target_field = "text_vi" if is_vi else "text_jp"
        trans_file_name = "transcript_vi.json" if is_vi else "transcript_jp.json"
        trans_path = self._work_dir / trans_file_name if self._work_dir else None
        from src.translate_pending import build_translation_template, has_complete_translation
        template_items = build_translation_template(items, target_field) if isinstance(items, list) else []
        existing_items = None
        if trans_path and trans_path.exists():
            try:
                existing_items = json.loads(trans_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                existing_items = None

        if isinstance(existing_items, list) and isinstance(items, list):
            existing_by_id = {
                item.get("id"): item
                for item in existing_items
                if isinstance(item, dict) and item.get("id") is not None
            }
            for item in template_items:
                saved = existing_by_id.get(item.get("id"))
                if isinstance(saved, dict) and isinstance(saved.get(target_field), str):
                    item[target_field] = saved[target_field]

        if template_items:
            self._trans_in.insert(
                "1.0", json.dumps(template_items, ensure_ascii=False, indent=2)
            )
            is_complete = has_complete_translation(existing_items, target_field, items)
            if is_complete:
                self._translation_status.config(
                    text="Đã nạp bản dịch đã lưu. Bạn có thể rà soát/chỉnh sửa rồi chạy TTS.",
                    fg=SUCCESS,
                )
            else:
                self._translation_status.config(
                    text=(
                        "Mẫu đã sẵn sàng. Hãy tự dịch tất cả trường "
                        f"{target_field}; hệ thống không gọi API dịch."
                    ),
                    fg=PRIMARY,
                )
        else:
            self._translation_status.config(
                text="Không tạo được mẫu dịch vì transcript gốc không phải mảng JSON.",
                fg=DANGER,
            )
        if self._work_dir:
            language = "vi" if is_vi else "jp"
            store = Store()
            project_id = store.sync_project(self._work_dir, language)
            draft = store.draft(project_id)
            if draft and draft["dirty"]:
                self._trans_in.delete("1.0", "end")
                self._trans_in.insert("1.0", draft["content"])
                self._translation_status.config(text="Đã khôi phục bản nháp từ SQLite. Rà soát rồi chạy TTS khi bản dịch hoàn tất.", fg=SUCCESS)
        self._trans_in.edit_modified(False)
        self._draft_last_text = self._trans_in.get("1.0", "end-1c")
        self._loading_translation = False
        self._refresh_voice_options()

        # Luôn dừng ở bước dịch để người dùng tự hoàn thiện nội dung.
        self._goto(2)

    def _translation_changed(self, _event=None):
        if not self._trans_in.edit_modified():
            return
        self._trans_in.edit_modified(False)
        if self._loading_translation or not self._work_dir:
            return
        if self._draft_after is not None:
            self.after_cancel(self._draft_after)
        self._draft_after = self.after(900, self.flush_translation_draft)

    def flush_translation_draft(self, origin="editor"):
        if self._draft_after is not None:
            self.after_cancel(self._draft_after)
            self._draft_after = None
        if not self._work_dir or not hasattr(self, "_trans_in"):
            return
        content = self._trans_in.get("1.0", "end-1c")
        if content == self._draft_last_text:
            return
        language = "vi" if "Việt" in self.target_lang_var.get() else "jp"
        store = Store()
        project_id = store.sync_project(self._work_dir, language)
        valid = store.save_draft(project_id, content, language, origin=origin)
        self._draft_last_text = content
        self._translation_status.config(text="Đã tự lưu bản dịch vào SQLite." if valid else
            "Đã tự lưu bản nháp vào SQLite; hoàn thiện JSON và các câu dịch trước khi chạy TTS.", fg=SUCCESS if valid else MUTED)

    def _open_translation_history(self):
        if not self._work_dir:
            return
        self.flush_translation_draft()
        language = "vi" if "Việt" in self.target_lang_var.get() else "jp"
        project_id = Store().sync_project(self._work_dir, language)
        from storage_ui import TranslationHistoryDialog
        def restore(content):
            self.flush_translation_draft()
            self._trans_in.delete("1.0", "end")
            self._trans_in.insert("1.0", content)
            self.flush_translation_draft(origin="restore")
        TranslationHistoryDialog(self, project_id, restore)

    def destroy(self):
        self.flush_translation_draft()
        super().destroy()

    def load_project(self, proj):
        """Tải dữ liệu từ dự án chưa hoàn thành để tiếp tục từ bước dịch thuật."""
        self._work_dir = proj["path"]
        self._subtitle_custom = False
        self._sync_subtitle_from_settings()
        # Đặt ngôn ngữ đích đúng
        if proj["lang"] == "VI":
            self.target_lang_var.set("Tiếng Việt (VI)")
        else:
            self.target_lang_var.set("Tiếng Nhật (JP)")
        # Gọi hàm chuẩn để điền dữ liệu và chuyển trang
        self._prep_translate()

    def _copy_json(self):
        """Sao chép transcript gốc đang hiển thị vào clipboard."""
        if not self._full_json:
            messagebox.showwarning("Trống dữ liệu", "Không tìm thấy nội dung JSON nguồn.")
            return
        self.clipboard_clear()
        self.clipboard_append(self._full_json)
        self._copy_ok.config(text="Đã sao chép vào Clipboard!")
        self.after(2000, lambda: self._copy_ok.config(text=""))

    def _copy_translation_template(self):
        """Sao chép mẫu JSON dịch hiện tại vào clipboard."""
        raw = self._trans_in.get("1.0", "end").strip()
        if not raw:
            messagebox.showwarning("Trống dữ liệu", "Chưa có mẫu JSON dịch để sao chép.")
            return
        self.clipboard_clear()
        self.clipboard_append(raw)
        self._copy_ok.config(text="Đã sao chép mẫu dịch vào Clipboard!")
        self.after(2000, lambda: self._copy_ok.config(text=""))

    def _go_done(self):
        """Hiển thị file đầu ra, tạo thumbnail và chuyển wizard sang Hoàn tất."""
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
        """Mở video kết quả bằng trình phát mặc định trên macOS."""
        if not self._dubbed or not self._dubbed.exists():
            messagebox.showerror("Lỗi", "Không tìm thấy video đích đã xử lý.")
            return
        try:
            subprocess.Popen(["open", str(self._dubbed)])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể mở video:\n{e}")

    def _download_video(self):
        """Sao chép video kết quả tới thư mục do người dùng chọn."""
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
    """Trang chỉnh sửa API, voice và các tham số dùng chung của pipeline."""

    _GROUPS = [
        ("Azure Speech API (Cần thiết cho ASR & TTS tiếng Nhật)", [
            ("AZURE_SPEECH_KEY",    "Speech API Key",  True),
            ("AZURE_SPEECH_REGION", "Region của dịch vụ", False),
            ("TTS_VOICE",           "Tên giọng Nhật Azure (ja-JP-...)", False),
        ]),
        ("LucyLab API — Tiếng Việt (Chỉ dùng nếu lồng tiếng Việt)", [
            ("VIETNAMESE_API_KEY",        "LucyLab API Key", True),
            ("VIETNAMESE_API_KEY_2",      "LucyLab API Key dự phòng #2", True),
            ("VIETNAMESE_API_KEY_3",      "LucyLab API Key dự phòng #3", True),
            ("VIETNAMESE_API_KEY_4",      "LucyLab API Key dự phòng #4", True),
            ("VIETNAMESE_API_KEY_5",      "LucyLab API Key dự phòng #5", True),
            ("VIETNAMESE_VOICEID_MALE",   "Mã giọng Nam (Male Voice ID)", False),
            ("VIETNAMESE_VOICEID_FEMALE", "Mã giọng Nữ (Female Voice ID)", False),
            ("LUCYLAB_API_URL",           "Đường dẫn LucyLab API URL", False),
            ("AZURE_VI_TTS_VOICE",        "Giọng Việt Azure dự phòng (vi-VN-...)", False),
            ("VOICE_TYPE",                "Giọng mặc định Việt (male/female)", False),
        ]),
        ("Google Gemini API (Tuỳ chọn — Tạo mô tả & ảnh thu nhỏ)", [
            ("GOOGLE_API_KEY", "Gemini API Key", True),
            ("IMAGE_MODEL_ID", "Model tạo ảnh (Image Model)", False),
            ("CONTENT_MODEL_ID", "Model văn bản (Content Model)", False),
        ]),

        ("Cấu hình xử lý tệp & thông số chung", [
            ("DEFAULT_SOURCE_LANG", "Mã ngôn ngữ nguồn mặc định", False),
            ("AUDIO_SAMPLE_RATE",   "Tần số lấy mẫu âm thanh (Hz)", False),
            ("TTS_SPEED",          "Tốc độ đọc cơ bản (0.5× – 2.0×)", False),
            ("TTS_OVERFLOW_MAX_SPEED", "Tốc độ tối đa khi câu dài (0.5× – 2.0×)", False),
            ("OUTPUT_DIR",          "Thư mục chứa đầu ra mặc định", False),
        ]),
        ("Phông chữ phụ đề", []),
    ]

    def __init__(self, parent, app):
        """Tải cấu hình hiện tại và dựng form chỉnh sửa .env."""
        super().__init__(parent, bg=BG)
        self.app   = app
        self._vars = {}
        self._loading = True
        self._autosave_after = None
        self._status_after = None
        self._dirty = False
        self._build()
        self._loading = False

    def _build(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill="x", padx=32, pady=(24, 23))
        heading(hdr, "CÀI ĐẶT STUDIO", "Thiết lập một lần. Sáng tạo mỗi ngày.",
                "Kết nối dịch vụ và chọn cấu hình phù hợp với bạn.").pack(anchor="w")
        actions = tk.Frame(self, bg=BG)
        actions.pack(fill="x", padx=32, pady=(0, 14))
        Btn(actions, "＋  Thư viện giọng đọc", command=self.app.open_voice_library,
            bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=13, pady=7).pack(side="left")
        Btn(actions, "Dữ liệu SQLite & sao lưu", command=self.app.open_database,
            bg=WHITE, fg=TEXT, font=FS, padx=13, pady=7).pack(side="left", padx=8)
        tabs = tk.Frame(self, bg=BG)
        tabs.pack(fill="x", padx=32, pady=(0, 18))
        self._group_buttons, self._group_panels = [], []
        names = ["Azure Speech", "Giọng Việt · LucyLab", "Gemini AI", "Xử lý video", "Phông phụ đề"]
        descriptions = [
            ("Nhận dạng lời nói & giọng Nhật", "Kết nối Azure để nhận dạng lời thoại và tổng hợp giọng tiếng Nhật."),
            ("Giọng đọc tiếng Việt", "Chọn giọng LucyLab, khóa dự phòng và giọng Azure thay thế."),
            ("Nội dung & hình ảnh", "Gemini hỗ trợ tạo tiêu đề, mô tả và ảnh thu nhỏ cho video."),
            ("Tùy chỉnh quy trình", "Điều chỉnh âm thanh, ngôn ngữ mặc định và vị trí lưu kết quả."),
            ("Phông chữ cho video của bạn", "Chọn kiểu chữ, cỡ phụ đề và xem trước ngay tại đây."),
        ]
        for i, name in enumerate(names):
            tabs.columnconfigure(i, weight=1, uniform="settings_tab")
            button = Btn(tabs, name, command=lambda index=i: self._select_group(index),
                         bg=TEXT if i == 0 else BG, fg=WHITE if i == 0 else MUTED,
                         font=FS, padx=15, pady=9)
            button.grid(row=0, column=i, sticky="ew", padx=(0, 6))
            self._group_buttons.append(button)
        self._settings_sf = sf = ScrollFrame(self, bg=BG)
        sf.outer.pack(fill="both", expand=True)
        env = load_env()
        for index, (_, fields) in enumerate(self._GROUPS):
            panel = tk.Frame(sf.inner, bg=BG)
            self._group_panels.append(panel)
            card = tk.Frame(panel, bg=WHITE, highlightbackground=BORDER,
                            highlightthickness=1, padx=24, pady=22)
            card.pack(fill="x", padx=32, pady=(0, 18))
            title, description = descriptions[index]
            tk.Label(card, text=title, font=FH2, bg=WHITE, fg=TEXT).pack(anchor="w")
            tk.Label(card, text=description, font=FS, bg=WHITE, fg=MUTED,
                     wraplength=730, justify="left").pack(anchor="w", pady=(6, 19))
            flat_sep(card).pack(fill="x", pady=(0, 15))
            if index == 4:
                from subtitle_font_ui import SubtitleFontPanel
                for key, default in SUBTITLE_DEFAULTS.items():
                    variable = tk.StringVar(value=env.get(key) or default)
                    self._vars[key] = variable
                    variable.trace_add("write", self._on_setting_changed)
                self._font_panel = SubtitleFontPanel(card, self._vars["SUBTITLE_FONT"], self._vars["SUBTITLE_FONT_SIZE"],
                    style_vars={key: self._vars[key] for key in STYLE_DEFAULTS})
                self._font_panel.pack(fill="x")
            grid = tk.Frame(card, bg=WHITE)
            grid.pack(fill="x")
            for column in range(2):
                grid.columnconfigure(column, weight=1, uniform="setting")
            for i, (key, label, secret) in enumerate(fields):
                cell = tk.Frame(grid, bg=WHITE)
                cell.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0, 18) if i % 2 == 0 else (0, 0), pady=(0, 18))
                tk.Label(cell, text=label, font=FS, bg=WHITE, fg=MUTED,
                         anchor="w").pack(anchor="w", pady=(0, 7))
                row = tk.Frame(cell, bg=WHITE)
                row.pack(fill="x")
                default = {"TTS_SPEED": "1.0", "TTS_OVERFLOW_MAX_SPEED": "1.4"}.get(key, "")
                variable = tk.StringVar(value=env.get(key) or default)
                self._vars[key] = variable
                variable.trace_add("write", self._on_setting_changed)
                field = entry(row, variable, secret=secret)
                field.config(width=12)
                field.pack(side="left", fill="x", expand=True)
                field.bind("<FocusOut>", lambda e: self.flush_autosave(), add="+")
                if secret:
                    toggle = Btn(row, "Hiện", bg=WHITE, fg=PRIMARY, font=(FF, 9), padx=8, pady=10)
                    toggle.pack(side="left", padx=(5, 0))
                    def show_secret(widget=field, button=toggle):
                        hidden = bool(widget.cget("show"))
                        widget.config(show="" if hidden else "•")
                        button.config(text="Ẩn" if hidden else "Hiện")
                    toggle.config(command=show_secret)
            if index in (0, 1):
                Btn(card, "▶  Nghe thử giọng Nhật" if index == 0 else "▶  Nghe thử giọng Việt",
                    command=lambda lang="jp" if index == 0 else "vi": self.app.open_voice_preview(lang),
                    bg=PRIMARY_LIGHT, fg=PRIMARY, font=FS, padx=14).pack(anchor="w", pady=(2, 0))
            elif index == 3:
                tk.Label(card, text=("1.0× là tốc độ tự nhiên. Câu dài chỉ tăng đến mức tối đa; "
                         "nếu vẫn dư thì chồng lời tại mốc gốc. Nếu mức tối đa thấp hơn tốc độ cơ bản, "
                         "hệ thống giữ tốc độ cơ bản. Bản dịch và mốc phụ đề được giữ nguyên."),
                         font=FS, bg=WHITE, fg=MUTED, wraplength=710, justify="left").pack(anchor="w")
        footer = tk.Frame(self, bg=BG)
        footer.pack(fill="x", padx=32, pady=(12, 20))
        flat_sep(footer).pack(fill="x", pady=(0, 12))
        Btn(footer, "Lưu thay đổi", command=self._save, padx=22).pack(side="right")
        tk.Label(footer, text="●  Tự động lưu khi bạn ngừng nhập", font=FS,
                 bg=BG, fg=MUTED).pack(side="left")
        self._saved_lbl = tk.Label(footer, text="", font=FS, bg=BG, fg=SUCCESS)
        self._saved_lbl.pack(side="left", padx=15)
        self._select_group(0)

    def _select_group(self, index):
        for i, panel in enumerate(self._group_panels):
            panel.pack_forget()
            self._group_buttons[i].config(bg=TEXT if i == index else BG,
                                          fg=WHITE if i == index else MUTED)
        self._group_panels[index].pack(fill="x")
        self._settings_sf.reset_scroll()

    def _test_gemini(self):
        """Gửi một prompt nhỏ để kiểm tra Gemini với key đang nhập."""
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

    def _on_setting_changed(self, *_args):
        """Debounce việc ghi để gõ API key không ghi lại .env sau từng ký tự."""
        if self._loading:
            return
        self._dirty = True
        if self._autosave_after is not None:
            try:
                self.after_cancel(self._autosave_after)
            except tk.TclError:
                pass
        self._saved_lbl.config(text="Đang chờ lưu thay đổi...", fg=WARN)
        self._autosave_after = self.after(650, lambda: self._save(automatic=True))

    def _save(self, automatic: bool = False):
        """Persist every visible setting, including an intentional blank value."""
        if self._autosave_after is not None:
            try:
                self.after_cancel(self._autosave_after)
            except tk.TclError:
                pass
            self._autosave_after = None

        # Ghi cả giá trị rỗng để khi thay/xóa API key, file .env thật sự được
        # cập nhật thay vì âm thầm giữ lại key cũ.
        data = {k: v.get().strip() for k, v in self._vars.items()}
        from src.tts_speed import validate_speed
        try:
            for key in ("TTS_SPEED", "TTS_OVERFLOW_MAX_SPEED"):
                data[key] = f"{validate_speed(data[key]):g}"
            data["SUBTITLE_FONT"] = get_font(data["SUBTITLE_FONT"]).name
            data["SUBTITLE_FONT_SIZE"] = str(validate_size(data["SUBTITLE_FONT_SIZE"]))
            data.update(SubtitleStyle.from_settings(data).to_settings())
        except (ValueError, OSError) as error:
            self._saved_lbl.config(text=str(error), fg=DANGER)
            return False
        save_env(data)
        self._dirty = False

        status = "Đã tự động lưu vào .env" if automatic else "Đã cập nhật tệp tin cấu hình!"
        self._saved_lbl.config(text=status, fg=SUCCESS)
        if self._status_after is not None:
            try:
                self.after_cancel(self._status_after)
            except tk.TclError:
                pass
        self._status_after = self.after(2500, lambda: self._saved_lbl.config(text=""))

        # Cập nhật voice đang hiển thị trên trang tạo dự án đã được khởi tạo.
        new_page = self.app._pages.get("new")
        if new_page and hasattr(new_page, "_sync_voice_from_settings"):
            new_page._sync_voice_from_settings()
            new_page._sync_subtitle_from_settings()
        return True

    def flush_autosave(self):
        """Ghi ngay các thay đổi còn chờ khi người dùng rời trang Settings."""
        if getattr(self, "_dirty", False):
            return self._save(automatic=True)
        return True

    def on_show(self):
        """Nạp lại giá trị .env khi người dùng mở lại trang Settings."""
        env = load_env()
        self._loading = True
        try:
            for k, v in self._vars.items():
                default = {"TTS_SPEED": "1.0", "TTS_OVERFLOW_MAX_SPEED": "1.4",
                           **SUBTITLE_DEFAULTS}.get(k, "")
                v.set(env.get(k) or default)
        finally:
            self._loading = False


if __name__ == "__main__":
    if not HAS_PIL:
        print("[INFO] Thư viện Pillow chưa cài - Ảnh xem trước (thumbnail) sẽ không hiển thị.")
        print("       Cài đặt bằng lệnh: pip install Pillow")
        
    app = App()
    app.mainloop()
