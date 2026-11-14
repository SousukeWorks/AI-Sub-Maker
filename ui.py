"""A resizable scrolling content area; the action bar lives outside it."""
import tkinter as tk
from tkinter import ttk


class ScrollArea(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(self, background="#f4f6fa", highlightthickness=0, width=1, height=1)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.body = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self.body.bind("<Configure>", self._content_changed)
        self.canvas.bind("<Configure>", self._resized)
        self._global_bindings = [(sequence, self.bind_all(sequence, callback, add='+'))
                                 for sequence, callback in (('<MouseWheel>', self._wheel), ('<FocusIn>', self._focus))]
        self.bind('<Destroy>', self._cleanup_bindings, add='+')

    def _cleanup_bindings(self, event):
        if event.widget is self:
            for sequence, binding in self._global_bindings:
                self._root()._unbind(('bind', 'all', sequence), binding)
            self._global_bindings.clear()

    def contains(self, widget):
        while widget is not None:
            if widget is self.body:
                return True
            widget = getattr(widget, "master", None)
        return False

    def _content_changed(self, _event):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resized(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)

    def _wheel(self, event):
        if self.contains(event.widget) and not isinstance(event.widget, (ttk.Combobox, ttk.Treeview, tk.Text, tk.Listbox)):
            if self.body.winfo_height() > self.canvas.winfo_height():
                steps = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
                self.canvas.yview_scroll(steps, "units")
                return "break"

    def _focus(self, event):
        if self.contains(event.widget):
            self.after_idle(self.ensure_visible, event.widget)

    def ensure_visible(self, widget):
        if not widget.winfo_exists() or not self.contains(widget):
            return
        top = widget.winfo_rooty() - self.canvas.winfo_rooty()
        bottom = top + widget.winfo_height()
        viewport = self.canvas.winfo_height()
        delta = top - 8 if top < 8 else bottom - viewport + 8 if bottom > viewport - 8 else 0
        if delta:
            content_height = max(1, self.body.winfo_height())
            position = self.canvas.canvasy(0) + delta
            self.canvas.yview_moveto(max(0, position / content_height))
