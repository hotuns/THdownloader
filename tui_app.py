#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设备数据下载器 - Tkinter GUI 界面
"""

import asyncio
import logging
import threading
import tkinter as tk
import tkinter.font as tkfont # v2.0
from datetime import datetime, timedelta
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, scrolledtext
from typing import List, Optional, Tuple, Dict
import sys
import os

from download.async_downloader import AsyncDeviceDataDownloader, DownloadError
from download.db import search_devices_by_name, get_device_by_exact_name # v2.0
from download.db import search_devices_by_id, get_device_by_id  # v2.1

class DownloadProgressWidget:
    """下载进度显示组件"""

    def __init__(self, parent):
        self.parent = parent
        self.current_message = ""
        self.current_progress = 0.0

        # 创建进度框架
        self.frame = ttk.LabelFrame(parent, text="下载进度", padding="10")

        # 进度条
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(
            self.frame,
            variable=self.progress_var,
            maximum=100,
            length=400
        )
        self.progress_bar.pack(fill=tk.X, pady=(0, 5))

        # 进度消息
        self.message_var = tk.StringVar(value="准备中...")
        self.message_label = ttk.Label(self.frame, textvariable=self.message_var)
        self.message_label.pack(pady=(0, 5))

        # 取消按钮
        self.cancel_btn = ttk.Button(
            self.frame,
            text="取消下载",
            state=tk.DISABLED
        )
        self.cancel_btn.pack()

    def update_progress(self, message: str, progress: float):
        """更新进度"""
        self.current_message = message
        self.current_progress = progress * 100

        self.progress_var.set(self.current_progress)
        self.message_var.set(f"{message} ({self.current_progress:.1f}%)")

    def enable_cancel_button(self, enabled: bool = True):
        """启用或禁用取消按钮"""
        self.cancel_btn.config(state=tk.NORMAL if enabled else tk.DISABLED)

    def reset(self):
        """重置进度显示"""
        self.current_message = ""
        self.current_progress = 0.0
        self.progress_var.set(0)
        self.message_var.set("准备中...")
        self.enable_cancel_button(False)


class DeviceSelectionWidget:
    """设备选择组件"""

    def __init__(self, parent):
        self.parent = parent
        self.selected_devices = []

        # 创建设备选择框架
        self.frame = ttk.LabelFrame(parent, text="设备选择", padding="10")

        # 说明标签
        ttk.Label(
            self.frame,
            text="设备选择 (多个设备用逗号分隔，如: 1,2,3)"
        ).pack(anchor=tk.W, pady=(0, 5))

        # 设备输入框
        self.device_var = tk.StringVar()
        self.device_entry = ttk.Entry(
            self.frame,
            textvariable=self.device_var,
            width=40
        )
        self.device_entry.pack(fill=tk.X, pady=(0, 5))

        # 状态标签
        self.status_var = tk.StringVar()
        self.status_label = ttk.Label(self.frame, textvariable=self.status_var)
        self.status_label.pack(anchor=tk.W)

    def get_devices(self) -> List[int]:
        """获取选择的设备列表"""
        try:
            devices = [int(d.strip()) for d in self.device_var.get().split(",") if d.strip()]
            return devices
        except ValueError:
            return []

    def validate_devices(self) -> bool:
        """验证设备输入"""
        devices = self.get_devices()

        if not devices:
            self.status_var.set("请输入至少一个有效的设备ID")
            self.status_label.config(foreground="red")
            return False

        self.status_var.set(f"已选择 {len(devices)} 个设备")
        self.status_label.config(foreground="green")
        return True
# v2.0
class DeviceNameSelectionWidget:
    """
    设备选择（按名称搜索，多选）
    - 输入时自动下拉（防抖 + 后台线程查库）
    - 下拉用 Text 做局部标红
    - 下拉高度随匹配数变化（最多 MAX_VISIBLE）
    - 切到别的程序/最小化时下拉自动隐藏（解决图层悬浮问题）
    - “添加”后清空输入框；“清空已选”一键清除
    - 对外接口保持：get_devices() -> List[int], validate_devices() -> bool
    """

    MAX_VISIBLE = 10
    MIN_WIDTH = 500

    def __init__(self, parent):
        self.parent = parent
        self._debounce_after_id = None

        self._popup: Optional[tk.Toplevel] = None
        self._text: Optional[tk.Text] = None
        self._suggestions: List[Tuple[int, str]] = []  # (id, name)
        self._selected: Dict[int, str] = {}            # {id: name}

        self.frame = ttk.LabelFrame(parent, text="设备选择（按名称搜索）", padding="10")

        ttk.Label(
            self.frame,
            text="输入设备名称关键字，自动下拉匹配；选择或输入完整名称后点击“添加”。"
        ).pack(anchor=tk.W, pady=(0, 6))

        row = ttk.Frame(self.frame)
        row.pack(fill=tk.X, pady=(0, 6))

        self.keyword_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.keyword_var, width=40)
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.btn_add = ttk.Button(row, text="添加", command=self.add_current)
        self.btn_add.pack(side=tk.LEFT, padx=(8, 0))

        self.btn_clear = ttk.Button(row, text="清空已选", command=self.clear_selected)
        self.btn_clear.pack(side=tk.LEFT, padx=(8, 0))

        self.status_var = tk.StringVar(value="尚未选择设备")
        self.status_label = ttk.Label(self.frame, textvariable=self.status_var)
        self.status_label.pack(anchor=tk.W, pady=(0, 6))

        box = ttk.LabelFrame(self.frame, text="已选择设备")
        box.pack(fill=tk.BOTH, expand=True)
        self.listbox = tk.Listbox(box, height=6)
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # --- 字体/行高：用于计算下拉高度 ---
        self._font = tkfont.Font(font=self.entry.cget("font"))
        self._row_height = max(18, self._font.metrics("linespace") + 6)

        # --- bindings ---
        self.keyword_var.trace_add("write", lambda *_: self.on_typing())
        self.entry.bind("<Down>", lambda e: self.focus_popup())
        self.entry.bind("<Return>", lambda e: self.add_current())
        self.entry.bind("<Escape>", lambda e: self.hide_popup())

        root = self.frame.winfo_toplevel()
        root.bind("<Button-1>", self._global_click_close, add=True)
        root.bind("<FocusOut>", self._on_root_focus_out, add=True)
        root.bind("<Unmap>", lambda e: self.hide_popup(), add=True)
        root.bind("<Configure>", lambda e: self._reposition_popup(), add=True)

    def get_devices(self) -> List[int]:
        return list(self._selected.keys())

    def validate_devices(self) -> bool:
        if not self._selected:
            self.status_var.set("请至少添加一个设备")
            self.status_label.config(foreground="red")
            return False
        self.status_var.set(f"已选择 {len(self._selected)} 个设备")
        self.status_label.config(foreground="green")
        return True

    def on_typing(self):
        if self._debounce_after_id:
            self.frame.after_cancel(self._debounce_after_id)
        self._debounce_after_id = self.frame.after(250, self._trigger_search_background)

    def _trigger_search_background(self):
        kw = self.keyword_var.get().strip()
        if not kw:
            self.hide_popup()
            return

        def worker():
            rows = search_devices_by_name(kw, limit=30)

            # v2.1: 如果用户输入的是纯数字，同步按ID检索并合并
            if kw.isdigit():
                try:
                    rows_by_id = search_devices_by_id(kw, limit=30)
                except Exception:
                    rows_by_id = []

                if rows_by_id:
                    seen = set()
                    merged = []
                    for did, name in (rows_by_id + rows):
                        if did not in seen:
                            merged.append((did, name))
                            seen.add(did)
                    rows = merged

            self.frame.after(0, lambda: self._render_suggestions(kw, rows))
            # try:
            #     rows = search_devices_by_name(kw, limit=30)
            # except Exception:
            #     rows = []
            # self.frame.after(0, lambda: self._render_suggestions(kw, rows))

        threading.Thread(target=worker, daemon=True).start()

    def _render_suggestions(self, kw: str, rows: List[Tuple[int, str]]):
        self._suggestions = rows
        if not rows:
            self.hide_popup()
            return

        self._ensure_popup()
        assert self._popup is not None and self._text is not None

        visible = min(len(rows), self.MAX_VISIBLE)
        self._text.config(height=visible)

        # 写入内容 + 标红
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.tag_delete("match")

        low_kw = kw.lower()
        for idx, (_, name) in enumerate(rows):
            self._text.insert("end", name + "\n")
            low_name = name.lower()
            start = 0
            while True:
                pos = low_name.find(low_kw, start)
                if pos == -1:
                    break
                self._text.tag_add("match", f"{idx + 1}.{pos}", f"{idx + 1}.{pos + len(kw)}")
                start = pos + len(kw)

        self._text.tag_config("match", foreground="red")
        self._text.config(state="disabled")

        # 显示（如果当前是隐藏状态）
        if not self._popup.winfo_viewable():
            self._popup.deiconify()
            self._popup.lift(self.frame.winfo_toplevel())

        # 定位（放在 deiconify 之后）
        self._reposition_popup(visible_rows=visible)

    def _ensure_popup(self):
        if self._popup and self._popup.winfo_exists():
            return

        self._popup = tk.Toplevel(self.frame)
        self._popup.withdraw()
        self._popup.overrideredirect(True)
        # 注意：不设置 topmost，避免切到别的程序时仍悬浮在最上层
        self._popup.transient(self.frame.winfo_toplevel())

        self._text = tk.Text(
            self._popup,
            height=5,
            width=60,
            wrap="none",
            cursor="hand2",
            font=self.entry.cget("font")
        )
        self._text.pack(fill="both", expand=True)
        self._text.bind("<Button-1>", self._on_popup_click)
        self._text.bind("<Escape>", lambda e: self.hide_popup())

    def _reposition_popup(self, visible_rows: Optional[int] = None):
        if not (self._popup and self._popup.winfo_exists()):
            return

        self.frame.update_idletasks()
        # entry 还没布局好（宽度=1时很常见），延迟再定位，避免跑到 0,0
        if self.entry.winfo_width() <= 1:
            self.frame.after(10, lambda: self._reposition_popup(visible_rows=visible_rows))
            return
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        w = max(self.entry.winfo_width(), self.MIN_WIDTH)

        if visible_rows is None and self._text:
            visible_rows = int(self._text.cget("height"))
        visible_rows = max(1, int(visible_rows or 1))

        h = visible_rows * self._row_height + 8
        self._popup.geometry(f"{w}x{h}+{x}+{y}")

    def _reposition_popup_dynamic(self, visible_rows: int):
        """
        - 确保 popup 已创建
        - 高度按匹配数动态调整
        - 重新定位到 entry 下方
        """
        self._ensure_popup()
        if self._text:
            try:
                self._text.config(height=max(1, int(visible_rows)))
            except Exception:
                pass
        self._reposition_popup(visible_rows=visible_rows)

    def hide_popup(self):
        #  不 destroy，改为 withdraw，避免触发一连串焦点/闪烁问题
        if self._popup and self._popup.winfo_exists():
            self._popup.withdraw()
        self._suggestions = []


    def focus_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.focus_force()

    def _on_popup_click(self, event):
        if not self._text:
            return
        index = self._text.index(f"@{event.x},{event.y}")
        line = int(index.split(".")[0])
        i = line - 1
        if 0 <= i < len(self._suggestions):
            _, name = self._suggestions[i]
            self.keyword_var.set(name)
            self.entry.icursor("end")
            self.hide_popup()

    def _global_click_close(self, event):
        if self._popup and self._popup.winfo_exists():
            widget = event.widget
            if widget is self._popup or (hasattr(widget, "winfo_toplevel") and widget.winfo_toplevel() is self._popup):
                return
            if widget is self.entry:
                return
            self.hide_popup()

    def _on_root_focus_out(self, event):
        self.frame.after(80, self._check_focus_then_maybe_hide)

    def _check_focus_then_maybe_hide(self):
        if not (self._popup and self._popup.winfo_exists()):
            return
        root = self.frame.winfo_toplevel()
        focused = root.focus_get()
        if focused is None:
            self.hide_popup()
            return
        top = focused.winfo_toplevel()
        if top is not root and top is not self._popup:
            self.hide_popup()

    def add_current(self):
        name = self.keyword_var.get().strip()
        if not name:
            return

        # v2.1: 允许用户直接输入设备ID并添加
        if name.isdigit():
            try:
                matches = get_device_by_id(int(name))
            except Exception:
                matches = []
            if len(matches) == 1:
                did, n = matches[0]
                self._selected[did] = n
                self._refresh_selected_list()
                self.keyword_var.set("")
                self.hide_popup()
                return

        for did, n in self._suggestions:
            if n == name:
                self._selected[did] = n
                self._refresh_selected_list()
                self.keyword_var.set("")
                self.hide_popup()
                return

        matches = get_device_by_exact_name(name)
        if len(matches) == 1:
            did, n = matches[0]
            self._selected[did] = n
            self._refresh_selected_list()
            self.keyword_var.set("")
            self.hide_popup()
            return

        self.hide_popup()
        self.status_var.set("未找到唯一匹配，请从下拉选择或输入更精确的名称")
        self.status_label.config(foreground="red")

    def clear_selected(self):
        self._selected.clear()
        self._refresh_selected_list()
        self.status_var.set("已清空已选设备")
        self.status_label.config(foreground="green")

    def _refresh_selected_list(self):
        self.listbox.delete(0, "end")
        for did, name in sorted(self._selected.items(), key=lambda x: x[1].lower()):
            self.listbox.insert("end", f"{name}  (ID={did})")
        if self._selected:
            self.status_var.set(f"已选择 {len(self._selected)} 个设备")
            self.status_label.config(foreground="green")
        else:
            self.status_var.set("尚未选择设备")
            self.status_label.config(foreground="black")

class DateRangeWidget:
    """日期范围选择组件"""

    def __init__(self, parent):
        self.parent = parent

        # 创建日期范围框架
        self.frame = ttk.LabelFrame(parent, text="时间范围", padding="10")

        # 创建水平布局框架
        date_frame = ttk.Frame(self.frame)
        date_frame.pack(fill=tk.X)

        # 开始时间
        start_frame = ttk.Frame(date_frame)
        start_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))

        ttk.Label(start_frame, text="开始时间").pack(anchor=tk.W)
        self.start_var = tk.StringVar(
            value=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d 00:00:00")
        )
        self.start_entry = ttk.Entry(start_frame, textvariable=self.start_var, width=20)
        self.start_entry.pack(fill=tk.X)

        # 结束时间
        end_frame = ttk.Frame(date_frame)
        end_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        ttk.Label(end_frame, text="结束时间").pack(anchor=tk.W)
        self.end_var = tk.StringVar(
            value=datetime.now().strftime("%Y-%m-%d 23:59:59")
        )
        self.end_entry = ttk.Entry(end_frame, textvariable=self.end_var, width=20)
        self.end_entry.pack(fill=tk.X)

    def get_date_range(self) -> tuple[str, str]:
        """获取日期范围"""
        return self.start_var.get(), self.end_var.get()

    def validate_dates(self) -> bool:
        """验证日期格式"""
        start_str, end_str = self.get_date_range()
        try:
            start = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")

            if start >= end:
                return False

            return True
        except ValueError:
            return False


class ContentTypeWidget:
    """内容类型与图片选项组件"""

    def __init__(self, parent):
        self.parent = parent

        # 创建内容类型框架
        self.frame = ttk.LabelFrame(parent, text="下载选项", padding="10")

        # 标题
        ttk.Label(self.frame, text="选择下载内容类型:").pack(anchor=tk.W, pady=(0, 5))

        # 第一行选项
        options_frame1 = ttk.Frame(self.frame)
        options_frame1.pack(fill=tk.X, pady=(0, 5))

        self.data_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame1, text="设备数据", variable=self.data_var).pack(side=tk.LEFT, padx=(0, 10))

        self.image_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame1, text="图片数据", variable=self.image_var).pack(side=tk.LEFT, padx=(0, 10))

        self.zip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame1, text="打包为ZIP文件", variable=self.zip_var).pack(side=tk.LEFT)

        # 第二行选项
        options_frame2 = ttk.Frame(self.frame)
        options_frame2.pack(fill=tk.X, pady=(0, 5))

        self.thumbnail_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame2, text="下载缩略图", variable=self.thumbnail_var).pack(side=tk.LEFT, padx=(0, 10))

        self.rename_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame2, text="重命名图片", variable=self.rename_var).pack(side=tk.LEFT)

        # 重命名格式输入
        rename_frame = ttk.Frame(self.frame)
        rename_frame.pack(fill=tk.X)

        self.rename_format_var = tk.StringVar()
        self.rename_entry = ttk.Entry(
            rename_frame,
            textvariable=self.rename_format_var,
            width=50
        )
        self.rename_entry.pack(fill=tk.X)
        hint_label = ttk.Label(rename_frame, text="输入日期格式化字符串，如 %Y-%m-%d %H-%M-%S 生成格式: 2025-01-01 12-30-45")
        hint_label2 = ttk.Label(rename_frame, text="常用格式: %Y年%m月%d日%H时 或 %Y%m%d_%H%M (注意:避免使用:/?*\"<>|等字符)")
        hint_label.pack(anchor=tk.W)
        hint_label2.pack(anchor=tk.W)

    def get_content_types(self) -> List[str]:
        """获取选择的内容类型"""
        contents = []
        if self.data_var.get():
            contents.append("data")
        if self.image_var.get():
            contents.append("image")
        return contents

    def should_zip(self) -> bool:
        """是否需要打包ZIP"""
        return self.zip_var.get()

    def should_thumbnail(self) -> bool:
        """是否下载缩略图"""
        return self.thumbnail_var.get()

    def get_rename_options(self) -> tuple[bool, Optional[str]]:
        """返回是否重命名以及重命名格式（可为空）"""
        enabled = self.rename_var.get()
        fmt_input = self.rename_format_var.get().strip()
        return enabled, (fmt_input or None)


class DownloadDirWidget:
    """下载目录选择组件"""

    def __init__(self, parent):
        self.parent = parent

        # 创建下载目录框架
        self.frame = ttk.LabelFrame(parent, text="下载目录", padding="10")

        # 目录输入框架
        dir_frame = ttk.Frame(self.frame)
        dir_frame.pack(fill=tk.X, pady=(0, 5))

        self.dir_var = tk.StringVar(value="./tmp/")
        self.dir_entry = ttk.Entry(dir_frame, textvariable=self.dir_var)
        self.dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # 浏览按钮
        self.browse_btn = ttk.Button(dir_frame, text="浏览", command=self.browse_directory)
        self.browse_btn.pack(side=tk.RIGHT)

        # 提示标签
        self.hint_var = tk.StringVar(value="默认为 ./tmp/")
        self.hint_label = ttk.Label(self.frame, textvariable=self.hint_var)
        self.hint_label.pack(anchor=tk.W)

    def browse_directory(self):
        """浏览目录"""
        directory = filedialog.askdirectory(initialdir=self.dir_var.get())
        if directory:
            self.dir_var.set(directory)

    def get_download_dir(self) -> str:
        """获取下载目录路径"""
        return self.dir_var.get().strip() or "./tmp/"

    def validate_directory(self) -> bool:
        """验证目录路径是否有效"""
        dir_path = self.get_download_dir()
        try:
            path = Path(dir_path)
            path.mkdir(parents=True, exist_ok=True)
            self.hint_var.set("目录有效")
            self.hint_label.config(foreground="green")
            return True
        except Exception as e:
            self.hint_var.set(f"目录无效: {str(e)}")
            self.hint_label.config(foreground="red")
            return False


class LogWidget:
    """日志显示组件"""

    def __init__(self, parent):
        self.parent = parent
        self.log_messages = []

        # 创建日志框架
        self.frame = ttk.LabelFrame(parent, text="日志信息", padding="10")

        # 日志文本区域
        self.log_text = scrolledtext.ScrolledText(
            self.frame,
            height=12,
            width=60,
            state=tk.DISABLED,
            wrap=tk.WORD
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)

    def add_log(self, message: str, level: str = "INFO"):
        """添加日志消息"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        formatted_message = f"[{timestamp}] [{level}] {message}\n"
        self.log_messages.append(formatted_message)

        # 更新文本区域
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, formatted_message)

        # 保留最近100条日志
        if len(self.log_messages) > 100:
            self.log_messages = self.log_messages[-100:]
            self.log_text.delete(1.0, tk.END)
            self.log_text.insert(1.0, "".join(self.log_messages))

        self.log_text.config(state=tk.DISABLED)
        self.log_text.see(tk.END)


class DownloadManagerApp:
    """主应用类"""

    def __init__(self):
        self.downloader = None
        self.current_worker = None
        self.download_dir = "./tmp/"
        self.download_thread = None
        self.is_downloading = False

        # 创建主窗口
        self.root = tk.Tk()
        self.root.title("设备数据下载器 - HOTUNS")
        self.root.geometry("1000x700")
        self.root.minsize(800, 600)

        # 设置编码和字体
        self.setup_fonts()

        # 设置窗口图标（如果有的话）
        try:
            # self.root.iconbitmap("icon.ico")  # 如果有图标文件
            pass
        except:
            pass

        self.setup_ui()
        self.setup_bindings()

        # 初始化下载器
        self.update_downloader()

    def setup_fonts(self):
        """设置字体以支持中文显示"""
        # 设置系统编码
        if hasattr(sys, 'setdefaultencoding'):
            sys.setdefaultencoding('utf-8')

        # 设置默认字体，优先使用支持中文的字体
        if sys.platform == "win32":
            default_font = ("Microsoft YaHei", 9)
        elif sys.platform == "darwin":
            default_font = ("PingFang SC", 9)
        else:
            # Linux系统，尝试多种中文字体
            import tkinter.font as tkFont
            available_fonts = tkFont.families()
            chinese_fonts = ["Noto Sans CJK SC", "WenQuanYi Micro Hei", "DejaVu Sans", "Liberation Sans"]
            selected_font = "TkDefaultFont"

            for font in chinese_fonts:
                if font in available_fonts:
                    selected_font = font
                    break

            default_font = (selected_font, 9)

        # 配置ttk样式
        style = ttk.Style()

        # 尝试设置主题
        try:
            if sys.platform == "win32":
                style.theme_use('vista')
            elif sys.platform == "darwin":
                style.theme_use('aqua')
            else:
                style.theme_use('clam')
        except:
            pass

        # 配置字体
        style.configure(".", font=default_font)
        style.configure("TLabel", font=default_font)
        style.configure("TButton", font=default_font)
        style.configure("TCheckbutton", font=default_font)
        style.configure("TEntry", font=default_font)
        style.configure("TLabelFrame.Label", font=default_font)

        # 设置重点按钮样式
        style.configure("Accent.TButton", font=default_font)

        # 设置根窗口的默认字体
        self.root.option_add('*Font', default_font)

    def setup_ui(self):
        """设置用户界面"""
        # 创建主框架
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 创建左右分栏
        left_frame = ttk.Frame(main_frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 左侧配置区域
        config_label = ttk.Label(left_frame, text="设备数据下载配置", font=("TkDefaultFont", 12, "bold"))
        config_label.pack(anchor=tk.W, pady=(0, 10))

        # 创建配置组件的容器
        config_container = ttk.Frame(left_frame)
        config_container.pack(fill=tk.BOTH, expand=True)

        # 上半部分：目录和设备选择
        top_config = ttk.Frame(config_container)
        top_config.pack(fill=tk.X, pady=(0, 10))

        top_left = ttk.Frame(top_config)
        top_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        top_right = ttk.Frame(top_config)
        top_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # 下半部分：时间和内容选择
        bottom_config = ttk.Frame(config_container)
        bottom_config.pack(fill=tk.X, pady=(0, 10))

        bottom_left = ttk.Frame(bottom_config)
        bottom_left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 10))

        bottom_right = ttk.Frame(bottom_config)
        bottom_right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
    
        # 创建各个组件
        self.dir_widget = DownloadDirWidget(top_left)
        self.dir_widget.frame.pack(fill=tk.X, pady=(0, 10))

        #self.device_widget = DeviceSelectionWidget(top_left)
        self.device_widget = DeviceNameSelectionWidget(top_left) # v2.0
        self.device_widget.frame.pack(fill=tk.X)

        self.date_widget = DateRangeWidget(top_right)
        self.date_widget.frame.pack(fill=tk.X, pady=(0, 10))

        self.content_widget = ContentTypeWidget(top_right)
        self.content_widget.frame.pack(fill=tk.X)

        # 按钮区域
        button_frame = ttk.Frame(left_frame)
        button_frame.pack(fill=tk.X, pady=(10, 0))

        self.download_btn = ttk.Button(
            button_frame,
            text="开始下载",
            command=self.handle_download,
            style="Accent.TButton"
        )
        self.download_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.test_btn = ttk.Button(
            button_frame,
            text="测试连接",
            command=self.handle_test_connections
        )
        self.test_btn.pack(side=tk.LEFT)

        # 右侧状态区域
        status_label = ttk.Label(right_frame, text="状态和日志", font=("TkDefaultFont", 12, "bold"))
        status_label.pack(anchor=tk.W, pady=(0, 10))

        self.progress_widget = DownloadProgressWidget(right_frame)
        self.progress_widget.frame.pack(fill=tk.X, pady=(0, 10))

        self.log_widget = LogWidget(right_frame)
        self.log_widget.frame.pack(fill=tk.BOTH, expand=True)
    
    def setup_bindings(self):
        """设置键盘绑定"""
        self.root.bind('<Control-d>', lambda e: self.handle_download())
        self.root.bind('<Control-t>', lambda e: self.handle_test_connections())
        self.root.bind('<Control-q>', lambda e: self.root.quit())

        # 设置取消按钮的命令
        self.progress_widget.cancel_btn.config(command=self.handle_cancel_download)

    def update_downloader(self):
        """更新下载器实例"""
        download_dir = self.dir_widget.get_download_dir()
        self.download_dir = download_dir

        try:
            self.downloader = AsyncDeviceDataDownloader(root_path=download_dir)
            self.log_widget.add_log(f"下载器初始化成功，目录: {download_dir}", "INFO")
        except Exception as e:
            self.log_widget.add_log(f"初始化失败: {str(e)}", "ERROR")

    def handle_download(self):
        """处理下载请求"""
        if not self.is_downloading:
            self.start_download()

    def handle_test_connections(self):
        """处理连接测试请求"""
        if not self.is_downloading:
            self.test_connections()

    def handle_cancel_download(self):
        """处理取消下载请求"""
        if self.is_downloading and self.downloader:
            self.downloader.cancel()
            self.log_widget.add_log("下载任务已取消", "WARNING")
            self.is_downloading = False
            self.progress_widget.enable_cancel_button(False)
            self.download_btn.config(state=tk.NORMAL)
    
    def test_connections(self):
        """测试数据库和OSS连接"""
        if not self.downloader:
            self.log_widget.add_log("下载器未初始化", "ERROR")
            return

        self.log_widget.add_log("开始测试连接...", "INFO")

        def test_worker():
            try:
                # 在新的事件循环中运行异步方法
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                results = loop.run_until_complete(self.downloader.test_connections())

                # 使用 after 方法在主线程中更新 UI
                self.root.after(0, self.update_test_results, results)

            except Exception as e:
                self.root.after(0, lambda: self.log_widget.add_log(f"连接测试失败: {str(e)}", "ERROR"))
            finally:
                loop.close()

        # 在新线程中运行测试
        test_thread = threading.Thread(target=test_worker, daemon=True)
        test_thread.start()

    def update_test_results(self, results):
        """更新测试结果到UI"""
        if results["database"]:
            self.log_widget.add_log("✓ 数据库连接成功", "INFO")
        else:
            self.log_widget.add_log("✗ 数据库连接失败", "ERROR")

        if results["oss"]:
            self.log_widget.add_log("✓ OSS连接成功", "INFO")
        else:
            self.log_widget.add_log("✗ OSS连接失败", "ERROR")
    
    def start_download(self):
        """开始下载任务"""
        # 验证输入
        if not self.device_widget.validate_devices():
            self.log_widget.add_log("设备选择无效", "ERROR")
            return

        if not self.date_widget.validate_dates():
            self.log_widget.add_log("日期范围无效", "ERROR")
            return

        if not self.dir_widget.validate_directory():
            self.log_widget.add_log("下载目录无效", "ERROR")
            return

        # 更新下载器目录
        self.update_downloader()

        devices = self.device_widget.get_devices()
        start_time, end_time = self.date_widget.get_date_range()
        contents = self.content_widget.get_content_types()
        is_zip = self.content_widget.should_zip()
        thumb = self.content_widget.should_thumbnail()
        rename_enabled, rename_fmt = self.content_widget.get_rename_options()

        if not contents:
            self.log_widget.add_log("请至少选择一种内容类型", "ERROR")
            return

        self.log_widget.add_log(f"开始下载设备 {devices} 的数据...", "INFO")
        self.log_widget.add_log(f"下载目录: {self.download_dir}", "INFO")
        if "image" in contents:
            if thumb:
                self.log_widget.add_log("将下载缩略图 (style/small)", "INFO")
            if rename_enabled:
                self.log_widget.add_log(
                    f"图片重命名启用，格式: {rename_fmt or '保留第二个时间戳'}",
                    "INFO"
                )

        # 设置进度回调
        self.downloader.set_progress_callback(self.progress_widget.update_progress)

        # 设置下载状态
        self.is_downloading = True
        self.download_btn.config(state=tk.DISABLED)
        self.progress_widget.enable_cancel_button(True)
        self.progress_widget.reset()

        # 启动下载线程
        self.download_thread = threading.Thread(
            target=self.download_worker,
            args=(devices, contents, start_time, end_time, is_zip, thumb, rename_enabled, rename_fmt),
            daemon=True
        )
        self.download_thread.start()
    
    def download_worker(self, devices, contents, start_time, end_time, is_zip, thumb, rename_enabled, rename_fmt):
        """下载工作线程"""
        try:
            # 在新的事件循环中运行异步下载
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            results = loop.run_until_complete(
                self.downloader.download_device_data(
                    devices=devices,
                    contents=contents,
                    start_at=start_time,
                    end_at=end_time,
                    is_zip=is_zip,
                    thumbnail=thumb,
                    rename_images=rename_enabled,
                    rename_format=rename_fmt
                )
            )

            # 使用 after 方法在主线程中更新 UI
            self.root.after(0, self.update_download_results, results)

        except DownloadError as e:
            self.root.after(0, lambda: self.log_widget.add_log(f"下载错误: {str(e)}", "ERROR"))
        except Exception as e:
            self.root.after(0, lambda: self.log_widget.add_log(f"未知错误: {str(e)}", "ERROR"))
        finally:
            loop.close()
            # 在主线程中重置状态
            self.root.after(0, self.download_finished)

    def update_download_results(self, results):
        """更新下载结果到UI"""
        if results["success"]:
            self.log_widget.add_log("下载完成！", "INFO")
            self.log_widget.add_log(f"成功处理 {results['processed_devices']} 个设备", "INFO")

            if results["failed_devices"]:
                self.log_widget.add_log(f"失败的设备: {results['failed_devices']}", "WARNING")

            if results["download_path"]:
                self.log_widget.add_log(f"下载目录: {results['download_path']}", "INFO")

            if results["zip_path"]:
                self.log_widget.add_log(f"ZIP文件: {results['zip_path']}", "INFO")
        else:
            self.log_widget.add_log("下载失败！", "ERROR")
            for error in results["errors"]:
                self.log_widget.add_log(error, "ERROR")

    def download_finished(self):
        """下载完成后的清理工作"""
        self.is_downloading = False
        self.download_btn.config(state=tk.NORMAL)
        self.progress_widget.enable_cancel_button(False)

    def run(self):
        """运行应用"""
        self.root.mainloop()


if __name__ == "__main__":
    # 设置日志级别
    logging.basicConfig(level=logging.INFO)

    # 启动应用
    app = DownloadManagerApp()
    app.run()
