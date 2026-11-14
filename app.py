"""Windows 11 Japanese ASS subtitle maker."""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from dataclasses import fields
from tkinter import filedialog, messagebox, scrolledtext, ttk
from tkinter.font import Font

import config
from engine import Settings, audio_temp_dir, endpoint, run
from media import Cancelled
from ui import ScrollArea
from retry_ui import RetryPanel
from subtitle_jobs import ProcessingIncomplete, load_job, process_job, record_path, redact_error

VERSION = "1.0.3"


class App(tk.Tk):
    def __init__(self, *, scaling=None, start_maximized=True):
        super().__init__()
        if scaling is not None:
            self.tk.call("tk", "scaling", scaling)
        self.title(f"AI-Sub-Maker {VERSION} · Soniox · 双语 ASS")
        self.geometry(f"{min(1040, max(760, self.winfo_screenwidth() - 80))}x{min(820, max(540, self.winfo_screenheight() - 100))}")
        self.minsize(760, 540)
        self.configure(bg="#f4f6fa")
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.busy = False
        self.last_output = None
        self._resize_job = None
        self.settings, remembered, warning = config.load()
        self._bootstrap_key_loaded = False
        if not self.settings.soniox_key:
            try:
                self.settings.soniox_key = config.local_soniox_key()
                if self.settings.soniox_key:
                    remembered = True
                    self._bootstrap_key_loaded = True
            except Exception:
                warning = "本机 Soniox 加密 Key 无法解密，请在“Soniox”重新填写 Key。"
        self.vars = {}
        for setting_field in fields(Settings):
            name = setting_field.name
            value = getattr(self.settings, name)
            cls = tk.BooleanVar if type(value) is bool else tk.StringVar
            self.vars[name] = cls(self, value=value)
        self.vars["audio_index"].set(str(self.settings.audio_index + 1))
        self.source = tk.StringVar(self)
        self.target = tk.StringVar(self)
        self.remember = tk.BooleanVar(self, value=remembered)
        self.backend_notice = tk.StringVar(self, value="Soniox 云端识别 · DeepSeek 整句校对翻译 · 日中双语 ASS")
        self.status = tk.StringVar(self, value="准备就绪 · Soniox · 选择 MP4 / MP3 即可开始")
        self.style_ui()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._poll_job = self.after(100, self.poll)
        self.log("使用 Soniox 云端识别与原生词级时间轴；DeepSeek 整批校对、翻译完整句子。")
        self.log("请在“Soniox”和“翻译”填写各自 API Key；人名、昵称和专有词统一在“词库”管理。")
        if warning:
            self.log(warning)
        if start_maximized:
            self.maximize_window()

    def maximize_window(self):
        try:
            self.state('zoomed')
        except tk.TclError:
            self.geometry(f'{self.winfo_screenwidth()}x{self.winfo_screenheight()}+0+0')

    def style_ui(self):
        self.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f4f6fa")
        style.configure("TLabel", background="#f4f6fa", foreground="#172033")
        style.configure("Hint.TLabel", foreground="#64748b", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 23, "bold"))
        style.configure("TButton", padding=(12, 7))
        style.configure("Primary.TButton", background="#245bdb", foreground="white", font=("Microsoft YaHei UI", 11, "bold"), padding=(24, 10))
        style.map("Primary.TButton", background=[("disabled", "#9caac3"), ("active", "#1949b5")])
        style.configure("TNotebook", background="#f4f6fa", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(8, 9))
        style.configure("Treeview", font=("Microsoft YaHei UI", 10), rowheight=Font(self, family="Microsoft YaHei UI", size=10).metrics("linespace") + 8)
        style.configure("TCheckbutton", background="#f4f6fa")
        style.configure("Horizontal.TProgressbar", troughcolor="#e3e9f3", background="#245bdb")

    def build_ui(self):
        shell = ttk.Frame(self, padding=20)
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.scroll_area = ScrollArea(shell)
        self.scroll_area.grid(row=0, column=0, sticky="nsew")
        root = self.scroll_area.body
        self.image_tool_button = ttk.Button(root, text="MP3＋图片 → MP4（独立工具）", command=self.open_image_tool)
        self.image_tool_button.pack(anchor="w", pady=(0, 12))
        ttk.Label(root, text="AI-Sub-Maker", style="Title.TLabel").pack(anchor="w")
        ttk.Label(root, textvariable=self.backend_notice, style="Hint.TLabel", wraplength=700).pack(anchor="w", pady=(2, 16))
        files = ttk.Frame(root)
        files.pack(fill="x")
        files.columnconfigure(1, weight=1)
        for row, (label, variable, action) in enumerate((("媒体文件", self.source, self.choose_source), ("ASS 输出", self.target, self.choose_target))):
            ttk.Label(files, text=label, width=8).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(files, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=(0, 10), pady=5)
            ttk.Button(files, text="选择…", command=action).grid(row=row, column=2)

        stage_row = ttk.Frame(root)
        stage_row.pack(fill='x', pady=(8, 0))
        stage_row.columnconfigure(1, weight=1)
        ttk.Label(stage_row, text='公演').grid(row=0, column=0, padx=(0, 8))
        self.stage_choice = tk.StringVar(self)
        self.stage_picker = ttk.Combobox(stage_row, textvariable=self.stage_choice, state='readonly', width=16)
        self.stage_picker.grid(row=0, column=1, sticky='ew')
        self.stage_picker.bind('<<ComboboxSelected>>', self.select_stage)
        self.lyrics_button = ttk.Button(stage_row, text='歌词库…', command=self.open_lyrics)
        self.lyrics_button.grid(row=0, column=2, padx=(8, 0))
        self.stage_hint = ttk.Label(root, style='Hint.TLabel', wraplength=700)
        self.stage_hint.pack(fill='x', pady=(4, 0))
        self.refresh_stages()

        tabs = ttk.Notebook(root)
        self.settings_tabs = tabs
        tabs.pack(fill="x", pady=(18, 12))
        recognition = ttk.Frame(tabs, padding=16)
        correction = ttk.Frame(tabs, padding=16)
        appearance = ttk.Frame(tabs, padding=16)
        tabs.add(recognition, text="Soniox")
        tabs.add(correction, text="翻译")
        tabs.add(appearance, text="样式")
        self.retry_panel = RetryPanel(tabs, self.start_retry, self.settings_for_import)
        tabs.add(self.retry_panel, text="失败重试")
        from member_ui import MemberPanel
        self.wordbook_tab = ttk.Frame(tabs)
        ttk.Checkbutton(self.wordbook_tab, text='使用人名 / 专有词库', variable=self.vars['use_member_glossary']).pack(anchor='w', padx=16, pady=(10, 0))
        self.member_panel = MemberPanel(self.wordbook_tab, self.vars['member_library'].get(), self.member_library_saved, embedded=True)
        self.member_panel.pack(fill='both', expand=True)
        self.member_panel.bind('<<WordbookLayoutChanged>>', self.queue_resize)
        tabs.add(self.wordbook_tab, text='词库')
        self.retry_panel.bind("<Configure>", self.wrap_hints)
        tabs.bind("<<NotebookTabChanged>>", self.queue_resize)
        for page in (recognition, correction, appearance):
            page.columnconfigure(1, weight=1)
            page.bind("<Configure>", self.wrap_hints)

        ttk.Label(recognition, text="Soniox 日语识别 · 原生词级时间轴", style="Hint.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.field(recognition, 1, "Base URL", "soniox_url")
        self.field(recognition, 2, "API Key", "soniox_key", secret=True)
        self.field(recognition, 3, "Soniox 模型", "soniox_model")
        ttk.Checkbutton(recognition, text="识别说话人，换人时断句", variable=self.vars["soniox_diarize"]).grid(row=4, column=0, columnspan=2, sticky="w", pady=5)
        self.soniox_test_button = ttk.Button(recognition, text="检测连接（不提交识别）", command=self.test_soniox)
        self.soniox_test_button.grid(row=5, column=0, columnspan=2, sticky="w", pady=5)
        self.field(recognition, 6, "音轨序号", "audio_index", spin=(1, 32))
        ttk.Label(recognition, text="临时音频目录").grid(row=7, column=0, sticky="w", padx=(0, 16), pady=5)
        temp_row = ttk.Frame(recognition)
        temp_row.grid(row=7, column=1, sticky="ew", pady=5)
        temp_row.columnconfigure(0, weight=1)
        self.temp_entry = ttk.Entry(temp_row, textvariable=self.vars['temp_folder'])
        self.temp_entry.grid(row=0, column=0, sticky='ew')
        self.temp_entry.bind('<FocusOut>', lambda _event: self.save_temp_folder())
        ttk.Button(temp_row, text="浏览…", command=self.choose_temp_folder).grid(row=0, column=1, padx=(8, 0))
        ttk.Label(recognition, text="留空时使用 ASS 输出目录；可选择 D / F 盘。已有 Soniox 结果会复用，也可在“失败重试”导入 JSON，不必再次识别。", style="Hint.TLabel", wraplength=780).grid(row=8, column=0, columnspan=2, sticky='w', pady=5)
        self.natural_button = ttk.Checkbutton(recognition, text="自然断句（标点、语法与停顿，保留词尾）", variable=self.vars['natural_sentences'])
        self.natural_button.grid(row=9, column=0, columnspan=2, sticky='w', pady=(10, 5))
        self.field(recognition, 10, "长停顿提示（秒）", "continuation_pause", spin=(.5, 3))
        ttk.Label(recognition, text="按标点分段；缺少标点时，结合语法、语段空格和实际停顿识别问答、句末及从句。词尾保留，时间取自原生词时间戳；分段后整批校对翻译，不按秒数或字数硬切。", style="Hint.TLabel", wraplength=780).grid(row=11, column=0, columnspan=2, sticky='w', pady=5)
        self.soniox_context_button = ttk.Checkbutton(recognition, text='发送歌词和词库作为识别上下文', variable=self.vars['soniox_send_context'])
        self.soniox_context_button.grid(row=12, column=0, columnspan=2, sticky='w', pady=5)
        ttk.Label(recognition, text='新识别按公演顺序发送完整日文歌词，保留 RAP、重复段落，并提供歌名、英文词和日文词库。\n不抽样或截断；Soniox 上限为 8000 tokens，服务端若拒绝超限会报错。DeepSeek 同样收到完整歌词。', style='Hint.TLabel', wraplength=780).grid(row=13, column=0, columnspan=2, sticky='w', pady=5)

        ttk.Checkbutton(correction, text="生成日中双语 ASS（DeepSeek 翻译）", variable=self.vars["translate"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Checkbutton(correction, text="同时校正日语错字和标点", variable=self.vars["deepseek"]).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        self.field(correction, 2, "Base URL", "deepseek_url")
        self.field(correction, 3, "API Key", "deepseek_key", secret=True)
        self.field(correction, 4, "模型名称", "deepseek_model")
        ttk.Label(correction, text="全部字幕一次请求；校对与翻译在同次请求完成，只上传文字。\n原始响应和结束原因会保存；JSON 损坏时保留可靠的完整条目，缺失条目可手动重试，不自动追加收费请求。\n保留 .raw.ass 和 .retry.json；未完成输出 .partial.ass。导出日文及中文时自动去除句首标点，过滤纯标点条目。", style="Hint.TLabel", wraplength=780).grid(row=5, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Label(correction, text='人名、昵称、专有词及译法请在“词库”标签页管理，新生成和重试均使用当前词库。', style='Hint.TLabel', wraplength=780).grid(row=7, column=0, columnspan=2, sticky='w', pady=6)

        self.field(appearance, 0, "字体", "font")
        self.field(appearance, 1, "字号", "font_size", spin=(12, 120))
        ttk.Label(appearance, text="默认白字黑描边、底部居中，1920×1080 参考画布，播放器自动缩放。\n日语使用 japanese 样式，中文使用 chinese 样式，各自独立的字幕事件，共用时间轴。\n日语在上、中文在下，每种语言各占一行，不插入换行符；中文使用微软雅黑，字号为日文的 85%。", style="Hint.TLabel", wraplength=780).grid(row=3, column=0, columnspan=2, sticky="w", pady=12)
        ttk.Checkbutton(appearance, text='使用成员应援色 / 合唱配色', variable=self.vars['use_member_colors']).grid(row=4, column=0, columnspan=2, sticky='w', pady=6)
        from color_widgets import ColorFields
        chorus = ttk.LabelFrame(appearance, text='合唱：粉色字体＋白色描边（可修改）', padding=8)
        chorus.grid(row=5, column=0, columnspan=2, sticky='ew', pady=6)
        self.chorus_color_fields = ColorFields(chorus, values={key: self.vars[f'chorus_{key}_color'] for key in ('font', 'outline', 'shadow')})
        self.chorus_color_fields.pack(fill='x')
        self.caption_colors_button = ttk.Button(appearance, text='成员 / 合唱标记与配色导出…', command=self.open_caption_colors)
        self.caption_colors_button.grid(row=6, column=0, columnspan=2, sticky='w', pady=6)
        ttk.Label(appearance, text='成员颜色在“词库 → 应援色”修改。明确自我介绍可辅助匹配本轮发言；未知身份保留默认，歌词不自动等同合唱。\n在标记窗口可 Ctrl / Shift 多选成员或合唱，直接重新导出 ASS，不调用识别或翻译 API。', style='Hint.TLabel', wraplength=780).grid(row=7, column=0, columnspan=2, sticky='ew', pady=6)

        self.logs = scrolledtext.ScrolledText(root, height=6, state="disabled", font=("Microsoft YaHei UI", 9), bg="#ffffff", fg="#334155", relief="flat", padx=12, pady=10, wrap="word")
        self.logs.pack(fill='x', pady=(0, 8))
        footer = ttk.Frame(shell)
        footer.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        action = ttk.Frame(footer)
        action.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.start_button = ttk.Button(action, text="Soniox 生成 ASS", style="Primary.TButton", command=self.start)
        self.start_button.grid(row=0, column=0, sticky="w")
        self.cancel_button = ttk.Button(action, text="取消", command=self.stop, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=8)
        self.output_button = ttk.Button(action, text="打开输出目录", command=self.open_output)
        self.output_button.grid(row=0, column=2)
        ttk.Checkbutton(footer, text="记住 Key（Windows 加密）", variable=self.remember).grid(row=1, column=0, sticky="w")
        self.progress = ttk.Progressbar(footer, maximum=100)
        self.progress.grid(row=2, column=0, sticky="ew", pady=4)
        status_label = ttk.Label(footer, textvariable=self.status, style="Hint.TLabel", wraplength=900)
        status_label.grid(row=3, column=0, sticky="ew", pady=(4, 0))
        footer.bind("<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width)))
        self.queue_resize()

    def refresh_stages(self):
        from lyrics import load_library, lyric_lines
        try:
            document = load_library(self.vars['lyrics_library'].get())
            self._stages = document['stages']
            self.stage_picker['values'] = ['不使用歌词辅助'] + [stage['name'] for stage in self._stages]
            key = self.vars['lyric_stage'].get()
            index = next((i + 1 for i, stage in enumerate(self._stages) if stage['id'] == key), 0)
            if key and not index:
                raise ValueError('之前选择的公演不在库中，请重新选择')
            self.stage_picker.current(index)
            if index:
                stage = self._stages[index - 1]
                count = sum(bool(lyric_lines(song.get('japanese', ''))) for song in stage['songs'])
                self.stage_hint.configure(text=f'已录入歌词 {count} / {len(stage["songs"])} 首；连续多行匹配才替换。已有 Soniox JSON 可导入重断句，无需重新识别。')
            else:
                self.stage_hint.configure(text='内置 5 套公演曲目目录；完整歌词需自行导入。选择公演后启用歌词辅助，不影响普通 MC 字幕。')
        except Exception as exc:
            self._stages = []
            self.stage_hint.configure(text='歌词库无法加载：' + str(exc))

    def select_stage(self, _event=None):
        index = self.stage_picker.current()
        self.vars['lyric_stage'].set(self._stages[index - 1]['id'] if index > 0 else '')
        self.refresh_stages()

    def open_lyrics(self):
        if self.busy:
            return
        from lyrics_ui import LyricsEditor
        try:
            def saved(path, stage):
                self.vars['lyrics_library'].set(path)
                self.vars['lyric_stage'].set(stage)
                self.refresh_stages()
                # Save only these preferences, leaving API fields to the normal
                # settings workflow and respecting the existing remember flag.
                current, remembered, _ = config.load()
                current.lyrics_library, current.lyric_stage = path, stage
                config.save(current, remembered)
            LyricsEditor(self, self.vars['lyrics_library'].get(), self.vars['lyric_stage'].get(), saved)
        except Exception as exc:
            messagebox.showerror('无法打开歌词库', str(exc))

    def open_members(self):
        if self.busy:
            return
        self.settings_tabs.select(self.wordbook_tab)
        self.after_idle(lambda: self.scroll_area.ensure_visible(self.member_panel.search_entry))

    def member_library_saved(self, path):
        self.vars['member_library'].set(path)
        current, remembered, _ = config.load()
        current.member_library = path
        current.use_member_glossary = self.vars['use_member_glossary'].get()
        config.save(current, remembered)

    def prepare_wordbook(self):
        if not self.vars['use_member_glossary'].get():
            return
        if not self.member_panel.save_if_dirty():
            raise ValueError('词库尚未保存，请在“词库”标签页检查并保存后再处理')


    def test_soniox(self):
        if self.busy:
            return
        settings = Settings(soniox_url=self.vars["soniox_url"].get(), soniox_key=self.vars["soniox_key"].get(), soniox_model=self.vars["soniox_model"].get())
        self.set_busy(True)
        self.status.set("正在检测 Soniox 连接……")
        def worker():
            from soniox_asr import check_connection
            try:
                self.events.put(("connection", check_connection(settings)))
            except Exception as exc:
                self.events.put(("error", redact_error(exc, settings)))
        threading.Thread(target=worker, daemon=True).start()

    def wrap_hints(self, event):
        if event.width < 160:
            return
        for widget in event.widget.winfo_children():
            if isinstance(widget, ttk.Label) and str(widget.cget("style")) == "Hint.TLabel":
                width = max(180, event.width - 40)
                if int(widget.cget("wraplength") or 0) != width:
                    widget.configure(wraplength=width)
        if self.settings_tabs.select() == str(event.widget):
            self.queue_resize()

    def queue_resize(self, _event=None):
        if self._resize_job is None:
            self._resize_job = self.after_idle(self.resize_tab)

    def resize_tab(self):
        self._resize_job = None
        selected = self.settings_tabs.select()
        if selected:
            page = self.nametowidget(selected)
            height = page.winfo_reqheight()
            if int(self.settings_tabs.cget("height") or 0) != height:
                self.settings_tabs.configure(height=height)

    def field(self, parent, row, label, name, values=None, secret=False, spin=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 18), pady=5)
        if values:
            widget = ttk.Combobox(parent, textvariable=self.vars[name], values=values, state="readonly", width=32)
        elif spin:
            widget = ttk.Spinbox(parent, textvariable=self.vars[name], from_=spin[0], to=spin[1], increment=.1 if isinstance(spin[0], float) else 1, width=10)
        else:
            widget = ttk.Entry(parent, textvariable=self.vars[name], show="•" if secret else "")
        widget.grid(row=row, column=1, sticky="w" if spin else "ew", pady=5)

    def choose_source(self):
        path = filedialog.askopenfilename(title="选择日语 MP4 / MP3 媒体", filetypes=[("视频与音频", "*.mp4 *.mp3 *.mkv *.mov *.m4v *.webm *.wav *.m4a *.flac"), ("MP3 音频", "*.mp3"), ("MP4 视频", "*.mp4"), ("所有文件", "*.*")])
        if path:
            self.source.set(path)
            language = "ja-zh" if self.vars["translate"].get() else "ja"
            target = Path(path).with_suffix(f".{language}.ass")
            if target.with_suffix(".soniox.json").exists():
                self.log("发现已有 Soniox 记录，已保留原输出位置以继续处理，避免新建输出后重新识别。")
            elif target.exists() or record_path(target).exists():
                index = 2
                while target.exists() or record_path(target).exists():
                    target = Path(path).with_suffix(f".{language}-{index}.ass")
                    index += 1
            self.target.set(str(target))


    def open_image_tool(self):
        import subprocess
        command = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, str(Path(__file__).resolve())]
        command.append("--image-video")
        if self.source.get().strip():
            command.extend(("--audio", self.source.get().strip()))
        try:
            subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except OSError as exc:
            messagebox.showerror("无法打开独立工具", str(exc))


    def choose_target(self):
        path = filedialog.asksaveasfilename(title="保存 ASS 字幕", defaultextension=".ass", filetypes=[("ASS 字幕", "*.ass")], initialfile=Path(self.target.get()).name if self.target.get() else "subtitle.ja-zh.ass")
        if path:
            self.target.set(path)




    def choose_temp_folder(self):
        path = filedialog.askdirectory(title="选择临时音频目录", mustexist=False)
        if path:
            self.vars['temp_folder'].set(path)
            self.save_temp_folder()

    def save_temp_folder(self):
        try:
            audio_temp_dir(Settings(temp_folder=self.vars['temp_folder'].get()))
            from dataclasses import replace
            config.save(replace(self.settings, temp_folder=self.vars['temp_folder'].get()), self.remember.get())
        except Exception as exc:
            messagebox.showerror('无法保存临时目录', str(exc))

    def snapshot(self):
        self.prepare_wordbook()
        values = {name: variable.get() for name, variable in self.vars.items()}
        values["audio_index"] = int(values["audio_index"]) - 1
        values["font_size"] = int(values["font_size"])
        values["continuation_pause"] = float(values["continuation_pause"])
        from sentence_context import allowed_pause
        allowed_pause("", values["continuation_pause"])
        if not 0 <= values["audio_index"] < 32 or not 12 <= values["font_size"] <= 120:
            raise ValueError("请检查音轨序号或字号")
        if any(c in values["font"] for c in ",\r\n") or not values["font"].strip():
            raise ValueError("请填写有效的字体名称")
        from soniox_asr import Client
        Client(Settings(soniox_url=values['soniox_url'], soniox_key=values['soniox_key']))
        if not values['soniox_model'].strip():
            raise ValueError('请填写 Soniox 模型名称')
        if values["deepseek"] or values["translate"]:
            endpoint(values["deepseek_url"], "chat/completions")
            if not values["deepseek_key"].strip() or not values["deepseek_model"].strip():
                raise ValueError("请填写 DeepSeek API Key 和模型名称")
        settings = Settings(**values)
        from lyrics import selected_stage
        selected_stage(settings)
        from member_terms import member_reference
        member_reference(settings)
        from member_colors import export_palette
        export_palette(settings)
        audio_temp_dir(settings)
        settings._legacy_context_for_cache = getattr(self.settings, '_legacy_context_for_cache', '')
        return settings

    def start(self):
        if self.busy:
            return
        try:
            if not self.source.get().strip() or not self.target.get().strip():
                raise ValueError("请先选择 MP4 / MP3 媒体和 ASS 输出位置")
            source, target = Path(self.source.get().strip()), Path(self.target.get().strip())
            if not source.is_file():
                raise ValueError("所选媒体文件不存在")
            if target.suffix.lower() != ".ass" or target.resolve() == source.resolve():
                raise ValueError("请选择独立的 .ass 输出文件")
            settings = self.snapshot()
            if target.exists() and not messagebox.askyesno("覆盖字幕", f"输出文件已存在，是否覆盖？\n{target}"):
                return
            config.save(settings, self.remember.get())
            if self._bootstrap_key_loaded:
                try:
                    config.local_soniox_key(consume=True)
                    self._bootstrap_key_loaded = False
                except Exception:
                    self.log("设置已保存；本机 Key 导入文件暂未移除。")
        except Exception as exc:
            messagebox.showerror("请检查设置", str(exc))
            return
        self.set_busy(True)
        self.cancel.clear()
        self.progress["value"] = 0
        self.status.set("正在提交 Soniox 识别……")
        self.log(f"开始：{source}")
        self.log(f"本次识别：Soniox · {settings.soniox_model} · 原生时间轴")

        def worker():
            try:
                count = run(source, target, settings, **self.worker_callbacks())
                review = 0
                if target.with_suffix(".soniox-timing.json").is_file():
                    report = json.loads(target.with_suffix(".soniox-timing.json").read_text(encoding="utf-8-sig"))
                    review = report["review_cues"]
                self.events.put(("done", (target, count, settings.translate, review)))
            except ProcessingIncomplete as exc:
                self.events.put(("incomplete", exc.result))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", redact_error(exc, settings)))
        threading.Thread(target=worker, daemon=True).start()

    def settings_for_import(self):
        from subtitles import make_ass
        settings = Settings(deepseek=self.vars["deepseek"].get(), translate=self.vars["translate"].get(),
                            font=self.vars["font"].get(), font_size=int(self.vars["font_size"].get()),
                            natural_sentences=self.vars["natural_sentences"].get(),
                            continuation_pause=float(self.vars["continuation_pause"].get()),
                            lyrics_library=self.vars['lyrics_library'].get(), lyric_stage=self.vars['lyric_stage'].get())
        from sentence_context import allowed_pause
        allowed_pause("", settings.continuation_pause)
        make_ass([], settings.font, settings.font_size)
        from dataclasses import replace
        return replace(settings, **self.color_kwargs(), member_library=self.vars['member_library'].get(),
                       use_member_glossary=self.vars['use_member_glossary'].get())

    def color_kwargs(self):
        return {key: self.vars[key].get() for key in ('use_member_colors', 'chorus_font_color', 'chorus_outline_color', 'chorus_shadow_color')}

    def color_settings(self):
        self.prepare_wordbook()
        settings = Settings(member_library=self.vars['member_library'].get(),
                            use_member_glossary=self.vars['use_member_glossary'].get(), **self.color_kwargs())
        from member_colors import export_palette
        export_palette(settings)
        return settings

    def open_caption_colors(self):
        if self.busy:
            return
        path = self.retry_panel.path
        if not path:
            value = filedialog.askopenfilename(title='打开字幕处理记录，指定成员 / 合唱', filetypes=[('字幕处理记录', '*.json')])
            if not value:
                return
            path = Path(value)
        try:
            load_job(path)
            self.color_settings()
            from caption_colors import CaptionColorsEditor
            CaptionColorsEditor(self, path, self.color_settings, self.colors_exported)
        except Exception as exc:
            messagebox.showerror('无法打开字幕配色', str(exc))

    def colors_exported(self, result):
        self.last_output = result.output
        self.retry_panel.load(result.record)
        self.status.set('字幕配色已导出：' + result.output.name)
        self.log('字幕配色已保存：' + str(result.output))

    def worker_callbacks(self):
        return dict(cancel=self.cancel, progress=lambda value: self.events.put(("progress", value)),
                    log=lambda text: self.events.put(("log", text)), on_job=lambda path: self.events.put(("job", path)))

    def set_busy(self, busy):
        self.busy = busy
        self.start_button.configure(state="disabled" if busy else "normal")
        self.cancel_button.configure(state="normal" if busy else "disabled")
        self.retry_panel.set_busy(busy)
        self.soniox_test_button.configure(state="disabled" if busy else "normal")
        self.lyrics_button.configure(state='disabled' if busy else 'normal')
        self.stage_picker.configure(state='disabled' if busy else 'readonly')
        self.caption_colors_button.configure(state='disabled' if busy else 'normal')


    def start_retry(self, path, ids, mode):
        if self.busy:
            return
        try:
            self.prepare_wordbook()
            settings = Settings(deepseek_url=self.vars["deepseek_url"].get().strip(),
                                deepseek_key=self.vars["deepseek_key"].get().strip(),
                                deepseek_model=self.vars["deepseek_model"].get().strip(),
                                continuation_pause=float(self.vars["continuation_pause"].get()),
                                lyrics_library=self.vars['lyrics_library'].get(), lyric_stage=self.vars['lyric_stage'].get(),
                                member_library=self.vars['member_library'].get(), use_member_glossary=self.vars['use_member_glossary'].get(),
                                **self.color_kwargs())
            from sentence_context import allowed_pause
            allowed_pause("", settings.continuation_pause)
            endpoint(settings.deepseek_url, "chat/completions")
            if not settings.deepseek_key or not settings.deepseek_model:
                raise ValueError("请先在“翻译”填写 DeepSeek API Key 和模型名称")
            load_job(path)
        except Exception as exc:
            messagebox.showerror("无法重试", str(exc))
            return
        self.set_busy(True)
        self.cancel.clear()
        self.progress["value"] = 0
        self.status.set(f"正在重试 {len(ids)} 条字幕，一次提交……")
        self.log(f"重试 {len(ids)} 条：{path}")

        def worker():
            try:
                result = process_job(path, settings, ids, mode, **self.worker_callbacks())
                self.events.put(("retry_done", result))
            except Cancelled:
                self.events.put(("cancelled", None))
            except Exception as exc:
                self.events.put(("error", redact_error(exc, settings)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        job_path = None
        for _ in range(200):
            try:
                kind, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self.log(value)
            elif kind == "progress":
                self.progress["value"] = value * 100
            elif kind == "job":
                job_path = value
            else:
                self.set_busy(False)
                if kind == "done":
                    self.last_output, count, translated, review = value
                    suffix = f" · {review} 条时间轴需复核" if review else ""
                    self.status.set(f"生成完成 · {count} 条字幕{suffix} · {self.last_output.name}")
                    kind_label = "日中双语字幕" if translated else "日语字幕"
                    detail = f"\n{review} 条字幕只有时间点，暂设短显示时间，请复核 .soniox-timing.json。" if review else ""
                    alert = messagebox.showwarning if review else messagebox.showinfo
                    alert("生成完成", f"已生成 {count} 条{kind_label}：\n{self.last_output}{detail}")
                elif kind in {"incomplete", "retry_done"}:
                    self.last_output = value.output
                    job_path = value.record
                    self.settings_tabs.select(self.retry_panel)
                    self.after_idle(self.scroll_area.ensure_visible, self.retry_panel.selected_button)
                    if value.remaining:
                        self.status.set(f"{value.remaining} 条字幕尚未完成 · 可在“失败重试”继续")
                        self.log(f"处理记录：{value.record}\n部分字幕：{value.output}")
                        if kind == "incomplete":
                            messagebox.showwarning("校对 / 翻译未完成", f"{value.remaining} 条字幕尚未完成。可选择单条或多条重试，无需重新识别。\n记录：{value.record}\n部分字幕：{value.output}")
                        elif not value.attempted:
                            messagebox.showinfo("没有可重试的步骤", "所选步骤已成功，或翻译需要先完成日语校对。请选择“失败步骤（自动）”或“仅校对”。")
                    else:
                        self.status.set(f"全部完成 · {value.total} 条字幕 · {value.output.name}")
                        self.log(f"完整字幕已保存：{value.output}")
                        messagebox.showinfo("重试完成", f"全部 {value.total} 条字幕已完成：\n{value.output}")
                elif kind == "connection":
                    self.status.set(value)
                    self.log(value)
                    messagebox.showinfo("Soniox 连接", value)
                elif kind == "cancelled":
                    self.status.set("任务已取消 · 已有处理记录可继续重试")
                    self.log("任务已取消，已保存的处理记录可继续重试。")
                else:
                    self.status.set("处理失败 · 请查看提示")
                    self.log(value)
                    messagebox.showerror("处理失败", value)
        if job_path:
            try:
                self.retry_panel.load(job_path)
                if not self.busy and self.retry_panel.document.get("last_output"):
                    self.last_output = job_path.parent / self.retry_panel.document["last_output"]
            except Exception as exc:
                self.log(f"记录暂未刷新：{exc}")
        self._poll_job = self.after(100, self.poll)

    def destroy(self):
        if getattr(self, "_resize_job", None):
            self.after_cancel(self._resize_job)
            self._resize_job = None
        if getattr(self, "_poll_job", None):
            self.after_cancel(self._poll_job)
            self._poll_job = None
        super().destroy()
        self.vars.clear()
        self.source = self.target = self.remember = None
        self.backend_notice = self.status = self.stage_choice = None

    def log(self, message):
        self.logs.configure(state="normal")
        self.logs.insert("end", message + "\n")
        self.logs.see("end")
        self.logs.configure(state="disabled")

    def stop(self):
        self.cancel.set()
        self.cancel_button.configure(state="disabled")
        self.status.set("正在取消，等待当前处理步骤结束……")

    def open_output(self):
        path = self.last_output or Path(self.target.get()) if self.target.get() else self.last_output
        if path and path.parent.is_dir():
            os.startfile(str(path.parent))
        else:
            messagebox.showinfo("输出目录", "请先选择输出位置或生成字幕。")

    def close(self):
        if self.busy and not messagebox.askyesno("退出", "字幕仍在处理中，确定取消并退出？"):
            return
        if not self.member_panel.confirm_close():
            return
        self.cancel.set()
        self.destroy()


def main():
    parser = argparse.ArgumentParser(description="MP4 / MP3 日语语音 → ASS 字幕")
    parser.add_argument("--smoke-test", metavar="REPORT", help=argparse.SUPPRESS)
    parser.add_argument("--image-video", action="store_true", help="只打开 MP3＋图片合成 MP4 的独立工具")
    parser.add_argument("--audio", default="", help=argparse.SUPPRESS)
    parser.add_argument("--picture", default="", help=argparse.SUPPRESS)
    parser.add_argument("--soniox", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test:
        from diagnostics import smoke_test
        smoke_test(Path(args.smoke_test))
        return
    if os.name == "nt":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if args.image_video or (getattr(sys, "frozen", False) and Path(sys.executable).stem.casefold() == "mp3imagetomp4"):
        from image_video_app import ImageVideoApp
        ImageVideoApp(args.audio, args.picture).mainloop()
    else:
        App().mainloop()


if __name__ == "__main__":
    main()
