"""Independent MP3 + picture converter. No subtitle settings, keys or ASR."""
from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from image_video import export_image_video
from media import Cancelled
from ui import ScrollArea


class ImageVideoApp(tk.Tk):
    def __init__(self, audio="", picture="", *, scaling=None):
        super().__init__()
        if scaling is not None:
            self.tk.call("tk", "scaling", scaling)
        self.title("MP3＋图片 → MP4 · 独立合成工具")
        self.geometry("900x560")
        self.minsize(720, 420)
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        self.audio, self.picture, self.target = (tk.StringVar(self) for _ in range(3))
        self.audio.set(audio)
        self.picture.set(picture)
        if audio:
            self.target.set(str(Path(audio).with_suffix(".image.mp4")))
        self.status = tk.StringVar(self, value="选择 MP3、一张图片和 MP4 保存位置即可开始")
        self.cancel = threading.Event()
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.last_output = None
        self.inputs = []
        shell = ttk.Frame(self, padding=16)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.scroll_area = ScrollArea(shell)
        self.scroll_area.grid(row=0, column=0, sticky="nsew")
        body = self.scroll_area.body
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="MP3＋一张图片，生成 MP4", font=("Microsoft YaHei UI", 18, "bold"), wraplength=650).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.hint = ttk.Label(body, text="这是独立合成功能，无需生成字幕或填写 API Key。\n视频画面始终为所选图片，声音来自 MP3，时长跟随音频。", wraplength=650)
        self.hint.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 16))
        for row, (label, variable, command) in enumerate((("MP3 音频", self.audio, self.choose_audio),
                ("画面图片", self.picture, self.choose_picture), ("MP4 保存", self.target, self.choose_target)), start=2):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=8)
            entry = ttk.Entry(body, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 8), pady=8)
            button = ttk.Button(body, text="选择…", command=command)
            button.grid(row=row, column=2, pady=8)
            self.inputs.extend((entry, button))
        footer = ttk.Frame(shell)
        footer.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        actions = ttk.Frame(footer)
        actions.grid(row=0, column=0, sticky="w")
        self.start_button = ttk.Button(actions, text="开始合成 MP4", command=self.start)
        self.start_button.grid(row=0, column=0)
        self.cancel_button = ttk.Button(actions, text="取消", command=self.stop, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=8)
        self.output_button = ttk.Button(actions, text="打开输出目录", command=self.open_output)
        self.output_button.grid(row=0, column=2)
        self.progress = ttk.Progressbar(footer, maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=10)
        self.status_label = ttk.Label(footer, textvariable=self.status, wraplength=650)
        self.status_label.grid(row=2, column=0, sticky="ew")
        footer.bind("<Configure>", lambda event: self.status_label.configure(wraplength=max(200, event.width)))
        body.bind("<Configure>", lambda event: self.hint.configure(wraplength=max(200, event.width - 20)), add="+")
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_job = self.after(100, self.poll)

    def choose_audio(self):
        path = filedialog.askopenfilename(title="选择 MP3 音频", filetypes=[("MP3 音频", "*.mp3"), ("其他音频", "*.wav *.m4a *.flac *.aac"), ("所有文件", "*.*")])
        if path:
            self.audio.set(path)
            self.target.set(str(Path(path).with_suffix(".image.mp4")))

    def choose_picture(self):
        path = filedialog.askopenfilename(title="选择一张画面图片", filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.webp"), ("所有文件", "*.*")])
        if path:
            self.picture.set(path)

    def choose_target(self):
        path = filedialog.asksaveasfilename(title="保存 MP4", initialfile=Path(self.target.get()).name if self.target.get() else "picture-audio.mp4",
                  defaultextension=".mp4", filetypes=[("MP4 视频", "*.mp4")], confirmoverwrite=False)
        if path:
            self.target.set(path)

    def set_busy(self, busy):
        self.busy = busy
        self.start_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        for widget in self.inputs:
            widget.configure(state="disabled" if busy else "normal")

    def start(self):
        if self.busy:
            return
        source, picture, target = map(Path, (self.audio.get().strip(), self.picture.get().strip(), self.target.get().strip()))
        if not source.is_file() or not picture.is_file() or target.suffix.lower() != ".mp4":
            messagebox.showerror("请检查输入", "请选择 MP3 音频、画面图片，以及独立的 .mp4 保存位置。", parent=self)
            return
        if target.resolve() in {source.resolve(), picture.resolve()}:
            messagebox.showerror("请检查输出", "MP4 输出不能覆盖音频或图片原文件。", parent=self)
            return
        if target.exists() and not messagebox.askyesno("覆盖 MP4", f"是否覆盖已有输出？\n{target}", parent=self):
            return
        self.cancel.clear()
        self.progress["value"] = 0
        self.set_busy(True)
        self.status.set("正在合成图片画面的 MP4……")
        def worker():
            try:
                duration = export_image_video(source, picture, target, cancel=self.cancel,
                    progress=lambda value: self.events.put(("progress", value)))
                self.events.put(("done", (target, duration)))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "progress":
                self.progress["value"] = value * 100
                continue
            self.set_busy(False)
            if self.closing:
                self.destroy()
                return
            if kind == "done":
                self.last_output, duration = value
                self.status.set(f"已完成 · {duration:.1f} 秒 · {self.last_output.name}")
                messagebox.showinfo("合成完成", f"MP3 与图片已合成为 MP4：\n{self.last_output}", parent=self)
            elif kind == "cancelled":
                self.status.set("合成已取消，原文件已保留")
            else:
                self.status.set("合成失败，请检查文件与保存位置")
                messagebox.showerror("合成失败", value, parent=self)
        self._poll_job = self.after(100, self.poll)

    def stop(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在取消合成……")

    def open_output(self):
        target = self.last_output or Path(self.target.get())
        if target.parent.is_dir():
            os.startfile(str(target.parent))

    def close(self):
        if self.busy:
            self.closing = True
            self.stop()
        else:
            self.destroy()

    def destroy(self):
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
            self._poll_job = None
        super().destroy()
        self.audio = self.picture = self.target = self.status = None


if __name__ == "__main__":
    ImageVideoApp().mainloop()
