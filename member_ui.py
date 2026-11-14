"""View and edit Japanese/Chinese names and nickname recognition aliases."""
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk
from pathlib import Path
import uuid
from copy import deepcopy

from member_terms import library_path, load_library, save_library, validate, TEXT_FIELDS, LIST_FIELDS
from member_catalog import initial_members
from ui import ScrollArea


MODES = {'按中文译法翻译': 'translate', '保留日文原文': 'preserve'}


class MemberPanel(ttk.Frame):
    def __init__(self, parent, path='', on_saved=None, *, embedded=False, on_close=None):
        super().__init__(parent)
        self.embedded = embedded
        self.on_close = on_close or self.destroy
        self.path = str(library_path(path))
        self.load_error = ''
        try:
            self.document = load_library(path)
        except (OSError, ValueError) as exc:
            self.document = validate(initial_members())
            self.load_error = str(exc)
        self.baseline = deepcopy(self.document)
        self.on_saved = on_saved or (lambda *_: None)
        self.active_member = None
        self.active_kind = 'members'
        self.search = tk.StringVar(self)
        self.enabled = tk.BooleanVar(self, value=True)
        self.summary = tk.StringVar(self)
        self.record_kind = tk.StringVar(self, value='编辑人名')
        self.translation = tk.StringVar(self, value='按中文译法翻译')
        self.color_enabled = tk.BooleanVar(self, value=False)
        self.color_source = tk.StringVar(self)
        self.fields = {name: tk.StringVar(self) for name in TEXT_FIELDS}
        shell = ttk.Frame(self, padding=12)
        shell.pack(fill='both', expand=True)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)
        if embedded:
            self.scroll_area = None
            root = ttk.Frame(shell)
            root.grid(row=0, column=0, sticky='nsew')
        else:
            self.scroll_area = ScrollArea(shell)
            self.scroll_area.grid(row=0, column=0, sticky='nsew')
            root = self.scroll_area.body
        root.columnconfigure(0, weight=1)
        hint = ttk.Label(root, text='直接管理人名、昵称与专有词。人名按中文姓名翻译，昵称保留日文原称。\n专有词可指定中文译法或保留日文。点击保存后用于 DeepSeek 校对与翻译。', style='Hint.TLabel', wraplength=650)
        hint.grid(row=0, column=0, sticky='ew', pady=6)
        paths = ttk.Frame(root)
        paths.grid(row=1, column=0, sticky='ew')
        paths.columnconfigure(0, weight=1)
        self.path_label = ttk.Entry(paths)
        self.path_label.grid(row=0, column=0, sticky='ew')
        self.show_path()
        ttk.Button(paths, text='打开库…', command=self.open_library).grid(row=0, column=1, padx=4)
        ttk.Button(paths, text='另存库…', command=self.save_as).grid(row=0, column=2)
        search = ttk.Frame(root)
        search.grid(row=2, column=0, sticky='ew', pady=6)
        search.columnconfigure(0, weight=1)
        entry = ttk.Entry(search, textvariable=self.search)
        entry.grid(row=0, column=0, sticky='ew')
        entry.bind('<KeyRelease>', lambda _: self.fill_list())
        self.search_entry = entry
        actions = ttk.Frame(search)
        actions.grid(row=1, column=0, sticky='w', pady=4)
        self.add_member_button = ttk.Button(actions, text='新增人名', command=self.add_member)
        self.add_member_button.grid(row=0, column=0, padx=(0, 4))
        self.add_term_button = ttk.Button(actions, text='新增词语', command=self.add_term)
        self.add_term_button.grid(row=0, column=1, padx=(0, 4))
        self.delete_button = ttk.Button(actions, text='删除', command=self.delete_member)
        self.delete_button.grid(row=0, column=2, padx=(0, 4))
        self.quick_save_button = ttk.Button(actions, text='保存', command=self.save)
        self.quick_save_button.grid(row=0, column=3)
        listing = ttk.Frame(root)
        listing.grid(row=3, column=0, sticky='ew')
        listing.columnconfigure(0, weight=1)
        self.tree = ttk.Treeview(listing, columns=('kind', 'jp', 'zh', 'nick'), show='headings', selectmode='browse', height=5)
        for key, title in (('kind', '类型'), ('jp', '日文姓名 / 原词'), ('zh', '中文姓名 / 译法'), ('nick', '日文昵称')):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=170 if key != 'kind' else 65, minwidth=70 if key != 'kind' else 50, stretch=key != 'kind')
        self.tree.grid(row=0, column=0, sticky='ew')
        bar = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.tree.configure(yscrollcommand=bar.set)
        self.tree.bind('<<TreeviewSelect>>', self.select_member)
        self.tabs = ttk.Notebook(root)
        self.tabs.grid(row=4, column=0, sticky='ew', pady=8)
        self.tabs.bind('<<NotebookTabChanged>>', lambda _: self.event_generate('<<WordbookLayoutChanged>>'))
        names = ttk.Frame(self.tabs, padding=8)
        names.columnconfigure(1, weight=1)
        self.tabs.add(names, text='姓名 / 词语')
        for row, (key, label) in enumerate((('japanese_name', '日文姓名 / 原词'), ('chinese_name', '中文姓名 / 译法'),
                                           ('reading_japanese', '日文读音'), ('notes', '备注'), ('source_url', '来源网址'))):
            ttk.Label(names, text=label).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=4)
            ttk.Entry(names, textvariable=self.fields[key], width=15).grid(row=row, column=1, sticky='ew')
        ttk.Label(names, textvariable=self.record_kind).grid(row=5, column=0, sticky='w', pady=4)
        self.mode_picker = ttk.Combobox(names, textvariable=self.translation, values=list(MODES), width=15, state='disabled')
        self.mode_picker.grid(row=5, column=1, sticky='ew')
        ttk.Checkbutton(names, text='此条目参与校对 / 翻译', variable=self.enabled).grid(row=6, column=0, columnspan=2, sticky='w', pady=8)
        self.texts = {}
        for tab_title, keys in (('昵称', (('nicknames_japanese', '日文昵称（原样输出）'), ('nicknames_chinese', '中文昵称 / 别称（仅供识别）'))),
                                ('别写', (('aliases_japanese', '日文别写'), ('aliases_chinese', '中文别写')))):
            page = ttk.Frame(self.tabs, padding=8)
            page.columnconfigure(0, weight=1)
            self.tabs.add(page, text=tab_title)
            for index, (key, label) in enumerate(keys):
                ttk.Label(page, text=label + '，每行一个').grid(row=index * 2, column=0, sticky='w', pady=4)
                text = scrolledtext.ScrolledText(page, height=3, width=22, wrap='word', font=('Microsoft YaHei UI', 10))
                text.grid(row=index * 2 + 1, column=0, sticky='ew')
                self.texts[key] = text
        color_page = ttk.Frame(self.tabs, padding=8)
        color_page.columnconfigure(0, weight=1)
        self.tabs.add(color_page, text='应援色')
        from color_widgets import ColorFields
        self.color_fields = ColorFields(color_page)
        self.color_fields.grid(row=0, column=0, sticky='ew')
        self.color_checkbox = ttk.Checkbutton(color_page, text='使用此成员的字幕配色（日文与中文）', variable=self.color_enabled)
        self.color_checkbox.grid(row=1, column=0, sticky='w', pady=4)
        self.reset_colors_button = ttk.Button(color_page, text='恢复官网配色', command=self.reset_colors)
        self.reset_colors_button.grid(row=2, column=0, sticky='w', pady=4)
        self.color_source_label = ttk.Label(color_page, textvariable=self.color_source, style='Hint.TLabel', wraplength=600)
        self.color_source_label.grid(row=3, column=0, sticky='ew', pady=4)
        color_page.bind('<Configure>', lambda event: self.color_source_label.configure(wraplength=max(200, event.width - 16)))
        summary = ttk.Label(root, textvariable=self.summary, style='Hint.TLabel', wraplength=650)
        summary.grid(row=5, column=0, sticky='ew', pady=6)
        def wrap(event):
            hint.configure(wraplength=max(200, event.width - 12))
            summary.configure(wraplength=max(200, event.width - 12))
        root.bind('<Configure>', wrap, add='+')
        footer = ttk.Frame(shell)
        footer.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        self.save_button = ttk.Button(footer, text='保存词库', command=self.save)
        self.save_button.pack(side='left')
        if not embedded:
            ttk.Button(footer, text='关闭', command=self.close).pack(side='right')
        self.fill_list()

    def show_path(self):
        self.path_label.configure(state='normal')
        self.path_label.delete(0, 'end')
        self.path_label.insert(0, self.path)
        self.path_label.configure(state='readonly')

    def capture(self):
        if self.active_member is not None:
            for key, value in self.fields.items():
                target = {'japanese_name': 'japanese_term', 'chinese_name': 'chinese_term'}.get(key, key) if self.active_kind == 'terms' else key
                self.active_member[target] = value.get().strip()
            self.active_member['enabled'] = self.enabled.get()
            for key, text in self.texts.items():
                if self.active_kind == 'terms' and key.startswith('nicknames_'):
                    continue
                self.active_member[key] = list(dict.fromkeys(line.strip() for line in text.get('1.0', 'end').splitlines() if line.strip()))
            if self.active_kind == 'terms':
                self.active_member['translation'] = MODES[self.translation.get()]
            else:
                self.active_member['subtitle_style'] = self.color_fields.get(self.color_enabled.get())

    def records(self):
        return [(kind, row) for kind in ('members', 'terms') for row in self.document.get(kind, [])]

    def fill_list(self, select=None):
        self.capture()
        current = select or (self.active_member['id'] if self.active_member else None)
        self.active_member = None
        self.tree.delete(*self.tree.get_children())
        query = self.search.get().strip().casefold()
        for kind, member in self.records():
            jp, zh = ('japanese_name', 'chinese_name') if kind == 'members' else ('japanese_term', 'chinese_term')
            values = [member.get(key, '') for key in (*TEXT_FIELDS, 'japanese_term', 'chinese_term')]
            values.extend(value for key in LIST_FIELDS for value in member.get(key, []))
            label = '人名' if kind == 'members' else '专有词'
            if query and query not in (' '.join(values) + ' ' + label).casefold():
                continue
            self.tree.insert('', 'end', iid=member['id'], values=(label, member[jp], member.get(zh, ''), ' / '.join(member.get('nicknames_japanese', []))))
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(current if current in children else children[0])
            self.select_member()
        else:
            self.show_member(None)
        self.summary.set(f'人名 {len(self.document["members"])} 位，专有词 {len(self.document.get("terms", []))} 条，当前显示 {len(children)} 条。修改后保存，下一次校对 / 翻译使用新词库。')
        if self.load_error:
            self.summary.set('词库无法读取：' + self.load_error + '。请打开有效词库，或另存当前内容为新词库。')

    def select_member(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        found = next(((kind, row) for kind, row in self.records() if row['id'] == selected[0]), None)
        member = found[1] if found else None
        if member is self.active_member:
            return
        self.capture()
        self.active_kind = found[0] if found else 'members'
        self.show_member(member)

    def show_member(self, member):
        self.active_member = member
        for key, value in self.fields.items():
            target = {'japanese_name': 'japanese_term', 'chinese_name': 'chinese_term'}.get(key, key) if self.active_kind == 'terms' else key
            value.set(member.get(target, '') if member else '')
        self.enabled.set(member.get('enabled', True) if member else False)
        self.record_kind.set('编辑专有词' if self.active_kind == 'terms' else '编辑人名')
        mode = member.get('translation', 'translate') if member else 'translate'
        self.translation.set(next(label for label, value in MODES.items() if value == mode))
        self.mode_picker.configure(state='readonly' if member and self.active_kind == 'terms' else 'disabled')
        from member_colors import ensure_style, DEFAULT_STYLE
        personal = bool(member and self.active_kind == 'members')
        if personal:
            ensure_style(member)
        self.color_fields.set(member['subtitle_style'] if personal else DEFAULT_STYLE)
        self.color_enabled.set(member['subtitle_style']['enabled'] if personal else False)
        self.color_fields.set_enabled(personal)
        self.color_checkbox.configure(state='normal' if personal else 'disabled')
        self.reset_colors_button.configure(state='normal' if personal else 'disabled')
        self.color_source.set(('官网色名：' + ' × '.join(member.get('penlight_colors', [])) +
                               '\n' + member.get('color_source_url', '') + '\n官网提供色名；RGB 是可编辑映射。三色分别用于字体、描边和阴影。')
                              if personal else '专有词不参与说话人配色。')
        for key, text in self.texts.items():
            text.configure(state='normal')
            text.delete('1.0', 'end')
            text.insert('1.0', '\n'.join(member.get(key, [])) if member else '')
            if self.active_kind == 'terms' and key.startswith('nicknames_'):
                text.configure(state='disabled')

    def reset_colors(self):
        if self.active_member and self.active_kind == 'members':
            from member_colors import official_style
            style = official_style(self.active_member['id'])
            self.color_fields.set(style)
            self.color_enabled.set(style['enabled'])

    def save(self):
        try:
            if self.load_error:
                raise ValueError('原词库无法读取，请先打开有效词库或另存为新文件，以保留原件')
            self.capture()
            save_library(self.document, self.path)
            self.on_saved(self.path)
            self.baseline = deepcopy(self.document)
            self.summary.set('已保存：' + self.path)
            return True
        except Exception as exc:
            messagebox.showerror('无法保存成员库', str(exc), parent=self)
            return False

    def is_dirty(self):
        self.capture()
        return self.document != self.baseline

    def save_if_dirty(self):
        return not self.load_error and (not self.is_dirty() or self.save())

    def confirm_close(self):
        if self.is_dirty():
            answer = messagebox.askyesnocancel('保存词库', '词库有修改，是否保存后关闭？', parent=self.winfo_toplevel())
            if answer is None or (answer and not self.save()):
                return False
        return True

    def close(self):
        if self.confirm_close():
            self.on_close()

    def open_library(self):
        path = filedialog.askopenfilename(parent=self, title='打开成员库', filetypes=[('成员库 JSON', '*.json')])
        if path:
            try:
                document = load_library(path)
                if not self.confirm_close():
                    return
                self.document, self.path, self.active_member = document, path, None
                self.baseline = deepcopy(document)
                self.load_error = ''
                self.show_path()
                self.search.set('')
                self.fill_list()
                self.on_saved(path)
            except Exception as exc:
                messagebox.showerror('无法打开成员库', str(exc), parent=self)

    def save_as(self):
        path = filedialog.asksaveasfilename(parent=self, title='另存成员库', initialfile='member-library.json', defaultextension='.json', filetypes=[('成员库 JSON', '*.json')])
        if path:
            previous, previous_error = self.path, self.load_error
            self.path = path
            self.load_error = ''
            if self.save():
                self.show_path()
            else:
                self.path = previous
                self.load_error = previous_error

    def add_member(self):
        name = simpledialog.askstring('新增成员', '日文姓名（也可补入毕业成员）', parent=self)
        if name and name.strip():
            self.capture()
            member = {'id': uuid.uuid4().hex, 'enabled': False, 'japanese_name': name.strip(), 'chinese_name': '',
                      'reading_japanese': '', 'notes': '', 'source_url': '', **{key: [] for key in LIST_FIELDS}}
            self.document['members'].append(member)
            self.search.set('')
            self.fill_list(member['id'])
            self.tabs.select(0)
            self.summary.set('填写中文姓名与昵称后，勾选“此成员参与校对 / 翻译”并保存。')

    def delete_member(self):
        if self.active_member and messagebox.askyesno('删除词条', '删除当前词条及其别称？保存后生效。', parent=self.winfo_toplevel()):
            self.document[self.active_kind].remove(self.active_member)
            self.active_member = None
            self.fill_list()

    def add_term(self):
        name = simpledialog.askstring('新增专有词', '日文原词', parent=self.winfo_toplevel())
        if name and name.strip():
            self.capture()
            term = {'id': uuid.uuid4().hex, 'enabled': False, 'japanese_term': name.strip(), 'chinese_term': '',
                    'translation': 'translate', 'reading_japanese': '', 'notes': '', 'source_url': '',
                    'aliases_japanese': [], 'aliases_chinese': []}
            self.document.setdefault('terms', []).append(term)
            self.search.set('')
            self.fill_list(term['id'])
            self.tabs.select(0)
            self.summary.set('填写中文译法，或选择保留日文；勾选参与校对 / 翻译后保存。')

    def destroy(self):
        super().destroy()
        self.fields.clear()
        self.search = self.enabled = self.summary = self.record_kind = self.translation = None
        self.color_enabled = self.color_source = None


class MemberEditor(tk.Toplevel):
    """Compatibility wrapper for standalone editor checks and existing callers."""
    def __init__(self, parent, path='', on_saved=None):
        super().__init__(parent)
        self.title('人名 / 专有词库')
        self.geometry('920x760')
        self.minsize(760, 540)
        self.panel = MemberPanel(self, path, on_saved, on_close=self.destroy)
        self.panel.pack(fill='both', expand=True)
        self.protocol('WM_DELETE_WINDOW', self.panel.close)

    def __getattr__(self, name):
        panel = self.__dict__.get('panel')
        if panel is not None and hasattr(panel, name):
            return getattr(panel, name)
        return super().__getattr__(name)
