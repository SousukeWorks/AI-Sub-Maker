"""Failure history browser and single/multiple subtitle retry selection."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from subtitles import ass_time, centiseconds
from subtitle_jobs import LABELS, STAGES, import_japanese_ass, load_job, save_job, unfinished

STATUS = {"pending": "待处理", "failed": "失败", "success": "成功", "skipped": "关闭"}
MODES = {"失败步骤（自动）": "auto", "仅校对": "proofread", "仅翻译": "translate"}


class RetryPanel(ttk.Frame):
    def __init__(self, parent, on_retry, settings_for_import):
        super().__init__(parent, padding=16)
        self.columnconfigure(0, weight=1)
        self.on_retry = on_retry
        self.settings_for_import = settings_for_import
        self.path = None
        self.document = None
        self.busy = False
        self.show_all = tk.BooleanVar(self, value=False)
        self.mode = tk.StringVar(self, value="失败步骤（自动）")
        self.summary = tk.StringVar(self, value="尚未加载记录。生成字幕后会自动保存，也可以打开之前的记录。")
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.open_button = ttk.Button(toolbar, text="打开记录…", command=self.open_record)
        self.open_button.pack(side="left")
        self.import_button = ttk.Button(toolbar, text="导入字幕…", command=self.import_ass)
        self.import_button.pack(side="left", padx=6)
        ttk.Checkbutton(toolbar, text="显示全部", variable=self.show_all, command=self.refresh).pack(side="right")
        ttk.Label(self, text="Ctrl / Shift 可多选。只重试失败或未完成的步骤；使用“翻译”中的当前 Key、接口和模型。", style="Hint.TLabel", wraplength=780).grid(row=1, column=0, sticky="ew", pady=6)
        actions = ttk.Frame(self)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        actions.columnconfigure(0, weight=1)
        ttk.Combobox(actions, textvariable=self.mode, values=list(MODES), state="readonly", width=17).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.selected_button = ttk.Button(actions, text="重试选中", command=lambda: self.retry(False))
        self.selected_button.grid(row=0, column=1, padx=(0, 6))
        self.all_button = ttk.Button(actions, text="重试全部失败", command=lambda: self.retry(True))
        self.all_button.grid(row=0, column=2)
        frame = ttk.Frame(self)
        frame.grid(row=3, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("id", "time", "proofread", "translate", "attempts", "japanese", "chinese", "error")
        self.tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="extended", height=6)
        widths = (52, 160, 80, 80, 105, 240, 240, 270)
        labels = ("编号", "时间轴", "校对", "翻译", "尝试次数", "日语", "中文", "失败原因")
        for column, width, label in zip(columns, widths, labels):
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=width if column not in {"japanese", "chinese", "error"} else 100, stretch=column in {"japanese", "chinese", "error"})
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.show_details)
        self.tree.bind("<Control-a>", self.select_all)
        ttk.Label(self, textvariable=self.summary, style="Hint.TLabel", wraplength=780).grid(row=4, column=0, sticky="ew", pady=8)
        self.details = scrolledtext.ScrolledText(self, height=5, state="disabled", wrap="word", font=("Microsoft YaHei UI", 9), relief="flat", padx=10, pady=8)
        self.details.grid(row=5, column=0, sticky="ew")
        self.set_busy(False)

    def set_busy(self, busy):
        self.busy = busy
        for button in (self.open_button, self.import_button, self.selected_button, self.all_button):
            button.configure(state="disabled" if busy else "normal")

    def open_record(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="打开字幕处理记录", filetypes=[("字幕处理记录", "*.json"), ("所有文件", "*.*")])
        if path:
            try:
                self.load(Path(path))
            except Exception as exc:
                messagebox.showerror("无法打开记录", str(exc))

    def import_ass(self):
        if self.busy:
            return
        source = filedialog.askopenfilename(title="导入已有 Soniox JSON 或日语 ASS（不重新识别）", filetypes=[("Soniox 识别记录", "*.soniox.json"), ("ASS 字幕", "*.ass"), ("JSON 记录", "*.json")])
        if not source:
            return
        source = Path(source)
        name = source.stem.removesuffix(".raw").removesuffix(".soniox") + ".recovered.ass"
        target = filedialog.asksaveasfilename(title="恢复后完整字幕的保存位置", defaultextension=".ass", initialdir=str(source.parent), initialfile=name, filetypes=[("ASS 字幕", "*.ass")])
        if not target:
            return
        try:
            settings = self.settings_for_import()
            if not settings.deepseek and not settings.translate:
                raise ValueError("请先在“翻译”启用日语校对或中文翻译")
            if source.suffix.lower() == ".json":
                from soniox_asr import import_result
                path, document = import_result(source, Path(target), settings)
            else:
                path, document = import_japanese_ass(source, Path(target), settings)
            document["state"] = "incomplete"
            save_job(path, document)
            self.load(path)
            if source.suffix.lower() == ".json":
                messagebox.showinfo("Soniox 结果已恢复", f"已导入 {len(document['cues'])} 条字幕，没有重新识别音频。\n点击“重试全部失败”即可整批校对 / 翻译。\n{document.get('timing_review_cues', 0)} 条字幕缺少时长，需复核时间轴报告。")
        except Exception as exc:
            messagebox.showerror("无法导入字幕", str(exc))

    def load(self, path):
        document = load_job(Path(path))
        self.path, self.document = Path(path).resolve(), document
        self.refresh()

    def refresh(self):
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        if self.document is None:
            return
        remaining = sum(unfinished(row) for row in self.document["cues"])
        for row in self.document["cues"]:
            if row.get('suppressed') and not self.show_all.get():
                continue
            visible_pending = unfinished(row) and not self.busy
            failed = any(row[stage]["status"] == "failed" for stage in STAGES)
            if not self.show_all.get() and not visible_pending and not failed:
                continue
            error = "；".join(f"{LABELS[stage]}：{row[stage]['error']}" for stage in STAGES if row[stage]["error"])
            time = f"{ass_time(centiseconds(row['start']))}–{ass_time(centiseconds(row['end']))}"
            attempts = f"校{row['proofread']['attempts']} / 译{row['translate']['attempts']}"
            self.tree.insert("", "end", iid=str(row["id"]), values=(row["id"], time, STATUS[row["proofread"]["status"]], STATUS[row["translate"]["status"]], attempts, row["text"][:150], row["translation"][:150], error))
        visible = set(self.tree.get_children())
        self.tree.selection_set(*sorted(selected & visible))
        self.summary.set(f"{self.path.name} · 共 {len(self.document['cues'])} 条 · {remaining} 条失败或未完成。" + ("已全部完成，可勾选“显示全部”查看历史。" if not remaining else ""))
        self.show_details()

    def select_all(self, _event=None):
        self.tree.selection_set(*self.tree.get_children())
        return "break"

    def show_details(self, _event=None):
        selected = self.tree.selection()
        text = "选择一条字幕查看原文、译文、失败原因和重试历史。"
        if self.document and selected:
            row = next(row for row in self.document["cues"] if row["id"] == int(selected[0]))
            lines = [f"已选择 {len(selected)} 条，当前详情：第 {row['id']} 条", f"原始日语：{row['original']}", f"当前日语：{row['text']}", f"中文字幕：{row['translation'] or '尚未完成'}"]
            if row.get('suppressed'):
                lines.append('已过滤：' + str(row['suppressed']))
            if row.get('chorus') or row.get('member_id'):
                lines.append('字幕角色：' + ('合唱' if row.get('chorus') else row['member_id']) + ' · ' + row.get('role_source', ''))
            if row.get('lyric'):
                reference = row['lyric']
                lines.append(f'歌词参考：{reference.get("stage", "")} / {reference.get("song", "")} 第 {reference.get("line", "")} 行；匹配度 {reference.get("score", 0):.1%}')
            if self.document.get('responses'):
                lines.append('最近响应诊断：' + str(self.path.parent / self.document['responses'][-1]))
            for stage in STAGES:
                state = row[stage]
                lines.append(f"\n{LABELS[stage]}：{STATUS[state['status']]}，尝试 {state['attempts']} 次。{state['error']}")
                for item in state["history"]:
                    if not isinstance(item, dict):
                        continue
                    date = str(item.get("at", ""))
                    try:
                        date = datetime.fromisoformat(date).astimezone(timezone(timedelta(hours=9))).strftime("%m-%d %H:%M:%S JST")
                    except ValueError:
                        pass
                    lines.append(f"  {date} · {item.get('result', '')} · {item.get('error', '')}")
            text = "\n".join(lines)
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def retry(self, all_failed):
        if self.busy:
            return
        if not self.path or not self.document:
            messagebox.showinfo("重试", "请先打开处理记录，或导入原始日语 ASS。")
            return
        ids = [row["id"] for row in self.document["cues"] if unfinished(row)] if all_failed else [int(value) for value in self.tree.selection()]
        if not ids:
            messagebox.showinfo("重试", "请选择一条或多条失败/未完成字幕。")
            return
        self.on_retry(self.path, ids, MODES[self.mode.get()])

    def destroy(self):
        super().destroy()
        self.show_all = self.mode = self.summary = None
