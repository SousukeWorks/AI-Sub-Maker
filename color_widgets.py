"""Small reusable editable RGB controls and an outline/shadow preview."""
import tkinter as tk
from tkinter import colorchooser, ttk
from tkinter.font import Font
from member_colors import DEFAULT_STYLE, rgb


class ColorFields(ttk.Frame):
    def __init__(self, parent, *, values=None, preview_only=False):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self.values = dict(values) if values else {key: tk.StringVar(self, value=value) for key, value in DEFAULT_STYLE.items() if key != 'enabled'}
        self.controls = []
        self.entries, self.buttons = {}, {}
        for row, (key, label) in enumerate((('font', '字体颜色'), ('outline', '描边颜色'), ('shadow', '阴影颜色'))):
            ttk.Label(self, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=4)
            entry = ttk.Entry(self, textvariable=self.values[key], width=10)
            entry.grid(row=row, column=1, sticky='ew', pady=4)
            button = ttk.Button(self, text='选色…', command=lambda k=key: self.choose(k))
            button.grid(row=row, column=2, padx=(6, 0), pady=4)
            self.controls.extend((entry, button))
            self.entries[key], self.buttons[key] = entry, button
            self.values[key].trace_add('write', lambda *_: self.preview())
        self.preview_font = Font(self, family='Microsoft YaHei UI', size=13, weight='bold')
        self.preview_step = self.preview_font.metrics('linespace') + 6
        self.canvas = tk.Canvas(self, height=self.preview_step * 2 + 12, width=240, bg='#20242B', highlightthickness=0)
        self.canvas.grid(row=3, column=0, columnspan=3, sticky='ew', pady=6)
        self.canvas.bind('<Configure>', lambda _: self.preview())
        if preview_only:
            for child in self.winfo_children():
                if child is not self.canvas:
                    child.grid_remove()
            self.canvas.grid_configure(row=0)
        self.preview()

    def set(self, style):
        for key, variable in self.values.items():
            variable.set(style.get(key, DEFAULT_STYLE[key]))

    def get(self, enabled=True):
        return {'enabled': enabled, **{key: value.get().strip().upper() for key, value in self.values.items()}}

    def set_enabled(self, enabled):
        for widget in self.controls:
            widget.configure(state='normal' if enabled else 'disabled')

    def choose(self, key):
        try:
            initial = rgb(self.values[key].get())
        except ValueError:
            initial = '#FFFFFF'
        value = colorchooser.askcolor(initialcolor=initial, title='选择字幕颜色', parent=self)[1]
        if value:
            self.values[key].set(value.upper())

    def preview(self):
        if not hasattr(self, 'canvas'):
            return
        self.canvas.delete('all')
        try:
            colors = {key: rgb(value.get()) for key, value in self.values.items()}
        except ValueError:
            self.canvas.create_text(10, 30, anchor='w', text='请输入 #RRGGBB 色值', fill='white')
            return
        center = max(120, self.canvas.winfo_width() / 2)
        for y, text in ((6 + self.preview_step / 2, '日本語の字幕'), (6 + self.preview_step * 1.5, '中文字幕预览')):
            font = self.preview_font
            self.canvas.create_text(center + 3, y + 3, text=text, font=font, fill=colors['shadow'])
            for dx, dy in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
                self.canvas.create_text(center + dx, y + dy, text=text, font=font, fill=colors['outline'])
            self.canvas.create_text(center, y, text=text, font=font, fill=colors['font'])

    def destroy(self):
        super().destroy()
        self.values.clear()
        self.preview_font = None
