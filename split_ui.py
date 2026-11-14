"""User-controlled audio boundary and second-half song selection."""
import tkinter as tk
from tkinter import ttk


class SplitPanel(ttk.LabelFrame):
    def __init__(self,parent,app):
        super().__init__(parent,text='分两段识别',padding=10)
        self.app=app
        self.columnconfigure(1,weight=1)
        self.enabled=ttk.Checkbutton(self,text='启用（两个 Soniox 任务）',variable=app.vars['soniox_split_enabled'],command=self.refresh_state)
        self.enabled.grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Label(self,text='分段时间').grid(row=1,column=0,sticky='w',padx=(0,12),pady=5)
        self.time_entry=ttk.Entry(self,textvariable=app.vars['soniox_split_time'])
        self.time_entry.grid(row=1,column=1,sticky='ew',pady=5)
        ttk.Label(self,text='第二段起始歌曲').grid(row=2,column=0,sticky='w',padx=(0,12),pady=5)
        self.choice=tk.StringVar(self)
        self.song_picker=ttk.Combobox(self,textvariable=self.choice,state='readonly')
        self.song_picker.grid(row=2,column=1,sticky='ew',pady=5)
        self.song_picker.bind('<<ComboboxSelected>>',self.select_song)
        self.hint=ttk.Label(self,text='时间填 秒、分:秒 或 时:分:秒，例如 01:10:00。请选择第二段歌曲开始前的停顿处。\n第一段发送该曲之前的歌词，第二段从该曲起发送；识别后自动合并时间轴，DeepSeek 仍整场一次请求。',style='Hint.TLabel',wraplength=700)
        self.hint.grid(row=3,column=0,columnspan=2,sticky='ew',pady=(4,0))
        self.range_hint=tk.StringVar(self)
        self.range_label=ttk.Label(self,textvariable=self.range_hint,style='Hint.TLabel',wraplength=700)
        self.range_label.grid(row=4,column=0,columnspan=2,sticky='ew',pady=(4,0))
        self.bind('<Configure>',lambda e:[label.configure(wraplength=max(180,e.width-26)) for label in (self.hint,self.range_label)])
        self.refresh()

    def refresh(self):
        key=self.app.vars['lyric_stage'].get()
        stage=next((s for s in self.app._stages if s['id']==key),None)
        self.songs=stage['songs'][1:] if stage else []
        self.song_picker['values']=[f'{i+2:02d} · {song["title"]}' for i,song in enumerate(self.songs)]
        current=self.app.vars['soniox_second_song'].get()
        index=next((i for i,s in enumerate(self.songs) if s['id']==current),None)
        if self.songs:
            index=index if index is not None else max(0,len(self.songs)//2)
            self.song_picker.current(index)
            self.app.vars['soniox_second_song'].set(self.songs[index]['id'])
        else:
            self.choice.set('请先选择公演')
            self.app.vars['soniox_second_song'].set('')
        self.refresh_state()

    def select_song(self,_event=None):
        index=self.song_picker.current()
        if 0<=index<len(self.songs):
            self.app.vars['soniox_second_song'].set(self.songs[index]['id'])
        self.update_range()

    def update_range(self):
        from lyrics import lyric_lines
        key=self.app.vars['lyric_stage'].get()
        stage=next((s for s in self.app._stages if s['id']==key),None)
        if not stage or not self.songs:
            self.range_hint.set('先选择至少包含两首曲目的公演。')
            return
        index=next((i for i,s in enumerate(stage['songs']) if s['id']==self.app.vars['soniox_second_song'].get()),0)
        ranges=(stage['songs'][:index],stage['songs'][index:])
        counts=[sum(len(lyric_lines(s.get('japanese',''))) for s in group) for group in ranges]
        self.range_hint.set(f'第一段：第 1–{index} 首，完整歌词 {counts[0]} 行；第二段：第 {index+1}–{len(stage["songs"])} 首，完整歌词 {counts[1]} 行。')

    def refresh_state(self):
        self.update_range()
        enabled=self.app.vars['soniox_split_enabled'].get() and not self.app.busy
        self.enabled.configure(state='disabled' if self.app.busy else 'normal')
        self.time_entry.configure(state='normal' if enabled else 'disabled')
        self.song_picker.configure(state='readonly' if enabled and self.songs else 'disabled')
