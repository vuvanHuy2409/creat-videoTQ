"""Voice audition dialog. Generate in a worker process; play without blocking Tk."""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from studio_ui import BG, WHITE, TEXT, MUTED, PRIMARY, SUCCESS, DANGER, BORDER, FF, FH2, FB, FS, Btn, Choice
from src.tts_speed import validate_speed
from src.voice_registry import options as voice_options, default_reference

SAMPLES = {
    "vi": "Xin chào! Đây là giọng đọc thử cho video của bạn. Hãy lắng nghe nhịp đọc và chọn giọng phù hợp với câu chuyện.",
    "jp": "こんにちは。これは動画のナレーション用のサンプルです。声の雰囲気と読み上げの速さを確認してください。",
}
JP_VOICES = ["ja-JP-KeitaNeural", "ja-JP-NanamiNeural", "ja-JP-NaokiNeural", "ja-JP-MayuNeural"]


def playback_command(path):
    if sys.platform == "darwin":
        return ["afplay", str(path)]
    if sys.platform == "win32":
        return ["powershell", "-NoProfile", "-Command",
                "$p = New-Object System.Media.SoundPlayer($env:AUTO_DUB_PREVIEW_WAV); $p.PlaySync()"]
    player = shutil.which("ffplay")
    if player:
        return [player, "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    raise RuntimeError("Cần cài ffplay để phát mẫu giọng đọc trên máy này.")


class VoicePreviewDialog(tk.Toplevel):
    def __init__(self, parent, environment, language="vi", voice=None, on_apply=None, on_manage=None):
        super().__init__(parent, bg=BG)
        self.title("Nghe thử giọng đọc — Auto-Dub Studio")
        self.geometry("720x590")
        self.minsize(660, 550)
        self.transient(parent)
        self.environment = dict(environment)
        self.on_apply = on_apply
        self._events = queue.Queue()
        self._generation = None
        self._player = None
        self._closed = False
        self._busy = False
        self._poll_id = None
        self.language = tk.StringVar(value="Tiếng Việt" if language == "vi" else "Tiếng Nhật")
        default_voice = default_reference(language, environment)
        self.voice = tk.StringVar(value=voice or default_voice)
        self.speed = tk.StringVar(value=environment.get("TTS_SPEED", "1.0") or "1.0")
        body = tk.Frame(self, bg=BG, padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Tìm giọng kể cho video của bạn.", font=FH2, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(body, text="Chọn giọng, chỉnh tốc độ và nghe cùng một câu để so sánh.", font=FS,
                 bg=BG, fg=MUTED).pack(anchor="w", pady=(7, 20))
        controls = tk.Frame(body, bg=BG)
        controls.pack(fill="x")
        for index in range(3):
            controls.columnconfigure(index, weight=1, uniform="preview")
        for index, label in enumerate(("Ngôn ngữ", "Giọng đọc", "Tốc độ cơ bản (×)")):
            tk.Label(controls, text=label, font=FS, bg=BG, fg=MUTED).grid(row=0, column=index, sticky="w", pady=(0, 7))
        language_combo = ttk.Combobox(controls, textvariable=self.language, values=["Tiếng Việt", "Tiếng Nhật"],
                                       state="readonly", width=14, style="Studio.TCombobox")
        language_combo.grid(row=1, column=0, sticky="ew", padx=(0, 10))
        language_combo.bind("<<ComboboxSelected>>", self._change_language)
        references, labels = voice_options(language, environment)
        self._voice_combo = Choice(controls, textvariable=self.voice,
                                    values=references, labels=labels,
                                    state="readonly", width=15, style="Studio.TCombobox")
        self._voice_combo.grid(row=1, column=1, sticky="ew", padx=(0, 10))
        ttk.Combobox(controls, textvariable=self.speed, values=["0.5", "0.75", "1.0", "1.1", "1.25", "1.5", "1.75", "2.0"],
                     width=8, style="Studio.TCombobox").grid(row=1, column=2, sticky="ew")
        if on_manage:
            Btn(body, "＋  Thêm / quản lý giọng đọc", command=on_manage,
                bg=WHITE, fg=PRIMARY, font=FS, padx=10, pady=6).pack(anchor="w", pady=(9, 0))
        tk.Label(body, text="Nội dung nghe thử · tối đa 500 ký tự", font=FS, bg=BG, fg=MUTED).pack(anchor="w", pady=(20, 8))
        self.text = tk.Text(body, height=5, font=FB, bg=WHITE, fg=TEXT, wrap="word", padx=12, pady=12,
                            relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=PRIMARY)
        self.text.pack(fill="both", expand=True)
        self.text.insert("1.0", SAMPLES[language])
        tk.Label(body, text="Mẫu mới dùng dịch vụ TTS đã cấu hình. Mẫu đã có sẽ được phát lại từ cache.",
                 font=(FF, 9), bg=BG, fg=MUTED, wraplength=610, justify="left").pack(anchor="w", pady=(9, 0))
        self.status = tk.Label(body, text="Sẵn sàng nghe thử.", font=FS, bg=BG, fg=MUTED,
                               wraplength=610, justify="left", anchor="w")
        self.status.pack(fill="x", pady=(14, 10))
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(fill="x")
        self.listen = Btn(buttons, "▶  Nghe thử", command=self._listen, padx=18)
        self.listen.pack(side="left")
        Btn(buttons, "■  Dừng", command=self._stop, bg=WHITE, fg=TEXT, padx=14).pack(side="left", padx=7)
        Btn(buttons, "Dùng giọng & tốc độ này", command=self._apply, bg=WHITE,
            fg=PRIMARY, font=FS, padx=12).pack(side="right")
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Destroy>", self._destroyed, add="+")
        self._poll_id = self.after(100, self._poll)

    def _change_language(self, _event=None):
        self._stop()
        language = "vi" if self.language.get() == "Tiếng Việt" else "jp"
        references, labels = voice_options(language, self.environment)
        self._voice_combo.config(values=references, labels=labels)
        self.voice.set(default_reference(language, self.environment))
        self.text.delete("1.0", "end")
        self.text.insert("1.0", SAMPLES[language])

    def refresh_voices(self, environment, language=None, voice=None):
        self.environment = dict(environment)
        if language and self.language.get() != ("Tiếng Việt" if language == "vi" else "Tiếng Nhật"):
            self.language.set("Tiếng Việt" if language == "vi" else "Tiếng Nhật")
            self._change_language()
        current_language = "vi" if self.language.get() == "Tiếng Việt" else "jp"
        references, labels = voice_options(current_language, self.environment)
        self._voice_combo.config(values=references, labels=labels)
        if voice in references:
            self.voice.set(voice)
        elif self.voice.get() not in references:
            self.voice.set(default_reference(current_language, self.environment))

    def _listen(self):
        if self._busy:
            return
        try:
            speed = validate_speed(self.speed.get())
        except ValueError as error:
            self.status.config(text=str(error), fg=DANGER)
            return
        text = self.text.get("1.0", "end").strip()
        if not 1 <= len(text) <= 500 or not any(character.isalnum() for character in text):
            self.status.config(text="Nhập từ 1 đến 500 ký tự, có chữ hoặc số.", fg=DANGER)
            return
        self._stop_player()
        base = Path(__file__).resolve().parent
        request = {"language": "vi" if self.language.get() == "Tiếng Việt" else "jp",
                   "voice": self.voice.get(), "text": text, "speed": speed,
                   "cache_dir": str(base / "output" / ".voice_previews")}
        self._busy = True
        self.listen.config(state="disabled")
        self.status.config(text="Đang chuẩn bị mẫu giọng…", fg=PRIMARY)
        try:
            process = subprocess.Popen([sys.executable, "-m", "src.voice_preview"], cwd=base,
                                       env={**os.environ, **self.environment}, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except OSError:
            self._busy = False
            self.listen.config(state="normal")
            self.status.config(text="Không khởi chạy được dịch vụ nghe thử.", fg=DANGER)
            return
        self._generation = process
        def generate():
            try:
                stdout, _ = process.communicate(json.dumps(request, ensure_ascii=False), timeout=360)
                response = json.loads(stdout.strip().splitlines()[-1]) if stdout.strip() else {}
                if process.returncode or "preview_result" not in response:
                    raise RuntimeError(response.get("preview_error", "Không tạo được mẫu giọng đọc."))
                self._events.put((process, response["preview_result"], None))
            except Exception as error:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
                self._events.put((process, None, str(error) if isinstance(error, RuntimeError)
                                  else "Tạo mẫu quá thời gian hoặc gặp lỗi. Vui lòng thử lại."))
        threading.Thread(target=generate, daemon=True).start()

    def _poll(self):
        self._poll_id = None
        if self._closed:
            return
        try:
            while True:
                process, result, error = self._events.get_nowait()
                if process is not self._generation:
                    continue
                self._generation = None
                self._busy = False
                self.listen.config(state="normal")
                if error:
                    self.status.config(text=error, fg=DANGER)
                    continue
                try:
                    path = result["path"]
                    self._player = subprocess.Popen(playback_command(path),
                        env={**os.environ, "AUTO_DUB_PREVIEW_WAV": path},
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    provider = "Azure (giọng dự phòng)" if result["provider"] == "azure" and result.get("requested_provider") == "lucylab" else result["provider"]
                    self.status.config(text=f"Đang phát · {provider} · {result['voice']} · {result['speed']:g}×", fg=SUCCESS)
                except (OSError, RuntimeError) as failure:
                    self.status.config(text=f"Không phát được audio: {failure}", fg=DANGER)
        except queue.Empty:
            pass
        if self._player and self._player.poll() is not None:
            code = self._player.returncode
            self._player = None
            self.status.config(text="Đã nghe xong. Đổi giọng hoặc tốc độ để so sánh." if code == 0
                               else "Không phát được mẫu giọng. Kiểm tra thiết bị âm thanh.", fg=MUTED if code == 0 else DANGER)
        self._poll_id = self.after(100, self._poll)

    def _apply(self):
        try:
            speed = validate_speed(self.speed.get())
        except ValueError as error:
            self.status.config(text=str(error), fg=DANGER)
            return
        if self.on_apply:
            try:
                success = self.on_apply("vi" if self.language.get() == "Tiếng Việt" else "jp", self.voice.get(), speed)
                self.status.config(text="Kiểm tra lại tốc độ đang nhập trong Cài đặt." if success is False
                                   else "Đã lưu giọng và tốc độ cơ bản vào cài đặt.", fg=DANGER if success is False else SUCCESS)
            except OSError:
                self.status.config(text="Không lưu được cài đặt. Kiểm tra quyền ghi tệp .env.", fg=DANGER)

    def _stop_player(self):
        if self._player and self._player.poll() is None:
            self._player.terminate()
        self._player = None

    def _stop(self):
        if self._generation and self._generation.poll() is None:
            self._generation.terminate()
        self._generation = None
        self._busy = False
        self._stop_player()
        if not self._closed:
            self.listen.config(state="normal")
            self.status.config(text="Đã dừng nghe thử.", fg=MUTED)

    def _destroyed(self, event):
        if event.widget is self and not self._closed:
            self._shutdown()

    def _shutdown(self):
        self._closed = True
        self._stop()
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None

    def _close(self):
        self._shutdown()
        self.destroy()
