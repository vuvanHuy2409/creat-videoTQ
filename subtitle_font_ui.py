"""Subtitle font picker with a preview drawn from the actual bundled TTF."""

import tkinter as tk
from tkinter import ttk, colorchooser
import unicodedata

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

from studio_ui import BG, WHITE, TEXT, MUTED, PRIMARY, PRIMARY_LIGHT, SUCCESS, DANGER, BORDER, FH2, FB, FS, Btn
from src.subtitle_fonts import DEFAULT_FONT, DEFAULT_SIZE, font_names, get_font, validate_size, needs_cjk_fallback
from src.subtitle_style import STYLE_DEFAULTS, SUBTITLE_COLOR_PRESETS, SubtitleStyle, validate_color

SAMPLE_TEXT = "Mỗi khoảnh khắc, một câu chuyện.\nĐẹp, rõ nét và đầy cảm xúc!"
PREVIEW_BG = "#303C30"


def _wrap_caption(draw, text, font, max_width):
    """Wrap words and unspaced CJK text without discarding the final lines."""
    lines = []
    for paragraph in unicodedata.normalize("NFC", text).splitlines():
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}".strip()
            if draw.textlength(candidate, font=font) <= max_width:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for char in word:
                if line and draw.textlength(line + char, font=font) > max_width:
                    lines.append(line)
                    line = ""
                line += char
        lines.append(line)
    return lines


def render_preview(name, size, text, width=900, height=300, style=None):
    """Fit a complete 720px reference scene into the UI without cropping.

    Lay out at a fixed reference width first. Scaling text with the widget's
    width alone makes wide, short settings panels cut off the caption.
    Supersampling keeps the bundled face and Vietnamese accents sharp.
    Colours and opacity use the same validated style as video export.
    """
    spec = get_font("Noto Sans JP" if needs_cjk_fallback(text) else name)
    size = validate_size(size)
    style = style or SubtitleStyle()
    image = Image.new("RGB", (width, height), PREVIEW_BG)
    if not text.strip():
        return image
    reference_width = 1440  # 720px scene rendered at 2x.
    font = ImageFont.truetype(str(spec.path), size * 2)
    stroke, pad_x, pad_y, gap, margin = 9, 22, 12, 12, 48
    draw = ImageDraw.Draw(image)
    lines = _wrap_caption(draw, text, font,
                          reference_width - 2 * (margin + pad_x + stroke))
    bounds = [draw.textbbox((0, 0), line or " ", font=font, stroke_width=stroke)
              for line in lines]
    heights = [max(1, box[3] - box[1]) + 2 * pad_y for box in bounds]
    content_height = sum(heights) + gap * (len(lines) - 1)
    scene_height = max(480, content_height + 2 * margin)
    scene = Image.new("RGBA", (reference_width, scene_height), PREVIEW_BG)
    captions = Image.new("RGBA", scene.size)
    draw = ImageDraw.Draw(captions)
    y = (scene_height - content_height) / 2
    for line, box, line_height in zip(lines, bounds, heights):
        if line:
            line_width = box[2] - box[0]
            left = (reference_width - line_width) / 2
            if style.bg_enabled and style.bg_opacity:
                rgb = tuple(int(style.bg_color[i:i + 2], 16) for i in (1, 3, 5))
                draw.rectangle((left - pad_x, y, left + line_width + pad_x,
                                y + line_height), fill=(*rgb, round(255 * style.bg_opacity / 100)))
            draw.text((left - box[0], y + pad_y - box[1]), line, font=font,
                      fill=style.text_color, stroke_width=stroke, stroke_fill=style.outline_color)
        y += line_height + gap
    scene = Image.alpha_composite(scene, captions).convert("RGB")
    inset = min(12, (width - 1) // 2, (height - 1) // 2)
    fitted = ImageOps.contain(scene, (width - 2 * inset, height - 2 * inset),
                              Image.Resampling.LANCZOS)
    image.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2))
    return image


class SubtitleFontPanel(tk.Frame):
    def __init__(self, parent, font_var, size_var, bg=WHITE, style_vars=None):
        super().__init__(parent, bg=bg)
        self.font_var, self.size_var = font_var, size_var
        self.style_vars = style_vars if style_vars is not None else {
            key: tk.StringVar(master=self, value=value) for key, value in STYLE_DEFAULTS.items()}
        self._render_after = None
        self._photo = None
        self._disposed = False
        controls = tk.Frame(self, bg=bg)
        controls.pack(fill="x")
        controls.columnconfigure(0, weight=3)
        controls.columnconfigure(1, weight=1)
        tk.Label(controls, text="Phông chữ phụ đề", font=FS, bg=bg, fg=MUTED).grid(row=0, column=0, sticky="w", pady=(0, 7))
        tk.Label(controls, text="Cỡ chữ (px)", font=FS, bg=bg, fg=MUTED).grid(row=0, column=1, sticky="w", pady=(0, 7))
        self.font_combo = ttk.Combobox(controls, textvariable=font_var, values=font_names(),
                                        state="readonly", style="Studio.TCombobox", font=FB)
        self.font_combo.grid(row=1, column=0, sticky="ew", padx=(0, 14))
        self.size_combo = ttk.Combobox(controls, textvariable=size_var,
                                        values=["32", "42", "50", "56", "64", "72", "80", "96"],
                                        width=8, style="Studio.TCombobox", font=FB)
        self.size_combo.grid(row=1, column=1, sticky="ew")
        self.description = tk.Label(self, text="", font=FS, bg=bg, fg=PRIMARY, anchor="w", justify="left")
        self.description.pack(fill="x", pady=(10, 14))
        self.color_target_var = tk.StringVar(master=self, value="SUBTITLE_TEXT_COLOR")
        self.color_labels = {"SUBTITLE_TEXT_COLOR": "Màu chữ", "SUBTITLE_OUTLINE_COLOR": "Màu viền chữ",
                             "SUBTITLE_BG_COLOR": "Màu nền phụ đề"}
        colors = tk.Frame(self, bg=bg)
        colors.pack(fill="x", pady=(0, 12))
        self.color_buttons = {}
        self.color_target_buttons = {}
        for index, (key, label) in enumerate(self.color_labels.items()):
            colors.columnconfigure(index, weight=1, uniform="caption_color")
            cell = tk.Frame(colors, bg=bg)
            cell.grid(row=0, column=index, sticky="ew", padx=(0, 10) if index < 2 else 0)
            target = Btn(cell, label, command=lambda k=key: self.color_target_var.set(k),
                         font=FS, padx=8, pady=6, bg=BG, fg=TEXT)
            target.pack(fill="x", pady=(0, 7))
            self.color_target_buttons[key] = target
            row = tk.Frame(cell, bg=bg)
            row.pack(fill="x")
            swatch = Btn(row, "Chọn", bg=self.style_vars[key].get(),
                         command=lambda k=key: self._choose_color(k), font=FS, padx=8, pady=6)
            swatch.pack(side="left", padx=(0, 5))
            self.color_buttons[key] = swatch
            field = tk.Entry(row, textvariable=self.style_vars[key], width=8, font=FS, bg=BG, fg=TEXT,
                             relief="flat", bd=6)
            field.pack(side="left", fill="x", expand=True)
            field.bind("<FocusIn>", lambda _event, k=key: self.color_target_var.set(k))
        palette = tk.Frame(self, bg=bg)
        palette.pack(fill="x", pady=(0, 12))
        tk.Label(palette, text="15 màu chọn nhanh", font=FS, bg=bg, fg=TEXT).pack(anchor="w")
        self.palette_hint = tk.Label(palette, text="", font=FS, bg=bg, fg=MUTED,
                                     anchor="w", justify="left", wraplength=600)
        self.palette_hint.pack(fill="x", pady=(5, 8))
        palette_grid = tk.Frame(palette, bg=bg)
        palette_grid.pack(fill="x")
        self.palette_buttons = {}
        for index, (name, color) in enumerate(SUBTITLE_COLOR_PRESETS):
            palette_grid.columnconfigure(index % 5, weight=1, uniform="palette_color")
            button = Btn(palette_grid, name, command=lambda c=color: self._apply_palette(c),
                         bg=color, fg=self._color_foreground(color), font=FS, padx=8, pady=7)
            button.grid(row=index // 5, column=index % 5, sticky="ew", padx=2, pady=2)
            self.palette_buttons[color] = button
        background = tk.Frame(self, bg=bg)
        background.pack(fill="x", pady=(0, 12))
        tk.Checkbutton(background, text="Hiện nền phụ đề", variable=self.style_vars["SUBTITLE_BG_ENABLED"],
                       onvalue="1", offvalue="0", font=FS, bg=bg, fg=TEXT,
                       activebackground=bg, selectcolor=bg).pack(side="left")
        tk.Label(background, text="Độ đậm nền (%)", font=FS, bg=bg, fg=MUTED).pack(side="left", padx=(16, 6))
        tk.Spinbox(background, from_=0, to=100, width=4, font=FS, bg=BG, fg=TEXT,
                   textvariable=self.style_vars["SUBTITLE_BG_OPACITY"]).pack(side="left")
        self.canvas = tk.Canvas(self, height=220, bg=PREVIEW_BG, highlightthickness=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", self._schedule_render)
        self.preview_hint = tk.Label(
            self, text="Màu chữ, viền và nền này sẽ dùng khi xuất video có phụ đề. Mẫu tự vừa khung.",
            font=FS, bg=bg, fg=MUTED, justify="left", anchor="w", wraplength=600)
        self.preview_hint.pack(fill="x", pady=(8, 14))
        tk.Label(self, text="Nội dung xem thử", font=FS, bg=bg, fg=MUTED).pack(anchor="w", pady=(0, 7))
        self.sample_var = tk.StringVar(value=SAMPLE_TEXT.replace("\n", " "))
        tk.Entry(self, textvariable=self.sample_var, font=FS, bg=BG, fg=TEXT,
                 relief="flat", bd=8, highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=PRIMARY).pack(fill="x")
        self.status = tk.Label(self, text="", font=FS, bg=bg, fg=DANGER,
                               wraplength=650, justify="left", anchor="w")
        self.status.pack(fill="x", pady=(8, 0))
        self._traces = [(variable, variable.trace_add("write", self._schedule_render))
                        for variable in (font_var, size_var, self.sample_var, *self.style_vars.values())]
        self._traces.append((self.color_target_var, self.color_target_var.trace_add("write", self._refresh_palette)))
        self.bind("<Destroy>", self._cleanup, add="+")
        self._schedule_render()

    def _choose_color(self, key):
        self.color_target_var.set(key)
        try:
            initial = validate_color(self.style_vars[key].get())
        except ValueError:
            initial = STYLE_DEFAULTS[key]
        _, color = colorchooser.askcolor(initialcolor=initial, parent=self.winfo_toplevel(),
                                        title="Chọn màu phụ đề")
        if color:
            self.style_vars[key].set(color.upper())

    @staticmethod
    def _color_foreground(color):
        def luminance(value):
            channels = [int(value[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4
                      for channel in channels]
            return sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))
        light, dark = luminance(color), luminance(TEXT)
        dark_contrast = (max(light, dark) + .05) / (min(light, dark) + .05)
        return TEXT if dark_contrast >= 1.05 / (light + .05) else WHITE

    def _apply_palette(self, color):
        self.style_vars[self.color_target_var.get()].set(validate_color(color))

    def _refresh_palette(self, *_):
        if self._disposed:
            return
        key = self.color_target_var.get()
        try:
            selected = validate_color(self.style_vars[key].get())
            name = dict((color, name) for name, color in SUBTITLE_COLOR_PRESETS).get(selected, "Tùy chỉnh")
            detail = f"{name} · {selected}"
        except ValueError:
            selected, detail = None, "Nhập mã #RRGGBB hoặc chọn một màu bên dưới"
        self.palette_hint.config(text=f"Đang chọn: {self.color_labels[key]} · {detail}")
        for target, button in self.color_target_buttons.items():
            button.config(bg=PRIMARY_LIGHT if target == key else BG,
                          fg=PRIMARY if target == key else MUTED)
        for name, color in SUBTITLE_COLOR_PRESETS:
            self.palette_buttons[color].config(text=f"✓ {name}" if color == selected else name)

    def _schedule_render(self, *_):
        if self._disposed:
            return
        self._refresh_palette()
        if self._render_after is not None:
            self.after_cancel(self._render_after)
        self._render_after = self.after(90, self._render)

    def _render(self):
        self._render_after = None
        try:
            spec = get_font(self.font_var.get())
            size = validate_size(self.size_var.get())
            style = SubtitleStyle.from_settings({key: var.get() for key, var in self.style_vars.items()})
            for key, button in self.color_buttons.items():
                color = style.to_settings()[key]
                button.config(bg=color, fg=self._color_foreground(color))
            self.description.config(text=spec.description)
            width = self.canvas.winfo_width()
            height = self.canvas.winfo_height()
            if width < 2 or height < 2:
                return  # Wait for Configure after the panel has a real size.
            self.preview_hint.config(wraplength=width)
            self.palette_hint.config(wraplength=width)
            self.description.config(wraplength=width)
            self.status.config(wraplength=width)
            image = render_preview(spec.name, size, self.sample_var.get(), width=width * 2,
                                   height=height * 2, style=style)
            self._photo = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS), master=self)
            self.canvas.delete("all")
            self.canvas.create_image(width / 2, height / 2, image=self._photo)
            self.status.config(text="Chữ Nhật/Trung dùng Noto Sans JP để hiển thị đủ ký tự."
                               if needs_cjk_fallback(self.sample_var.get()) else "")
        except (ValueError, OSError) as error:
            self.status.config(text=str(error))

    def _cleanup(self, event):
        if event.widget is not self or self._disposed:
            return
        self._disposed = True
        if self._render_after is not None:
            self.after_cancel(self._render_after)
            self._render_after = None
        for variable, trace in self._traces:
            variable.trace_remove("write", trace)


class SubtitleFontDialog(tk.Toplevel):
    def __init__(self, parent, environment, on_apply):
        super().__init__(parent, bg=BG)
        self.title("Phông chữ phụ đề — Auto-Dub Studio")
        self.geometry("760x680")
        self.minsize(650, 650)
        self.transient(parent)
        self.on_apply = on_apply
        self.font_var = tk.StringVar(value=environment.get("SUBTITLE_FONT") or DEFAULT_FONT)
        self.size_var = tk.StringVar(value=environment.get("SUBTITLE_FONT_SIZE") or str(DEFAULT_SIZE))
        self.style_vars = {key: tk.StringVar(master=self, value=environment.get(key, value))
                           for key, value in STYLE_DEFAULTS.items()}
        body = tk.Frame(self, bg=BG, padx=24, pady=22)
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Chọn diện mạo cho câu chuyện.", font=FH2, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(body, text="12 phông có sẵn · hỗ trợ tiếng Việt · xem mẫu ngay khi chọn",
                 font=FS, bg=BG, fg=MUTED).pack(anchor="w", pady=(7, 18))
        buttons = tk.Frame(body, bg=BG)
        buttons.pack(side="bottom", fill="x", pady=(12, 0))
        Btn(buttons, "Dùng kiểu phụ đề này", command=self._apply).pack(side="right")
        Btn(buttons, "Đóng", command=self.destroy, bg=WHITE, fg=TEXT, font=FS).pack(side="left")
        viewport = tk.Frame(body, bg=BG)
        viewport.pack(fill="both", expand=True)
        self.scroll_canvas = tk.Canvas(viewport, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=self.scroll_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self.scroll_canvas.pack(side="left", fill="both", expand=True)
        self.scroll_canvas.config(yscrollcommand=scrollbar.set)
        self.panel = SubtitleFontPanel(self.scroll_canvas, self.font_var, self.size_var,
                                       bg=BG, style_vars=self.style_vars)
        window = self.scroll_canvas.create_window(0, 0, anchor="nw", window=self.panel)
        self.scroll_canvas.bind("<Configure>", lambda event: self.scroll_canvas.itemconfig(window, width=event.width))
        self.panel.bind("<Configure>", lambda event: self.scroll_canvas.config(
            scrollregion=self.scroll_canvas.bbox("all")), add="+")
        self.bind("<MouseWheel>", lambda event: self.scroll_canvas.yview_scroll(
            -1 if event.delta > 0 else 1, "units"))

    def _apply(self):
        try:
            font = get_font(self.font_var.get())
            size = validate_size(self.size_var.get())
            style = SubtitleStyle.from_settings({key: var.get() for key, var in self.style_vars.items()})
            saved = self.on_apply(font.name, size, style)
            self.panel.status.config(text="Kiểm tra lại các giá trị đang nhập trong Cài đặt." if saved is False
                                     else "Đã lưu kiểu phụ đề. Video xuất ra sẽ dùng lựa chọn này.",
                                     fg=DANGER if saved is False else SUCCESS)
        except (ValueError, OSError) as error:
            self.panel.status.config(text=str(error), fg=DANGER)
