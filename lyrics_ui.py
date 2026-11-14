"""Local stage/song lyric editor. No downloads and no API requests."""
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, simpledialog, ttk

from lyrics import library_path, load_library, lyric_lines, save_library, chinese_lines
from ui import ScrollArea


class LyricsEditor(tk.Toplevel):
    def __init__(self, parent, path='', stage_id='', on_saved=None):
        super().__init__(parent)
        self.title('公演歌词库 · 查看 / 编辑')
        self.geometry('920x720')
        self.minsize(760, 540)
        self.path = str(library_path(path))
        self.document = load_library(path)
        self.on_saved = on_saved or (lambda *_: None)
        self.active_song = None
        self.stage_id = stage_id
        self.stage = tk.StringVar(self)
        self.summary = tk.StringVar(self)
        shell = ttk.Frame(self, padding=12)
        shell.pack(fill='both', expand=True)
        shell.rowconfigure(0, weight=1)
        shell.columnconfigure(0, weight=1)
        self.scroll_area = ScrollArea(shell)
        self.scroll_area.grid(row=0, column=0, sticky='nsew')
        root = self.scroll_area.body
        root.columnconfigure(0, weight=1)
        hint = ttk.Label(root, text='公演目录已内置；完整歌词请粘贴或导入你有权使用的 TXT / LRC。\n日文每行一句；中文可留空交给 DeepSeek 翻译，填写时须与日文逐行对应。',
                  style='Hint.TLabel', wraplength=650)
        hint.grid(row=0, column=0, sticky='ew', pady=6)
        path_frame = ttk.Frame(root)
        path_frame.grid(row=1, column=0, sticky='ew')
        path_frame.columnconfigure(0, weight=1)
        self.path_label = ttk.Entry(path_frame)
        self.path_label.insert(0, self.path)
        self.path_label.configure(state='readonly')
        self.path_label.grid(row=0, column=0, sticky='ew')
        ttk.Button(path_frame, text='打开库…', command=self.open_library).grid(row=0, column=1, padx=4)
        ttk.Button(path_frame, text='另存库…', command=self.save_as).grid(row=0, column=2)
        stage_frame = ttk.Frame(root)
        stage_frame.grid(row=2, column=0, sticky='ew', pady=8)
        stage_frame.columnconfigure(0, weight=1)
        self.stage_picker = ttk.Combobox(stage_frame, textvariable=self.stage, state='readonly')
        self.stage_picker.grid(row=0, column=0, sticky='ew')
        self.stage_picker.bind('<<ComboboxSelected>>', self.change_stage)
        ttk.Button(stage_frame, text='新增公演', command=self.add_stage).grid(row=0, column=1, padx=4)
        ttk.Button(stage_frame, text='改名', command=self.rename_stage).grid(row=0, column=2)
        songs_frame = ttk.Frame(root)
        songs_frame.grid(row=3, column=0, sticky='ew')
        songs_frame.columnconfigure(0, weight=1)
        self.songs = ttk.Treeview(songs_frame, columns=('title', 'lines'), show='headings', height=5, selectmode='browse')
        self.songs.heading('title', text='曲目（按公演顺序）')
        self.songs.heading('lines', text='日文行数')
        self.songs.column('title', width=400, stretch=True)
        self.songs.column('lines', width=100, stretch=False)
        self.songs.grid(row=0, column=0, sticky='ew')
        bar = ttk.Scrollbar(songs_frame, orient='vertical', command=self.songs.yview)
        bar.grid(row=0, column=1, sticky='ns')
        self.songs.configure(yscrollcommand=bar.set)
        self.songs.bind('<<TreeviewSelect>>', self.change_song)
        toolbar = ttk.Frame(root)
        toolbar.grid(row=4, column=0, sticky='ew', pady=6)
        for i, (text, command) in enumerate((('新增曲目', self.add_song), ('改曲名', self.rename_song),
                                            ('上移', lambda: self.move_song(-1)), ('下移', lambda: self.move_song(1)),
                                            ('删除', self.delete_song))):
            ttk.Button(toolbar, text=text, command=command).grid(row=i // 3, column=i % 3, padx=(0, 4), pady=2)
        self.editors = ttk.Notebook(root)
        self.editors.grid(row=5, column=0, sticky='ew')
        self.texts = {}
        for key, title in (('japanese', '日文歌词'), ('chinese', '中文译文（可选）')):
            page = ttk.Frame(self.editors)
            page.columnconfigure(0, weight=1)
            text = scrolledtext.ScrolledText(page, height=8, width=25, wrap='word', font=('Microsoft YaHei UI', 10))
            text.grid(row=0, column=0, sticky='ew')
            ttk.Button(page, text='导入 TXT / LRC…', command=lambda k=key: self.import_text(k)).grid(row=1, column=0, sticky='w', pady=4)
            self.texts[key] = text
            self.editors.add(page, text=title)
        summary_label = ttk.Label(root, textvariable=self.summary, style='Hint.TLabel', wraplength=650)
        summary_label.grid(row=6, column=0, sticky='ew', pady=6)
        def wrap(event):
            width = max(200, event.width - 12)
            hint.configure(wraplength=width)
            summary_label.configure(wraplength=width)
        root.bind('<Configure>', wrap, add='+')
        footer = ttk.Frame(shell)
        footer.grid(row=1, column=0, sticky='ew', pady=(10, 0))
        self.save_button = ttk.Button(footer, text='保存歌词库', command=self.save)
        self.save_button.pack(side='left')
        ttk.Button(footer, text='关闭', command=self.close).pack(side='right')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.fill_stages()

    def capture(self):
        if self.active_song:
            for key, widget in self.texts.items():
                value = widget.get('1.0', 'end-1c')
                self.active_song[key] = value if key == 'chinese' and self.active_song.get('chinese_partial') else value.strip()

    def current_stage(self):
        return next((stage for stage in self.document['stages'] if stage['id'] == self.stage_id), None)

    def fill_stages(self):
        self.stage_picker['values'] = [stage['name'] for stage in self.document['stages']]
        stage = self.current_stage()
        if stage is None and self.document['stages']:
            stage = self.document['stages'][0]
            self.stage_id = stage['id']
        self.stage.set(stage['name'] if stage else '')
        self.fill_songs()

    def change_stage(self, _event=None):
        self.capture()
        selected = self.stage_picker.current()
        if selected >= 0:
            self.stage_id = self.document['stages'][selected]['id']
        self.fill_songs()

    def fill_songs(self, select=None):
        self.active_song = None
        self.songs.delete(*self.songs.get_children())
        stage = self.current_stage()
        if stage:
            for song in stage['songs']:
                self.songs.insert('', 'end', iid=song['id'], values=(song['title'], len(lyric_lines(song.get('japanese', '')))))
        for text in self.texts.values():
            text.delete('1.0', 'end')
        ids = self.songs.get_children()
        if ids:
            self.songs.selection_set(select if select in ids else ids[0])
            self.change_song()

    def change_song(self, _event=None):
        selected = self.songs.selection()
        if not selected:
            return
        stage = self.current_stage()
        song = next((s for s in stage['songs'] if s['id'] == selected[0]), None) if stage else None
        if song is self.active_song:
            return
        self.capture()
        self.active_song = song
        for key, text in self.texts.items():
            text.delete('1.0', 'end')
            text.insert('1.0', song.get(key, '') if song else '')
        self.summary.set('此曲为部分中文：空行对应尚未提供的日文行，生成时交给 DeepSeek 翻译；请保留空行位置。'
                         if song and song.get('chinese_partial') else '连续多行可靠匹配才采用歌词；未匹配内容保留原文。LRC 时间只用于去除标签，不作为本场公演的时间轴。')

    def save(self):
        try:
            self.capture()
            for stage in self.document['stages']:
                for song in stage['songs']:
                    ja, zh = lyric_lines(song.get('japanese', '')), chinese_lines(song)
                    if zh and len(ja) != len(zh):
                        raise ValueError(f'《{song["title"]}》日文 {len(ja)} 行、中文 {len(zh)} 行，需逐行对应')
            save_library(self.document, self.path)
            self.on_saved(self.path, self.stage_id)
            self.summary.set('已保存：' + self.path)
            return True
        except Exception as exc:
            messagebox.showerror('无法保存歌词库', str(exc), parent=self)
            return False

    def close(self):
        self.capture()
        try:
            original = load_library(self.path) if Path(self.path).exists() else load_library()
            changed = original != self.document
        except Exception:
            changed = True
        if changed:
            answer = messagebox.askyesnocancel('保存歌词库', '歌词库有修改，是否保存后关闭？', parent=self)
            if answer is None or (answer and not self.save()):
                return
        self.destroy()

    def destroy(self):
        super().destroy()
        # Release the editor's Tcl variables while still on the GUI thread.
        self.stage = None
        self.summary = None

    def open_library(self):
        self.capture()
        path = filedialog.askopenfilename(parent=self, title='打开另一个歌词库', filetypes=[('歌词库 JSON', '*.json')])
        if path:
            try:
                document = load_library(path)
                if not self.save():
                    return
                self.document, self.path, self.stage_id = document, path, ''
                self.path_label.configure(state='normal')
                self.path_label.delete(0, 'end')
                self.path_label.insert(0, path)
                self.path_label.configure(state='readonly')
                self.fill_stages()
                self.on_saved(self.path, self.stage_id)
            except Exception as exc:
                messagebox.showerror('无法打开歌词库', str(exc), parent=self)

    def save_as(self):
        path = filedialog.asksaveasfilename(parent=self, title='另存歌词库', initialfile='lyrics-library.json', defaultextension='.json', filetypes=[('歌词库 JSON', '*.json')])
        if path:
            previous = self.path
            self.path = path
            if self.save():
                self.path_label.configure(state='normal')
                self.path_label.delete(0, 'end')
                self.path_label.insert(0, path)
                self.path_label.configure(state='readonly')
            else:
                self.path = previous

    def import_text(self, key):
        if not self.active_song:
            return
        path = filedialog.askopenfilename(parent=self, title='导入你有权使用的歌词', filetypes=[('歌词文件', '*.txt *.lrc')])
        if path:
            try:
                source = Path(path)
                if source.stat().st_size > 512000:
                    raise ValueError('单曲歌词文件过大')
                raw = source.read_bytes()
                for encoding in ('utf-8-sig', 'utf-16', 'cp932', 'gb18030'):
                    try:
                        text = raw.decode(encoding)
                        if '\0' in text:
                            continue
                        break
                    except UnicodeError:
                        continue
                else:
                    raise ValueError('无法识别文本编码，请另存为 UTF-8')
                widget = self.texts[key]
                widget.delete('1.0', 'end')
                widget.insert('1.0', '\n'.join(lyric_lines(text)))
            except Exception as exc:
                messagebox.showerror('无法导入歌词', str(exc), parent=self)

    def add_stage(self):
        name = simpledialog.askstring('新增公演', '公演名称', parent=self)
        if name and name.strip():
            self.capture()
            self.stage_id = uuid.uuid4().hex
            self.document['stages'].append({'id': self.stage_id, 'name': name.strip(), 'songs': []})
            self.fill_stages()

    def rename_stage(self):
        stage = self.current_stage()
        if stage:
            name = simpledialog.askstring('公演改名', '公演名称', initialvalue=stage['name'], parent=self)
            if name and name.strip():
                self.capture()
                stage['name'] = name.strip()
                self.fill_stages()

    def add_song(self):
        stage = self.current_stage()
        if not stage:
            return
        title = simpledialog.askstring('新增曲目', '曲目名称', parent=self)
        if title and title.strip():
            self.capture()
            song = {'id': uuid.uuid4().hex, 'title': title.strip(), 'japanese': '', 'chinese': ''}
            stage['songs'].append(song)
            self.fill_songs(song['id'])

    def rename_song(self):
        if self.active_song:
            title = simpledialog.askstring('修改曲名', '曲目名称', initialvalue=self.active_song['title'], parent=self)
            if title and title.strip():
                self.capture()
                self.active_song['title'] = title.strip()
                self.fill_songs(self.active_song['id'])

    def move_song(self, offset):
        if self.active_song:
            self.capture()
            songs = self.current_stage()['songs']
            index = songs.index(self.active_song)
            other = index + offset
            if 0 <= other < len(songs):
                songs[index], songs[other] = songs[other], songs[index]
                self.fill_songs(songs[other]['id'])

    def delete_song(self):
        if self.active_song and messagebox.askyesno('删除曲目', '删除此曲目及其歌词？保存后生效。', parent=self):
            self.current_stage()['songs'].remove(self.active_song)
            self.fill_songs()
