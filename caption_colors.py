"""Manual caption/member/chorus assignments and color-only ASS re-export."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import uuid

from color_widgets import ColorFields
from member_colors import DEFAULT_STYLE, annotate_identities, export_palette
from subtitle_jobs import _lock, export_job, load_job, record_path, save_job
from subtitles import ass_time, centiseconds
from ui import ScrollArea


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def apply_roles(document, ids, role):
    count = 0
    for row in document['cues']:
        if row['id'] not in ids or row.get('suppressed'):
            continue
        row.pop('member_id', None)
        row.pop('chorus', None)
        row['role_source'] = 'manual'
        if role == '__chorus__':
            row['chorus'] = True
        elif role:
            row['member_id'] = role
        count += 1
    return count


def save_coloring(path, document, settings, *, expected_hash=None):
    path = Path(path)
    with _lock(path):
        if expected_hash is not None and fingerprint(path) != expected_hash:
            raise ValueError('处理记录已更新，请重新打开后修改，避免覆盖新的校对或翻译结果')
        document['options']['coloring'] = export_palette(settings)
        result = export_job(path, document)
        document['last_output'] = result.output.name
        save_job(path, document)
    return result


class CaptionColorsEditor(tk.Toplevel):
    def __init__(self, parent, path, settings_getter, on_export=None):
        super().__init__(parent)
        self.title('字幕成员 / 合唱配色')
        self.geometry('1040x780')
        self.minsize(760, 540)
        self.path = Path(path).resolve()
        with _lock(self.path):
            self.document = load_job(self.path)
            self.saved_hash = fingerprint(self.path)
        self.settings_getter = settings_getter
        self.on_export = on_export or (lambda *_: None)
        self.palette = export_palette(settings_getter())
        self.dirty = False
        self.search = tk.StringVar(self)
        self.filter = tk.StringVar(self, value='全部')
        self.role = tk.StringVar(self, value='默认 / 未确认')
        self.whole_turn = tk.BooleanVar(self, value=False)
        self.status = tk.StringVar(self, value='Ctrl / Shift 多选后指定成员或合唱。仅修改标记和配色，不调用识别 / 翻译 API。')
        shell = ttk.Frame(self, padding=12)
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        self.scroll_area = ScrollArea(shell)
        self.scroll_area.grid(row=0, column=0, sticky='nsew')
        root = self.scroll_area.body
        root.columnconfigure(0, weight=1)
        ttk.Label(root, text='成员应援色用于日语和中文。合唱默认为粉色字、白色描边。\n自我介绍可辅助匹配；歌词不自动等同合唱，未知身份保留默认。', style='Hint.TLabel', wraplength=680).grid(row=0, column=0, sticky='ew', pady=6)
        toolbar = ttk.Frame(root)
        toolbar.grid(row=1, column=0, sticky='ew', pady=4)
        toolbar.columnconfigure(1, weight=1)
        ttk.Combobox(toolbar, textvariable=self.filter, values=['全部', '未确认', '歌词匹配段', 'MC / 未匹配歌词'], state='readonly', width=12).grid(row=0, column=0, padx=(0, 8))
        self.search_entry = ttk.Entry(toolbar, textvariable=self.search, width=16)
        self.search_entry.grid(row=0, column=1, sticky='ew')
        self.search.trace_add('write', lambda *_: self.refresh())
        self.filter.trace_add('write', lambda *_: self.refresh())
        table = ttk.Frame(root)
        table.grid(row=2, column=0, sticky='ew')
        table.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(table, columns=('id', 'time', 'speaker', 'role', 'jp', 'zh'), show='headings', height=7, selectmode='extended')
        for key, title, width in [('id', '编号', 60), ('time', '时间轴', 170), ('speaker', '说话人 ID', 100), ('role', '成员 / 合唱', 150), ('jp', '日语', 300), ('zh', '中文', 260)]:
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=60, stretch=key in {'jp', 'zh'})
        self.tree.grid(row=0, column=0, sticky='ew')
        ybar = ttk.Scrollbar(table, orient='vertical', command=self.tree.yview)
        xbar = ttk.Scrollbar(table, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ybar.grid(row=0, column=1, sticky='ns')
        xbar.grid(row=1, column=0, sticky='ew')
        self.tree.bind('<<TreeviewSelect>>', self.preview)
        self.tree.bind('<Control-a>', self.select_all)
        assignment = ttk.Frame(root)
        assignment.grid(row=3, column=0, sticky='ew', pady=8)
        assignment.columnconfigure(0, weight=1)
        self.role_picker = ttk.Combobox(assignment, textvariable=self.role, state='readonly', width=20)
        self.role_picker.grid(row=0, column=0, sticky='ew', padx=(0, 8))
        self.apply_button = ttk.Button(assignment, text='应用选中', command=self.apply)
        self.apply_button.grid(row=0, column=1)
        self.turn_check = ttk.Checkbutton(assignment, text='扩展到该说话人的本轮连续发言', variable=self.whole_turn)
        self.turn_check.grid(row=1, column=0, columnspan=2, sticky='w', pady=6)
        self.auto_button = ttk.Button(assignment, text='匹配自我介绍', command=self.auto)
        self.auto_button.grid(row=2, column=0, sticky='w')
        self.refresh_button = ttk.Button(assignment, text='刷新词库配色', command=self.refresh_palette)
        self.refresh_button.grid(row=2, column=1)
        self.color_preview = ColorFields(root, preview_only=True)
        self.color_preview.grid(row=4, column=0, sticky='ew')
        self.origin_label = ttk.Label(root, text='', style='Hint.TLabel', wraplength=680)
        self.origin_label.grid(row=5, column=0, sticky='ew', pady=4)
        label = ttk.Label(root, textvariable=self.status, style='Hint.TLabel', wraplength=680)
        label.grid(row=6, column=0, sticky='ew', pady=8)
        root.bind('<Configure>', lambda event: label.configure(wraplength=max(200, event.width - 20)), add='+')
        footer = ttk.Frame(shell)
        footer.grid(row=1, column=0, sticky='ew', pady=(8, 0))
        self.save_button = ttk.Button(footer, text='保存并导出', command=self.save)
        self.save_button.pack(side='left')
        self.save_as_button = ttk.Button(footer, text='另存 ASS…', command=self.save_as)
        self.save_as_button.pack(side='left', padx=8)
        ttk.Button(footer, text='关闭', command=self.close).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.set_choices()
        self.refresh()

    def set_choices(self):
        self.choices = {'默认 / 未确认': '', '合唱': '__chorus__'}
        self.choices.update({value['name']: key for key, value in self.palette['members'].items()})
        self.role_picker['values'] = list(self.choices)
        if self.role.get() not in self.choices:
            self.role.set('默认 / 未确认')

    def role_name(self, row):
        if row.get('chorus'):
            return '合唱'
        member = self.palette['members'].get(row.get('member_id', ''))
        return member['name'] if member else ('未知成员：' + row['member_id'] if row.get('member_id') else '默认 / 未确认')

    def refresh(self):
        if not hasattr(self, 'tree'):
            return
        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        query, kind = self.search.get().casefold(), self.filter.get()
        for row in self.document['cues']:
            if row.get('suppressed'):
                continue
            if kind == '未确认' and (row.get('member_id') or row.get('chorus')):
                continue
            if kind == '歌词匹配段' and not row.get('lyric') or kind == 'MC / 未匹配歌词' and row.get('lyric'):
                continue
            role = self.role_name(row)
            if query and query not in (' '.join([row['text'], row['translation'], role, row.get('speaker', '')])).casefold():
                continue
            time = f"{ass_time(centiseconds(row['start']))}–{ass_time(centiseconds(row['end']))}"
            self.tree.insert('', 'end', iid=str(row['id']), values=(row['id'], time, row.get('speaker', ''), role, row['text'], row['translation']))
        self.tree.selection_set(*(selected & set(self.tree.get_children())))
        self.preview()

    def select_all(self, _event=None):
        self.tree.selection_set(*self.tree.get_children())
        return 'break'

    def apply(self):
        ids = {int(i) for i in self.tree.selection()}
        if not ids:
            messagebox.showinfo('字幕配色', '请选择一条或多条字幕', parent=self)
            return
        if self.whole_turn.get():
            rows = self.document['cues']
            for index, row in enumerate(rows):
                if row['id'] not in ids or not row.get('speaker'):
                    continue
                for step in (-1, 1):
                    at = index + step
                    while 0 <= at < len(rows) and rows[at].get('speaker') == row['speaker'] and bool(rows[at].get('lyric')) == bool(row.get('lyric')):
                        ids.add(rows[at]['id'])
                        at += step
        count = apply_roles(self.document, ids, self.choices[self.role.get()])
        self.dirty = True
        self.refresh()
        self.status.set(f'已标记 {count} 条为 {self.role.get()}，保存后写入 ASS。连续发言范围依说话人 ID 划定，可逐条修正。')

    def auto(self):
        count = annotate_identities(self.document, self.settings_getter())
        self.dirty = True
        self.refresh()
        self.status.set(f'更新 {count} 条自我介绍 / 连续发言标记，手动标记保留。请检查后保存。')

    def refresh_palette(self):
        try:
            self.palette = export_palette(self.settings_getter())
            self.set_choices()
            self.refresh()
        except Exception as exc:
            messagebox.showerror('配色无法读取', str(exc), parent=self)

    def preview(self, _event=None):
        if not hasattr(self, 'color_preview'):
            return
        selected = self.tree.selection()
        row = next((row for row in self.document['cues'] if selected and row['id'] == int(selected[0])), {})
        colors = self.palette['chorus'] if row.get('chorus') else self.palette['members'].get(row.get('member_id', ''), DEFAULT_STYLE)
        self.color_preview.set(colors)
        origins = {'manual': '手动指定', 'self_intro': '自我介绍匹配', 'continuous_turn': '同一轮连续发言', 'explicit_chorus': '明确的合唱标记'}
        self.origin_label.configure(text=(f"当前：第 {row['id']} 条 · {self.role_name(row)} · 来源：{origins.get(row.get('role_source'), '未确认')}" if row else '选择字幕查看配色与标记来源。'))

    def save(self):
        try:
            result = save_coloring(self.path, self.document, self.settings_getter(), expected_hash=self.saved_hash)
            self.saved_hash = fingerprint(self.path)
            self.dirty = False
            self.on_export(result)
            self.status.set('已保存标记并导出：' + str(result.output))
            return True
        except Exception as exc:
            messagebox.showerror('无法导出配色', str(exc), parent=self)
            return False

    def save_as(self):
        name = filedialog.asksaveasfilename(title='另存配色字幕', initialdir=str(self.path.parent),
            initialfile=Path(self.document['output_file']).stem + '.colored.ass', defaultextension='.ass', filetypes=[('ASS 字幕', '*.ass')], parent=self)
        if not name:
            return
        try:
            target = Path(name).resolve()
            doc = deepcopy(self.document)
            doc.update(job_id=uuid.uuid4().hex, output_file=target.name, source_color_record=str(self.path))
            path = record_path(target)
            if path.exists():
                path = target.with_name(target.stem + '.retry-' + uuid.uuid4().hex[:8] + '.json')
            doc['options']['coloring'] = export_palette(self.settings_getter())
            result = export_job(path, doc)
            doc['last_output'] = result.output.name
            save_job(path, doc)
            self.path, self.document, self.saved_hash = path, doc, fingerprint(path)
            self.dirty = False
            self.on_export(result)
            self.status.set('已另存配色字幕：' + str(result.output))
        except Exception as exc:
            messagebox.showerror('无法另存配色', str(exc), parent=self)

    def close(self):
        if self.dirty:
            answer = messagebox.askyesnocancel('未保存配色', '保存字幕标记并导出 ASS？', parent=self)
            if answer is None or answer and not self.save():
                return
        self.destroy()

    def destroy(self):
        super().destroy()
        self.search = self.filter = self.role = self.whole_turn = self.status = None
