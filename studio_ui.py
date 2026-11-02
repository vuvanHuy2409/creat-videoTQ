"""Small, dependency-free visual components for the desktop studio."""

import math
import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

BG = "#F7F6F2"
WHITE = "#FFFFFF"
TEXT = "#272923"
MUTED = "#77796F"
BORDER = "#E5E5DC"
PRIMARY = "#B84B28"
PRIMARY_HOVER = "#993E21"
PRIMARY_LIGHT = "#F8EAE1"
SIDEBAR = "#EEEFE8"
SIDEBAR_HOVER = "#E4E6DC"
SIDEBAR_ACTIVE = "#DEE3D4"
SIDEBAR_TEXT = TEXT
SIDEBAR_MUTED = "#737969"
CARD_BOR = BORDER
SUCCESS = "#48704A"
SUCCESS_BG = "#ECF2E7"
WARN = "#966A23"
WARN_BG = "#FAF1DE"
DANGER = "#AD423B"
DANGER_BG = "#FBECE8"
INFO_BG = PRIMARY_LIGHT
LOG_BG = "#242921"
LOG_FG = "#E0E6D6"
STEP_IDLE = "#EEEFE8"
STEP_ACT = PRIMARY_LIGHT
FF = "Segoe UI" if sys.platform == "win32" else "Helvetica Neue"
FH1 = (FF, 28, "bold")
FH2 = (FF, 15, "bold")
FH3 = (FF, 11, "bold")
FB = (FF, 12)
FS = (FF, 10)
FM = ("Consolas" if sys.platform == "win32" else "Menlo", 11)


def rounded_rect(canvas, x1, y1, x2, y2, radius=10, **kw):
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return canvas.create_polygon(
        x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
        x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
        x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1,
        smooth=True, splinesteps=24, **kw,
    )


class Btn(tk.Canvas):
    """Rounded button that also paints correctly with Tk on macOS.

    Native Aqua buttons ignore background colors. Drawing the surface keeps
    the primary, secondary and destructive actions consistent on all systems.
    """

    def __init__(self, parent, text, command=None, bg=PRIMARY, fg=WHITE,
                 font=FH3, padx=18, pady=10, **kw):
        self._text, self._cmd = text, command
        self._bg, self._fg, self._font = bg, fg, tkfont.Font(font=font)
        self._hover = self._pressed = self._focused = False
        self._state = kw.pop("state", "normal")
        self._padx, self._pady = padx, pady
        super().__init__(
            parent, bg=parent.cget("bg"), bd=0, highlightthickness=0,
            width=self._font.measure(text) + padx * 2 + 4,
            height=self._font.metrics("linespace") + pady * 2 + 4,
            cursor="hand2", takefocus=True, **kw,
        )
        self.bind("<Configure>", self._draw)
        self.bind("<Enter>", lambda e: self._interaction(hover=True))
        self.bind("<Leave>", lambda e: self._interaction(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<FocusIn>", lambda e: self._interaction(focused=True))
        self.bind("<FocusOut>", lambda e: self._interaction(focused=False, pressed=False))
        self.bind("<space>", self._keyboard_invoke)
        self.bind("<Return>", self._keyboard_invoke)

    def _interaction(self, **values):
        for key, value in values.items():
            setattr(self, "_" + key, value)
        self._draw()

    def _press(self, _event):
        if self._state != "disabled":
            self.focus_set()
            self._interaction(pressed=True)

    def _release(self, event):
        pressed = self._pressed
        self._interaction(pressed=False)
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.invoke()

    def _keyboard_invoke(self, _event):
        self.invoke()
        return "break"

    def invoke(self):
        if self._state != "disabled" and self._cmd:
            return self._cmd()

    def configure(self, cnf=None, **kw):
        if isinstance(cnf, dict):
            kw = {**cnf, **kw}
            cnf = None
        for key, attr in (("text", "_text"), ("command", "_cmd"),
                          ("state", "_state"), ("fg", "_fg"), ("bg", "_bg")):
            if key in kw:
                setattr(self, attr, kw.pop(key))
        result = super().configure(cnf, **kw) if kw or cnf is not None else None
        self._draw()
        return result

    config = configure

    def cget(self, key):
        if key in ("text", "command", "state", "fg"):
            return getattr(self, {"text": "_text", "command": "_cmd",
                                  "state": "_state", "fg": "_fg"}[key])
        return super().cget(key)

    @staticmethod
    def _dark(color):
        return "#" + "".join(f"{max(0, int(color[i:i + 2], 16) - 12):02x}" for i in (1, 3, 5))

    def _draw(self, _event=None):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 3:
            return
        disabled = self._state == "disabled"
        fill = STEP_IDLE if disabled else self._dark(self._bg) if self._hover or self._pressed else self._bg
        if self._focused:
            rounded_rect(self, 0, 0, w, h, fill=PRIMARY_LIGHT, outline=PRIMARY)
        rounded_rect(self, 2, 2, w - 2, h - 2, fill=fill, outline=BORDER if self._bg == WHITE else fill)
        self.create_text(w / 2, h / 2 + int(self._pressed), text=self._text,
                         font=self._font, fill=MUTED if disabled else self._fg)


def entry(parent, variable, secret=False):
    """Comfortable input with a visible keyboard focus ring."""
    widget = tk.Entry(parent, textvariable=variable, font=FB, relief="flat",
                      bg=BG, fg=TEXT, insertbackground=PRIMARY,
                      highlightthickness=1, highlightbackground=BORDER,
                      highlightcolor=PRIMARY, bd=8, show="•" if secret else "")
    return widget


class Choice(ttk.Combobox):
    """Show readable labels while keeping pipeline option values unchanged."""

    LABELS = {
        "zh": "Tiếng Trung", "en": "Tiếng Anh", "ja": "Tiếng Nhật",
        "vi": "Tiếng Việt", "ko": "Tiếng Hàn", "male": "Giọng nam",
        "female": "Giọng nữ", "demucs": "Tách giọng bằng AI",
        "duck": "Giảm âm giọng gốc", "none": "Bỏ âm thanh gốc",
        "ja-JP-KeitaNeural": "Keita · Nam", "ja-JP-NanamiNeural": "Nanami · Nữ",
        "ja-JP-NaokiNeural": "Naoki · Nam", "ja-JP-MayuNeural": "Mayu · Nữ",
    }

    def __init__(self, parent, textvariable, values, labels=None, **kw):
        self._labels = {**self.LABELS, **(labels or {})}
        self._value = textvariable
        self._display = tk.StringVar(master=parent)
        self._choices = list(values)
        super().__init__(parent, textvariable=self._display,
                         values=[self._labels.get(value, value) for value in values], **kw)
        self._display_trace = self._display.trace_add("write", self._select)
        self._value_trace = self._value.trace_add("write", self._sync)
        self._sync()

    def _sync(self, *_):
        label = self._labels.get(self._value.get(), self._value.get())
        if self._display.get() != label:
            self._display.set(label)

    def _select(self, *_):
        value = next((value for value in self._choices
                      if self._labels.get(value, value) == self._display.get()), None)
        if value is not None and self._value.get() != value:
            self._value.set(value)

    def configure(self, cnf=None, **kw):
        if "labels" in kw:
            self._labels = {**self.LABELS, **kw.pop("labels")}
        if "values" in kw:
            self._choices = list(kw["values"])
            kw["values"] = [self._labels.get(value, value) for value in self._choices]
        return super().configure(cnf, **kw)

    config = configure

    def destroy(self):
        self._value.trace_remove("write", self._value_trace)
        self._display.trace_remove("write", self._display_trace)
        super().destroy()


def heading(parent, eyebrow, title, description):
    block = tk.Frame(parent, bg=BG)
    tk.Label(block, text=eyebrow, font=(FF, 9, "bold"), bg=BG,
             fg=PRIMARY, anchor="w").pack(anchor="w")
    tk.Label(block, text=title, font=FH1, bg=BG, fg=TEXT,
             anchor="w").pack(anchor="w", pady=(6, 4))
    tk.Label(block, text=description, font=FB, bg=BG, fg=MUTED,
             anchor="w").pack(anchor="w")
    return block


class StudioBanner(tk.Canvas):
    """Responsive artwork made from a timeline and voice waveform."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG, height=174, highlightthickness=0, bd=0)
        self.bind("<Configure>", self._draw)

    def _draw(self, _event=None):
        self.delete("all")
        w = self.winfo_width()
        dark = "#303C30"
        rounded_rect(self, 0, 0, w, 174, 18, fill=dark, outline=dark)
        self.create_text(26, 26, anchor="w", text="AUTO-DUB  /  CREATIVE STUDIO",
                         font=(FF, 9, "bold"), fill="#B8C7A6")
        self.create_text(26, 77, anchor="w", text="Mỗi video, một giọng nói mới.",
                         font=(FF, 24, "bold"), fill="#FAFAF4")
        self.create_text(26, 121, anchor="w", text="Dịch, lồng tiếng và giữ trọn nhịp điệu câu chuyện.",
                         font=(FF, 12), fill="#C7CDBD")
        self.create_text(26, 149, anchor="w", text="TIẾNG VIỆT  /  TIẾNG NHẬT",
                         font=(FF, 8, "bold"), fill="#B8C7A6")
        if w < 900:
            return
        x = w - 300
        rounded_rect(self, x, 25, w - 26, 149, 14, fill="#3B4939", outline="#56634D")
        self.create_text(x + 18, 45, anchor="w", text="ORIGINAL", font=(FF, 8, "bold"), fill="#BBC9AC")
        self.create_text(w - 44, 45, anchor="e", text="DUBBED", font=(FF, 8, "bold"), fill="#EFC0A4")
        for i in range(42):
            height = 6 + abs(math.sin(i * .71) * math.cos(i * .19)) * 43
            bx = x + 19 + i * 5.5
            self.create_line(bx, 88 - height / 2, bx, 88 + height / 2,
                             fill="#BCCA9C" if i < 21 else "#EAB08B", width=3, capstyle="round")
        self.create_line(x + 18, 124, w - 45, 124, fill="#68725B")
        self.create_oval(x + 119, 120, x + 127, 128, fill="#EAB08B", outline="")


def empty_art(parent):
    canvas = tk.Canvas(parent, width=150, height=88, bg=WHITE, highlightthickness=0)
    rounded_rect(canvas, 27, 8, 123, 76, 12, fill=SIDEBAR, outline=BORDER)
    rounded_rect(canvas, 41, 19, 109, 65, 7, fill=WHITE, outline=BORDER)
    canvas.create_polygon(68, 29, 68, 55, 88, 42, fill=PRIMARY)
    canvas.create_line(16, 36, 16, 52, fill="#A7AF98", width=3, capstyle="round")
    canvas.create_line(9, 40, 9, 48, fill="#A7AF98", width=3, capstyle="round")
    canvas.create_line(135, 30, 135, 58, fill="#A7AF98", width=3, capstyle="round")
    canvas.create_line(142, 38, 142, 50, fill="#A7AF98", width=3, capstyle="round")
    return canvas
