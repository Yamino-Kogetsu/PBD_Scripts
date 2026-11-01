#!/usr/bin/env python3
"""
baidu_share_gui.py - Giao diện đồ hoạ cho Baidu Pan Share Lister.

Yêu cầu:  Python 3.9+, tkinter (có sẵn trong Python; Linux: sudo apt install python3-tk)
          pip install requests
          và file baidu_cli.py nằm cùng thư mục với file này.
Chạy:     python baidu_share_gui.py
"""
import json
import os
import queue
import re
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import requests
    from baidu_cli import (
        BaiduError,
        BaiduShare,
        build_report,
        human_size,
        parse_link,
        write_json,
        write_txt,
    )
except ImportError as exc:  # thiếu requests hoặc thiếu file lõi
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Thiếu thư viện / file",
        f"{exc}\n\nCài đặt:  pip install requests\n"
        "và đặt baidu_cli.py cùng thư mục với file này.",
    )
    sys.exit(1)

SETTINGS_FILE = Path.home() / ".baidu_share_gui.json"

PALETTES = {
    "light": dict(
        bg="#f3f5f9", card="#ffffff", card2="#eef1f7", border="#d9dfeb",
        text="#1c2333", muted="#6b7488", accent="#4f6df5", accent_hover="#3f5ae0",
        accent_fg="#ffffff", danger="#d93f45", ok="#13895a", warn="#b7791f",
        sel="#dfe6ff", sel_fg="#1c2333", entry_bg="#ffffff", log_bg="#fbfcfe",
    ),
    "dark": dict(
        bg="#12151c", card="#1a1f2b", card2="#232a38", border="#2d3547",
        text="#e6e9f2", muted="#8b94a8", accent="#6b83ff", accent_hover="#8396ff",
        accent_fg="#ffffff", danger="#ff6b70", ok="#3ecf8e", warn="#f2b84b",
        sel="#2b3a6b", sel_fg="#ffffff", entry_bg="#141926", log_bg="#0f131b",
    ),
}


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_settings(data: dict):
    try:
        SETTINGS_FILE.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Baidu Pan Share Lister")
        self.geometry("1020x760")
        self.minsize(880, 620)

        self.theme = load_settings().get("theme", "light")
        if self.theme not in PALETTES:
            self.theme = "light"

        self.q = queue.Queue()
        self.client = None
        self.worker = None
        self.report = None
        self.node_by_iid = {}
        self._fill_token = 0
        self._search_job = None
        self._auto_sub = None
        self._auto_pwd = None
        self._adv_open = False
        self._ctx_iid = None

        self._init_fonts()
        self.style = ttk.Style(self)
        self.style.theme_use("clam")
        self._make_icons()
        self._make_vars()
        self._build()
        self.apply_theme()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.after(100, self._poll)
        self.link_entry.focus_set()

    # ------------------------------------------------------------------ #
    # Khởi tạo
    # ------------------------------------------------------------------ #
    def _init_fonts(self):
        fams = set(tkfont.families())

        def pick(options, fallback):
            for f in options:
                if f in fams:
                    return f
            return fallback

        base = pick(["Segoe UI", "SF Pro Text", "Helvetica Neue", "Inter", "Ubuntu",
                     "Noto Sans", "DejaVu Sans"], tkfont.nametofont("TkDefaultFont").actual("family"))
        mono = pick(["Cascadia Mono", "Consolas", "Menlo", "DejaVu Sans Mono", "Courier New"],
                    "TkFixedFont")
        self.f_base = (base, 10)
        self.f_bold = (base, 10, "bold")
        self.f_small = (base, 9)
        self.f_title = (base, 19, "bold")
        self.f_btn = (base, 10, "bold")
        self.f_mono = (mono, 9)

    def _make_icons(self):
        folder = tk.PhotoImage(width=18, height=16)
        folder.put("#e8a92a", to=(1, 2, 8, 5))
        folder.put("#f7c85c", to=(1, 4, 17, 14))
        folder.put("#e8a92a", to=(1, 4, 17, 6))
        file_ = tk.PhotoImage(width=18, height=16)
        file_.put("#9aa6bf", to=(3, 1, 15, 15))
        file_.put("#ffffff", to=(4, 2, 14, 14))
        file_.put("#c3cada", to=(5, 5, 13, 6))
        file_.put("#c3cada", to=(5, 8, 13, 9))
        file_.put("#c3cada", to=(5, 11, 10, 12))
        self.icon_folder, self.icon_file = folder, file_

    def _make_vars(self):
        self.link_var = tk.StringVar()
        self.pwd_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.cookie_var = tk.StringVar()
        self.delay_var = tk.DoubleVar(value=0.4)
        self.depth_var = tk.IntVar(value=0)
        self.search_var = tk.StringVar()
        self.status_var = tk.StringVar(value="Sẵn sàng. Dán link Baidu Pan rồi nhấn “Quét danh sách”.")
        self.link_var.trace_add("write", self._on_link_changed)
        self.search_var.trace_add("write", self._on_search_changed)

    # ------------------------------------------------------------------ #
    # Dựng giao diện
    # ------------------------------------------------------------------ #
    def _build(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # ---- Header
        hdr = ttk.Frame(self)
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 14))
        hdr.columnconfigure(0, weight=1)
        ttk.Label(hdr, text="Baidu Pan Share Lister", style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        ttk.Label(hdr, text="Liệt kê tệp & thư mục từ link chia sẻ, xuất ra JSON hoặc TXT",
                  style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.theme_btn = ttk.Button(hdr, style="Ghost.TButton", command=self.toggle_theme)
        self.theme_btn.grid(row=0, column=1, rowspan=2, sticky="e")

        # ---- Thẻ nhập liệu
        card = ttk.Frame(self, style="Card.TFrame", padding=18)
        card.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        card.columnconfigure(1, weight=1)

        ttk.Label(card, text="Link chia sẻ", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=(0, 10))
        self.link_entry = ttk.Entry(card, textvariable=self.link_var, font=self.f_base)
        self.link_entry.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        self.link_entry.bind("<Return>", lambda e: self.start_scan())
        ttk.Button(card, text="Dán", command=self.paste_link).grid(
            row=0, column=2, padx=(10, 0), pady=(0, 10))

        ttk.Label(card, text="Mã trích xuất", style="Card.TLabel").grid(
            row=1, column=0, sticky="w", padx=(0, 14))
        row1 = ttk.Frame(card, style="Card.TFrame")
        row1.grid(row=1, column=1, columnspan=2, sticky="ew")
        row1.columnconfigure(2, weight=1)
        self.pwd_entry = ttk.Entry(row1, textvariable=self.pwd_var, width=10, font=self.f_base)
        self.pwd_entry.grid(row=0, column=0, sticky="w")
        self.pwd_entry.bind("<Return>", lambda e: self.start_scan())
        ttk.Label(row1, text="Thư mục con (tuỳ chọn)", style="Card.TLabel").grid(
            row=0, column=1, padx=(18, 10))
        self.sub_entry = ttk.Entry(row1, textvariable=self.sub_var, font=self.f_base)
        self.sub_entry.grid(row=0, column=2, sticky="ew")
        self.sub_entry.bind("<Return>", lambda e: self.start_scan())
        ttk.Button(row1, text="Xoá", command=lambda: self.sub_var.set("")).grid(
            row=0, column=3, padx=(10, 0))

        self.adv_btn = ttk.Button(card, style="Link.TButton", command=self.toggle_advanced)
        self.adv_btn.grid(row=2, column=0, columnspan=3, sticky="w", pady=(12, 0))
        self.adv_frame = ttk.Frame(card, style="Card.TFrame")
        self.adv_frame.columnconfigure(1, weight=1)
        ttk.Label(self.adv_frame, text="Cookie", style="Card.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 14), pady=(10, 0))
        ttk.Entry(self.adv_frame, textvariable=self.cookie_var, font=self.f_base).grid(
            row=0, column=1, columnspan=4, sticky="ew", pady=(10, 0))
        ttk.Label(self.adv_frame, text="Nghỉ giữa request (giây)", style="Card.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Spinbox(self.adv_frame, from_=0, to=10, increment=0.1, width=6,
                    textvariable=self.delay_var).grid(row=1, column=2, sticky="w", padx=(8, 24), pady=(10, 0))
        ttk.Label(self.adv_frame, text="Độ sâu tối đa (0 = không giới hạn)", style="Card.TLabel").grid(
            row=1, column=3, sticky="w", pady=(10, 0))
        ttk.Spinbox(self.adv_frame, from_=0, to=50, increment=1, width=5,
                    textvariable=self.depth_var).grid(row=1, column=4, sticky="w", padx=(8, 0), pady=(10, 0))
        self._refresh_adv_btn()

        # ---- Thanh hành động
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        bar.columnconfigure(2, weight=1)
        self.scan_btn = ttk.Button(bar, text="Quét danh sách", style="Accent.TButton",
                                   command=self.start_scan)
        self.scan_btn.grid(row=0, column=0)
        self.cancel_btn = ttk.Button(bar, text="Huỷ", style="Danger.TButton",
                                     command=self.cancel_scan, state="disabled")
        self.cancel_btn.grid(row=0, column=1, padx=(8, 0))
        self.progress = ttk.Progressbar(bar, mode="indeterminate",
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=2, sticky="ew", padx=(16, 0))
        self.progress.grid_remove()
        self.status_lbl = ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel")
        self.status_lbl.grid(row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))

        # ---- Notebook: cây thư mục / nhật ký
        self.nb = ttk.Notebook(self)
        self.nb.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 12))
        tab_tree = ttk.Frame(self.nb, style="Card.TFrame")
        tab_log = ttk.Frame(self.nb, style="Card.TFrame")
        self.nb.add(tab_tree, text="Cây thư mục")
        self.nb.add(tab_log, text="Nhật ký")

        # -- tab cây
        tab_tree.columnconfigure(0, weight=1)
        tab_tree.rowconfigure(1, weight=1)
        tool = ttk.Frame(tab_tree, style="Card.TFrame", padding=(12, 10))
        tool.grid(row=0, column=0, sticky="ew")
        tool.columnconfigure(1, weight=1)
        ttk.Label(tool, text="Tìm", style="Card.TLabel").grid(row=0, column=0, padx=(0, 10))
        self.search_entry = ttk.Entry(tool, textvariable=self.search_var, font=self.f_base)
        self.search_entry.grid(row=0, column=1, sticky="ew")
        ttk.Button(tool, text="Mở rộng", command=lambda: self._set_open(True)).grid(
            row=0, column=2, padx=(10, 0))
        ttk.Button(tool, text="Thu gọn", command=lambda: self._set_open(False)).grid(
            row=0, column=3, padx=(8, 0))

        tree_wrap = ttk.Frame(tab_tree, style="Card.TFrame")
        tree_wrap.grid(row=1, column=0, sticky="nsew")
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(tree_wrap, columns=("size", "modified"), selectmode="browse")
        self.tree.heading("#0", text="Tên", anchor="w")
        self.tree.heading("size", text="Dung lượng", anchor="e")
        self.tree.heading("modified", text="Cập nhật", anchor="w")
        self.tree.column("#0", width=460, minwidth=200, stretch=True)
        self.tree.column("size", width=110, minwidth=80, anchor="e", stretch=False)
        self.tree.column("modified", width=175, minwidth=150, anchor="w", stretch=False)
        vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        self.empty_lbl = ttk.Label(
            tree_wrap, style="Empty.TLabel", justify="center",
            text="Chưa có dữ liệu\nDán link Baidu Pan ở trên và nhấn “Quét danh sách”")
        self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Sao chép tên", command=lambda: self._copy_node("name"))
        self.menu.add_command(label="Sao chép đường dẫn", command=lambda: self._copy_node("path"))
        self.menu.add_separator()
        self.menu.add_command(label="Chỉ quét thư mục này", command=self._scan_selected_folder)
        self._scan_idx = 3
        for seq in ("<Button-3>", "<Button-2>", "<Control-Button-1>"):
            self.tree.bind(seq, self._on_right_click)

        # -- tab log
        tab_log.columnconfigure(0, weight=1)
        tab_log.rowconfigure(1, weight=1)
        ltool = ttk.Frame(tab_log, style="Card.TFrame", padding=(12, 10))
        ltool.grid(row=0, column=0, columnspan=2, sticky="ew")
        ltool.columnconfigure(0, weight=1)
        ttk.Button(ltool, text="Xoá nhật ký", command=self.clear_log).grid(row=0, column=1)
        self.log_text = tk.Text(tab_log, wrap="word", relief="flat", borderwidth=0,
                                padx=12, pady=8, font=self.f_mono, state="disabled")
        lsb = ttk.Scrollbar(tab_log, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=lsb.set)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        lsb.grid(row=1, column=1, sticky="ns")

        # ---- Chân trang: thống kê + xuất file
        foot = ttk.Frame(self)
        foot.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 20))
        foot.columnconfigure(3, weight=1)
        c1, self.chip_files = self._chip(foot, "Tệp")
        c2, self.chip_dirs = self._chip(foot, "Thư mục")
        c3, self.chip_size = self._chip(foot, "Dung lượng")
        c1.grid(row=0, column=0)
        c2.grid(row=0, column=1, padx=8)
        c3.grid(row=0, column=2)
        self.txt_btn = ttk.Button(foot, text="Xuất TXT", state="disabled",
                                  command=lambda: self.export("txt"))
        self.json_btn = ttk.Button(foot, text="Xuất JSON", style="Accent.TButton", state="disabled",
                                   command=lambda: self.export("json"))
        self.txt_btn.grid(row=0, column=4, padx=(0, 8))
        self.json_btn.grid(row=0, column=5)

    def _chip(self, parent, title):
        f = ttk.Frame(parent, style="Chip.TFrame", padding=(12, 7))
        ttk.Label(f, text=title, style="ChipTitle.TLabel").pack(side="left")
        v = ttk.Label(f, text="—", style="ChipValue.TLabel")
        v.pack(side="left", padx=(8, 0))
        return f, v

    # ------------------------------------------------------------------ #
    # Giao diện sáng / tối
    # ------------------------------------------------------------------ #
    def apply_theme(self):
        p = self.p = PALETTES[self.theme]
        st = self.style
        self.configure(bg=p["bg"])

        st.configure(".", background=p["bg"], foreground=p["text"], font=self.f_base,
                     bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                     troughcolor=p["card2"], focuscolor=p["bg"], borderwidth=1)
        st.configure("TFrame", background=p["bg"])
        st.configure("Card.TFrame", background=p["card"])
        st.configure("Chip.TFrame", background=p["card2"])

        st.configure("TLabel", background=p["bg"], foreground=p["text"])
        st.configure("Card.TLabel", background=p["card"], foreground=p["text"])
        st.configure("Title.TLabel", background=p["bg"], foreground=p["text"], font=self.f_title)
        st.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        st.configure("Status.TLabel", background=p["bg"], foreground=p["muted"], font=self.f_base)
        st.configure("Empty.TLabel", background=p["card"], foreground=p["muted"],
                     font=(self.f_base[0], 11), anchor="center")
        st.configure("ChipTitle.TLabel", background=p["card2"], foreground=p["muted"], font=self.f_small)
        st.configure("ChipValue.TLabel", background=p["card2"], foreground=p["text"], font=self.f_bold)

        entry_kw = dict(fieldbackground=p["entry_bg"], foreground=p["text"], insertcolor=p["text"],
                        bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                        padding=(8, 7), arrowcolor=p["muted"])
        for name in ("TEntry", "TSpinbox"):
            st.configure(name, **entry_kw)
            st.map(name, bordercolor=[("focus", p["accent"])],
                   lightcolor=[("focus", p["accent"])], darkcolor=[("focus", p["accent"])])

        st.configure("TButton", background=p["card2"], foreground=p["text"], font=self.f_base,
                     bordercolor=p["border"], lightcolor=p["card2"], darkcolor=p["card2"],
                     padding=(14, 8), relief="flat")
        st.map("TButton", background=[("active", p["border"]), ("disabled", p["card2"])],
               foreground=[("disabled", p["muted"])])

        st.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                     font=self.f_btn, bordercolor=p["accent"], lightcolor=p["accent"],
                     darkcolor=p["accent"], padding=(20, 9))
        st.map("Accent.TButton",
               background=[("disabled", p["card2"]), ("active", p["accent_hover"])],
               foreground=[("disabled", p["muted"])],
               bordercolor=[("disabled", p["border"]), ("active", p["accent_hover"])],
               lightcolor=[("disabled", p["card2"]), ("active", p["accent_hover"])],
               darkcolor=[("disabled", p["card2"]), ("active", p["accent_hover"])])

        st.configure("Danger.TButton", foreground=p["danger"], padding=(16, 9), font=self.f_btn)
        st.map("Danger.TButton", foreground=[("disabled", p["muted"])])

        st.configure("Ghost.TButton", background=p["bg"], foreground=p["muted"],
                     bordercolor=p["bg"], lightcolor=p["bg"], darkcolor=p["bg"], padding=(10, 6))
        st.map("Ghost.TButton", background=[("active", p["card2"])],
               foreground=[("active", p["text"])])

        st.configure("Link.TButton", background=p["card"], foreground=p["accent"],
                     bordercolor=p["card"], lightcolor=p["card"], darkcolor=p["card"], padding=(0, 2))
        st.map("Link.TButton", background=[("active", p["card"])],
               foreground=[("active", p["accent_hover"])])

        st.configure("Treeview", background=p["card"], fieldbackground=p["card"],
                     foreground=p["text"], rowheight=28, borderwidth=0, relief="flat")
        st.map("Treeview", background=[("selected", p["sel"])],
               foreground=[("selected", p["sel_fg"])])
        st.configure("Treeview.Heading", background=p["card2"], foreground=p["muted"],
                     font=self.f_bold, relief="flat", padding=(8, 7), borderwidth=0)
        st.map("Treeview.Heading", background=[("active", p["border"])])

        st.configure("TNotebook", background=p["bg"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                     padding=(18, 9), font=self.f_bold, borderwidth=0)
        st.map("TNotebook.Tab", background=[("selected", p["card"])],
               foreground=[("selected", p["accent"])])

        for orient in ("Vertical", "Horizontal"):
            st.configure(f"{orient}.TScrollbar", background=p["card2"], troughcolor=p["card"],
                         bordercolor=p["card"], lightcolor=p["card2"], darkcolor=p["card2"],
                         arrowcolor=p["muted"], relief="flat", gripcount=0)
            st.map(f"{orient}.TScrollbar", background=[("active", p["border"])])

        st.configure("Accent.Horizontal.TProgressbar", troughcolor=p["card2"],
                     background=p["accent"], bordercolor=p["card2"],
                     lightcolor=p["accent"], darkcolor=p["accent"], thickness=6)

        # widget tk thuần
        self.log_text.configure(bg=p["log_bg"], fg=p["text"], insertbackground=p["text"],
                                selectbackground=p["sel"], selectforeground=p["sel_fg"])
        self.log_text.tag_configure("dir", foreground=p["accent"])
        self.log_text.tag_configure("err", foreground=p["danger"])
        self.log_text.tag_configure("ok", foreground=p["ok"])
        self.log_text.tag_configure("time", foreground=p["muted"])
        self.menu.configure(bg=p["card"], fg=p["text"], activebackground=p["accent"],
                            activeforeground=p["accent_fg"], bd=0, relief="flat")

        self.theme_btn.configure(text="Chế độ sáng" if self.theme == "dark" else "Chế độ tối")
        self._refresh_status_color()

    def toggle_theme(self):
        self.theme = "dark" if self.theme == "light" else "light"
        self.apply_theme()
        save_settings({"theme": self.theme})

    # ------------------------------------------------------------------ #
    # Trạng thái / log
    # ------------------------------------------------------------------ #
    def set_status(self, text, kind="info"):
        self._status_kind = kind
        self.status_var.set(text)
        self._refresh_status_color()

    def _refresh_status_color(self):
        kind = getattr(self, "_status_kind", "info")
        color = {"info": self.p["muted"], "ok": self.p["ok"],
                 "err": self.p["danger"], "warn": self.p["warn"]}.get(kind, self.p["muted"])
        self.style.configure("Status.TLabel", foreground=color)

    def log(self, msg, tag=None):
        if tag is None:
            if msg.lstrip().startswith("[DIR"):
                tag = "dir"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", datetime.now().strftime("[%H:%M:%S] "), "time")
        self.log_text.insert("end", msg.strip() + "\n", tag or ())
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ #
    # Tương tác nhập liệu
    # ------------------------------------------------------------------ #
    def paste_link(self):
        try:
            self.link_var.set(self.clipboard_get().strip())
        except tk.TclError:
            self.set_status("Clipboard đang trống.", "warn")

    def _on_link_changed(self, *_):
        link = self.link_var.get().strip()
        if "baidu.com" not in link:
            return
        try:
            _, _, pwd, sub = parse_link(link, None)
        except Exception:
            return
        # Tự điền mã trích xuất
        if pwd and (not self.pwd_var.get().strip() or self.pwd_var.get() == self._auto_pwd):
            self.pwd_var.set(pwd)
            self._auto_pwd = pwd
        # Tự điền thư mục con từ #list/path=...
        if sub:
            self.sub_var.set(sub)
            self._auto_sub = sub
        elif self._auto_sub and self.sub_var.get() == self._auto_sub:
            self.sub_var.set("")
            self._auto_sub = None

    def toggle_advanced(self):
        self._adv_open = not self._adv_open
        if self._adv_open:
            self.adv_frame.grid(row=3, column=0, columnspan=3, sticky="ew")
        else:
            self.adv_frame.grid_remove()
        self._refresh_adv_btn()

    def _refresh_adv_btn(self):
        self.adv_btn.configure(text=("▾ Tuỳ chọn nâng cao" if self._adv_open else "▸ Tuỳ chọn nâng cao"))

    # ------------------------------------------------------------------ #
    # Quét
    # ------------------------------------------------------------------ #
    def start_scan(self):
        if self.worker and self.worker.is_alive():
            return
        link = self.link_var.get().strip()
        if not link:
            self.set_status("Vui lòng nhập link chia sẻ.", "err")
            self.link_entry.focus_set()
            return
        try:
            delay = max(0.0, float(self.delay_var.get()))
        except Exception:
            delay = 0.4
        try:
            depth = max(0, int(self.depth_var.get()))
        except Exception:
            depth = 0

        self.report = None
        self._fill_token += 1
        self.tree.delete(*self.tree.get_children())
        self.node_by_iid.clear()
        self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")
        self.empty_lbl.configure(text="Đang quét…")
        for chip in (self.chip_files, self.chip_dirs, self.chip_size):
            chip.configure(text="—")
        self._set_running(True)
        self.set_status("Đang kết nối và xác thực…", "info")
        self.log("Bắt đầu quét: " + link)

        self.worker = threading.Thread(
            target=self._scan_worker,
            kwargs=dict(link=link, pwd=self.pwd_var.get().strip() or None,
                        sub=self.sub_var.get().strip() or None,
                        cookie=self.cookie_var.get().strip() or None,
                        delay=delay, depth=depth),
            daemon=True,
        )
        self.worker.start()

    def _scan_worker(self, link, pwd, sub, cookie, delay, depth):
        client = None
        try:
            full, surl, pwd2, sub2 = parse_link(link, pwd)
            sub = sub or sub2
            client = BaiduShare(full, surl, pwd2, cookie=cookie, delay=delay, verbose=False)
            client.log_cb = lambda m: self.q.put(("log", m))
            client.max_depth = depth or None
            self.client = client
            client.verify()
            real, rel = client.resolve_start(sub)
            tree = client.crawl(real, rel)
            report = build_report(link, surl, tree, client.n_files, client.n_dirs, rel)
            self.q.put(("done", report))
        except BaiduError as e:
            self.q.put(("cancel" if client and client.stop_flag else "error", str(e)))
        except requests.RequestException as e:
            self.q.put(("error", f"Lỗi mạng: {e}"))
        except Exception as e:  # noqa: BLE001
            self.q.put(("error", f"Lỗi không mong đợi: {e}\n{traceback.format_exc()}"))

    def cancel_scan(self):
        if self.client:
            self.client.stop_flag = True
            self.cancel_btn.configure(state="disabled")
            self.set_status("Đang huỷ…", "warn")

    def _set_running(self, running):
        self.scan_btn.configure(state="disabled" if running else "normal")
        self.cancel_btn.configure(state="normal" if running else "disabled")
        if running:
            self.progress.grid()
            self.progress.start(12)
            self.txt_btn.configure(state="disabled")
            self.json_btn.configure(state="disabled")
        else:
            self.progress.stop()
            self.progress.grid_remove()

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log(payload)
                    if payload.lstrip().startswith("[DIR"):
                        self.set_status("Đang quét: " + payload.split("]", 1)[1].strip(), "info")
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "cancel":
                    self._set_running(False)
                    self.empty_lbl.configure(text="Đã huỷ quét.")
                    self.set_status(payload, "warn")
                    self.log(payload, "err")
                elif kind == "error":
                    self._set_running(False)
                    self.empty_lbl.configure(text="Quét thất bại.\nXem tab “Nhật ký” để biết chi tiết.")
                    self.set_status(payload.splitlines()[0], "err")
                    self.log(payload, "err")
        except queue.Empty:
            pass
        if self.worker and self.worker.is_alive() and self.client:
            self.chip_files.configure(text=str(self.client.n_files))
            self.chip_dirs.configure(text=str(self.client.n_dirs))
        self.after(100, self._poll)

    def _on_done(self, report):
        self._set_running(False)
        self.report = report
        self.chip_files.configure(text=f"{report['total_files']:,}")
        self.chip_dirs.configure(text=f"{report['total_folders']:,}")
        self.chip_size.configure(text=report["total_size_human"])
        self.txt_btn.configure(state="normal")
        self.json_btn.configure(state="normal")
        self.nb.select(0)
        self.search_var.set("")
        self._refresh_tree()
        self.log(f"Hoàn tất: {report['total_files']} tệp, {report['total_folders']} thư mục, "
                 f"{report['total_size_human']}", "ok")
        if report["total_files"] + report["total_folders"] == 0:
            self.empty_lbl.configure(text="Thư mục trống: không tìm thấy mục nào.")
            self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")
            self.set_status("Hoàn tất nhưng không có mục nào.", "warn")
        else:
            self.set_status(
                f"Hoàn tất: {report['total_files']:,} tệp, {report['total_folders']:,} thư mục, "
                f"{report['total_size_human']}.", "ok")

    # ------------------------------------------------------------------ #
    # Cây thư mục
    # ------------------------------------------------------------------ #
    def _on_search_changed(self, *_):
        if self._search_job:
            self.after_cancel(self._search_job)
        self._search_job = self.after(250, self._refresh_tree)

    def _filter_nodes(self, nodes, q):
        out = []
        for n in nodes:
            if q in n["name"].lower():
                out.append(n)  # giữ nguyên cả nhánh nếu thư mục khớp
            elif n["type"] == "folder":
                ch = self._filter_nodes(n.get("children", []), q)
                if ch:
                    out.append({**n, "children": ch})
        return out

    def _refresh_tree(self):
        if not self.report:
            return
        q = self.search_var.get().strip().lower()
        nodes = self._filter_nodes(self.report["items"], q) if q else self.report["items"]
        self._fill_tree(nodes, expand=bool(q))
        if nodes:
            self.empty_lbl.place_forget()
        else:
            self.empty_lbl.configure(text="Không có kết quả phù hợp." if q else "Thư mục trống.")
            self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

    def _fill_tree(self, nodes, expand=False):
        """Đổ dữ liệu vào Treeview theo từng lô nhỏ để giao diện không bị đơ."""
        self._fill_token += 1
        token = self._fill_token
        self.tree.delete(*self.tree.get_children())
        self.node_by_iid.clear()
        stack = [("", iter(nodes))]

        def step():
            if token != self._fill_token:
                return
            n = 0
            while stack and n < 400:
                parent, it = stack[-1]
                node = next(it, None)
                if node is None:
                    stack.pop()
                    continue
                is_dir = node["type"] == "folder"
                if is_dir:
                    size_txt = "chưa quét" if node.get("truncated") else human_size(node["size"])
                else:
                    size_txt = human_size(node["size"])
                iid = self.tree.insert(
                    parent, "end", text="  " + node["name"],
                    image=self.icon_folder if is_dir else self.icon_file,
                    values=(size_txt, node.get("modified") or ""), open=expand)
                self.node_by_iid[iid] = node
                n += 1
                if is_dir and node.get("children"):
                    stack.append((iid, iter(node["children"])))
            if stack:
                self.after(1, step)

        step()

    def _set_open(self, open_, item=""):
        for c in self.tree.get_children(item):
            self.tree.item(c, open=open_)
            self._set_open(open_, c)

    def _on_right_click(self, ev):
        iid = self.tree.identify_row(ev.y)
        if not iid:
            return
        self.tree.selection_set(iid)
        self._ctx_iid = iid
        node = self.node_by_iid.get(iid)
        is_dir = bool(node and node["type"] == "folder")
        self.menu.entryconfigure(self._scan_idx, state="normal" if is_dir else "disabled")
        try:
            self.menu.tk_popup(ev.x_root, ev.y_root)
        finally:
            self.menu.grab_release()

    def _copy_node(self, key):
        node = self.node_by_iid.get(self._ctx_iid)
        if node:
            self.clipboard_clear()
            self.clipboard_append(node[key])
            self.set_status(f"Đã sao chép: {node[key]}", "ok")

    def _scan_selected_folder(self):
        node = self.node_by_iid.get(self._ctx_iid)
        if node and node["type"] == "folder":
            self.sub_var.set(node["path"])
            self._auto_sub = None
            self.start_scan()

    # ------------------------------------------------------------------ #
    # Xuất file
    # ------------------------------------------------------------------ #
    def _default_stem(self):
        r = self.report
        stem = "baidu_" + r["short_url"][1:]
        if r["start_path"] not in ("", "/"):
            tail = re.sub(r'[\\/:*?"<>|\s]+', "_", r["start_path"].strip("/").replace("/", "_"))
            stem += "_" + tail
        return stem

    def export(self, kind):
        if not self.report:
            return
        ext = ".json" if kind == "json" else ".txt"
        types = [("JSON", "*.json")] if kind == "json" else [("Văn bản", "*.txt")]
        path = filedialog.asksaveasfilename(
            title="Lưu danh sách", defaultextension=ext,
            initialfile=self._default_stem() + ext, filetypes=types + [("Tất cả", "*.*")])
        if not path:
            return
        try:
            (write_json if kind == "json" else write_txt)(self.report, path)
        except OSError as e:
            messagebox.showerror("Không lưu được", str(e))
            return
        self.set_status(f"Đã lưu: {path}", "ok")
        self.log(f"Đã xuất {kind.upper()}: {path}", "ok")

    # ------------------------------------------------------------------ #
    def _on_close(self):
        if self.client:
            self.client.stop_flag = True
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()