"""Voice library and local database management windows."""

import sqlite3
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, ttk

from studio_ui import BG, WHITE, TEXT, MUTED, PRIMARY, PRIMARY_LIGHT, SUCCESS, DANGER, BORDER, FH2, FB, FS, Btn, entry
from src.storage import Store


class VoiceLibraryDialog(tk.Toplevel):
    def __init__(self, parent, on_change, on_preview, on_default):
        super().__init__(parent, bg=BG)
        self.title("Thư viện giọng đọc — SQLite")
        self.geometry("1000x680")
        self.minsize(900, 620)
        self.transient(parent)
        self.on_change, self.on_preview, self.on_default = on_change, on_preview, on_default
        self.reference = None
        header = tk.Frame(self, bg=BG, padx=24, pady=20)
        header.pack(fill="x")
        tk.Label(header, text="Giọng kể của riêng bạn.", font=FH2, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(header, text="Thêm Voice ID từ LucyLab hoặc Azure đã có quyền sử dụng. Lựa chọn được lưu trong SQLite.",
                 font=FS, bg=BG, fg=MUTED).pack(anchor="w", pady=(7, 0))
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=24)
        listing = tk.Frame(body, bg=BG)
        listing.pack(side="left", fill="both", expand=True, padx=(0, 20))
        self.tree = ttk.Treeview(listing, columns=("language", "provider", "state"), show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Giọng đọc")
        self.tree.column("#0", width=185, minwidth=130)
        for key, text, width in (("language", "Ngôn ngữ", 70), ("provider", "Dịch vụ", 70), ("state", "Trạng thái", 100)):
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, minwidth=60, stretch=False)
        scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select)
        actions = tk.Frame(listing, bg=BG)
        actions.pack(fill="x", pady=(12, 0))
        Btn(actions, "＋  Thêm mới", command=self._new, font=FS, padx=12).pack(side="left")
        Btn(actions, "▶  Nghe thử", command=self._preview, bg=WHITE, fg=PRIMARY, font=FS, padx=12).pack(side="left", padx=6)
        Btn(actions, "Đặt mặc định", command=self._default, bg=WHITE, fg=TEXT, font=FS, padx=12).pack(side="left")
        form = tk.Frame(body, bg=WHITE, padx=20, pady=18, highlightthickness=1, highlightbackground=BORDER)
        form.pack(side="right", fill="y")
        self.variables = {key: tk.StringVar(value=value) for key, value in {
            "name": "", "language": "vi", "provider": "lucylab", "provider_voice_id": "", "gender": "other", "notes": ""}.items()}
        for key, label in (("name", "Tên giọng"), ("language", "Ngôn ngữ: vi / jp"),
                           ("provider", "Nhà cung cấp"), ("provider_voice_id", "Voice ID"),
                           ("gender", "Giới tính: male / female / other"), ("notes", "Ghi chú")):
            tk.Label(form, text=label, font=FS, bg=WHITE, fg=MUTED).pack(anchor="w", pady=(0, 6))
            choices = {"language": ["vi", "jp"], "provider": ["lucylab", "azure"], "gender": ["male", "female", "other"]}
            if key in choices:
                field = ttk.Combobox(form, textvariable=self.variables[key], values=choices[key],
                                     state="readonly", width=27, style="Studio.TCombobox")
            else:
                field = entry(form, self.variables[key])
                field.config(width=28)
            field.pack(fill="x", pady=(0, 14))
        self.save_button = Btn(form, "Lưu giọng vào SQLite", command=self._save, padx=14)
        self.save_button.pack(fill="x", pady=(4, 7))
        self.hide_button = Btn(form, "Ẩn giọng", command=self._hide, bg=WHITE, fg=DANGER, font=FS)
        self.hide_button.pack(fill="x")
        self.status = tk.Label(self, text="Chọn một giọng để quản lý, hoặc bấm Thêm mới.", font=FS,
                               bg=BG, fg=MUTED, wraplength=930, justify="left", anchor="w")
        self.status.pack(fill="x", padx=24, pady=18)
        self.refresh()

    def refresh(self, selected=None):
        self.tree.delete(*self.tree.get_children())
        settings = Store().settings()
        self.records = {voice["id"]: voice for voice in Store().voices(include_disabled=True)}
        for reference, voice in self.records.items():
            default = settings.get("DEFAULT_VOICE_VI" if voice["language"] == "vi" else "DEFAULT_VOICE_JP")
            state = "Đã ẩn" if not voice["enabled"] else "Mặc định" if default == reference else "Có sẵn" if voice["builtin"] else "Của bạn"
            self.tree.insert("", "end", iid=reference, text=voice["name"],
                values=("Tiếng Việt" if voice["language"] == "vi" else "Tiếng Nhật", voice["provider"], state))
        if selected in self.records:
            self.tree.selection_set(selected)
            self.tree.see(selected)

    def _select(self, _event=None):
        selection = self.tree.selection()
        if not selection:
            return
        self.reference = selection[0]
        voice = self.records[self.reference]
        for key, variable in self.variables.items():
            variable.set(voice.get(key) or "")
        self.save_button.config(state="disabled" if voice["builtin"] else "normal")
        self.hide_button.config(state="disabled" if voice["builtin"] else "normal")
        self.status.config(text="Giọng có sẵn. Bấm Thêm mới để nhập một Voice ID khác." if voice["builtin"]
                           else "Chỉnh sửa và lưu; giọng đã ẩn sẽ được khôi phục khi lưu.", fg=MUTED)

    def _new(self):
        self.reference = None
        self.tree.selection_remove(*self.tree.selection())
        for key, value in {"name": "", "provider_voice_id": "", "notes": ""}.items():
            self.variables[key].set(value)
        self.save_button.config(state="normal")
        self.hide_button.config(state="disabled")
        self.status.config(text="Nhập tên, dịch vụ và Voice ID rồi lưu vào SQLite.", fg=MUTED)

    def _save(self):
        try:
            reference = Store().save_voice(**{key: value.get() for key, value in self.variables.items()}, reference=self.reference)
            self.on_change()
            self.refresh(reference)
            self.status.config(text="Đã lưu giọng. Bạn có thể nghe thử hoặc đặt làm mặc định.", fg=SUCCESS)
        except (ValueError, sqlite3.Error) as error:
            self.status.config(text=str(error), fg=DANGER)

    def _selected(self):
        selection = self.tree.selection()
        if not selection:
            self.status.config(text="Chọn một giọng trong danh sách trước.", fg=DANGER)
            return None
        return self.records[selection[0]]

    def _preview(self):
        voice = self._selected()
        if voice and voice["enabled"]:
            self.on_preview(voice["language"], voice["id"])

    def _default(self):
        voice = self._selected()
        if voice and voice["enabled"]:
            self.on_default(voice["language"], voice["id"])
            self.refresh(voice["id"])
            self.status.config(text="Đã đặt giọng mặc định cho ngôn ngữ này.", fg=SUCCESS)

    def _hide(self):
        voice = self._selected()
        if voice:
            try:
                Store().archive_voice(voice["id"])
                self.on_change()
                self.refresh(voice["id"])
                self.status.config(text="Đã ẩn giọng. Chọn giọng này và lưu để khôi phục.", fg=SUCCESS)
            except ValueError as error:
                self.status.config(text=str(error), fg=DANGER)


class DatabaseDialog(tk.Toplevel):
    def __init__(self, parent, on_import):
        super().__init__(parent, bg=BG)
        self.title("Dữ liệu SQLite — Auto-Dub Studio")
        self.geometry("900x700")
        self.minsize(780, 620)
        self.transient(parent)
        self.on_import = on_import
        body = tk.Frame(self, bg=BG, padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Dữ liệu của studio.", font=FH2, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(body, text=str(Store().path), font=FS, bg=BG, fg=MUTED, wraplength=820, justify="left").pack(anchor="w", pady=(8, 15))
        self.summary = tk.Label(body, text="", font=FB, bg=WHITE, fg=TEXT, padx=16, pady=15,
                                justify="left", anchor="w")
        self.summary.pack(fill="x")
        tk.Label(body, text="Lịch sử xử lý gần đây", font=FS, bg=BG, fg=MUTED).pack(anchor="w", pady=(18, 8))
        self.runs = ttk.Treeview(body, columns=("project", "status", "time"), show="headings", height=7)
        for key, label, width in (("project", "Dự án", 300), ("status", "Trạng thái", 140), ("time", "Bắt đầu (UTC)", 230)):
            self.runs.heading(key, text=label)
            self.runs.column(key, width=width)
        self.runs.pack(fill="both", expand=True)
        actions = tk.Frame(body, bg=BG)
        actions.pack(fill="x", pady=(15, 12))
        Btn(actions, "Sao lưu SQLite…", command=self._backup, font=FS).pack(side="left")
        Btn(actions, "Nhập dự án từ tệp", command=self._import, bg=WHITE, fg=PRIMARY, font=FS).pack(side="left", padx=8)
        Btn(actions, "Làm mới", command=self.refresh, bg=WHITE, fg=TEXT, font=FS).pack(side="right")
        self.status = tk.Label(body, text="SQLite lưu dữ liệu quản lý và bản nháp. Sao lưu này không bao gồm video/audio hay khóa API.",
                               font=FS, bg=BG, fg=MUTED, wraplength=820, justify="left", anchor="w")
        self.status.pack(fill="x")
        self.refresh()

    def refresh(self):
        counts = Store().counts()
        self.summary.config(text=(
            f"Giọng đọc: {counts['voices']}     Cài đặt: {counts['app_settings']}     Dự án: {counts['projects']}\n"
            f"Phân đoạn lời thoại: {counts['transcript_segments']}     Bản nháp: {counts['translation_drafts']}\n"
            f"Phiên bản dịch: {counts['translation_revisions']}     Lượt chạy: {counts['runs']}\n"
            f"Tệp / cache TTS: {counts['artifacts']}     Mẫu nghe thử: {counts['voice_previews']}"))
        self.runs.delete(*self.runs.get_children())
        for run in Store().recent_runs():
            self.runs.insert("", "end", values=(run["project_name"] or "Đang khởi tạo", run["status"], run["started_at"]))

    def _backup(self):
        destination = filedialog.asksaveasfilename(parent=self, title="Chọn tệp sao lưu SQLite mới",
            initialfile="studio-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".sqlite3",
            defaultextension=".sqlite3", filetypes=[("SQLite database", "*.sqlite3")])
        if not destination:
            return
        try:
            path = Store().backup(destination)
            self.status.config(text=f"Đã sao lưu và kiểm tra toàn vẹn: {path}", fg=SUCCESS)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.status.config(text=str(error), fg=DANGER)

    def _import(self):
        try:
            count = self.on_import()
            self.refresh()
            self.status.config(text=f"Đã nhập/cập nhật {count} dự án. Bản nháp đang sửa và tệp gốc được giữ lại.", fg=SUCCESS)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.status.config(text=str(error), fg=DANGER)


class TranslationHistoryDialog(tk.Toplevel):
    def __init__(self, parent, project_id, on_restore):
        super().__init__(parent, bg=BG)
        self.title("Lịch sử bản dịch — SQLite")
        self.geometry("820x590")
        self.transient(parent.winfo_toplevel())
        self.on_restore = on_restore
        self.records = {str(row["id"]): row for row in Store().revisions(project_id)}
        body = tk.Frame(self, bg=BG, padx=20, pady=18)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="100 phiên bản gần nhất · chọn để xem và khôi phục", font=FS,
                 bg=BG, fg=MUTED).pack(anchor="w", pady=(0, 12))
        self.tree = ttk.Treeview(body, columns=("time", "origin", "valid"), show="headings", height=7)
        for key, label in (("time", "Thời điểm (UTC)"), ("origin", "Nguồn"), ("valid", "Bản dịch đầy đủ")):
            self.tree.heading(key, text=label)
        for ref, row in self.records.items():
            self.tree.insert("", "end", iid=ref, values=(row["created_at"], row["origin"], "Có" if row["valid"] else "Chưa"))
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._select)
        self.preview = tk.Text(body, bg=WHITE, fg=TEXT, font=FS, wrap="word", state="disabled", padx=10, pady=10)
        self.preview.pack(fill="both", expand=True, pady=12)
        Btn(body, "Khôi phục vào ô dịch", command=self._restore).pack(anchor="e")

    def _select(self, _event=None):
        selected = self.tree.selection()
        if selected:
            self.preview.config(state="normal")
            self.preview.delete("1.0", "end")
            self.preview.insert("1.0", self.records[selected[0]]["content"])
            self.preview.config(state="disabled")

    def _restore(self):
        selected = self.tree.selection()
        if selected:
            self.on_restore(self.records[selected[0]]["content"])
            self.destroy()
