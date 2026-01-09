# device_select_demo.py
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import List, Tuple, Dict, Optional
from device_repo import search_devices, get_device_by_exact_name
class DevicePicker(ttk.Frame):
    """
    最小可复用组件：
    - 输入框实时搜索（防抖 + 后台线程查库）
    - 下拉 Text 里做“局部标红”
    - 添加到“已选择设备”列表
    - get_selected_device_ids() 返回 List[int]，可直接接入你原来的下载逻辑
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._debounce_after_id: Optional[str] = None
        self._popup: Optional[tk.Toplevel] = None
        self._text: Optional[tk.Text] = None

        self._suggestions: List[Tuple[int, str]] = []   # 当前下拉列表 (id, name)
        self._selected: Dict[int, str] = {}            # 已选择 {id: name}

        # --- UI ---
        title = ttk.Label(self, text="设备选择（按名称搜索）")
        title.pack(anchor="w", pady=(0, 6))

        row = ttk.Frame(self)
        row.pack(fill="x")

        self.keyword_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.keyword_var)
        self.entry.pack(side="left", fill="x", expand=True)

        self.btn_add = ttk.Button(row, text="添加", command=self.add_current)
        self.btn_add.pack(side="left", padx=(8, 0))

        self.btn_clear = ttk.Button(row, text="清空已选", command=self.clear_selected)
        self.btn_clear.pack(side="left", padx=(8, 0))

        hint = ttk.Label(self, text="输入关键字会自动下拉；点击下拉项可快速填入；再点“添加”。")
        hint.pack(anchor="w", pady=(6, 6))

        # 已选择列表
        box_frame = ttk.LabelFrame(self, text="已选择设备")
        box_frame.pack(fill="both", expand=True)

        self.listbox = tk.Listbox(box_frame, height=8)
        self.listbox.pack(fill="both", expand=True, padx=6, pady=6)

        # --- bindings ---
        self.keyword_var.trace_add("write", lambda *_: self.on_typing())
        self.entry.bind("<Down>", lambda e: self.focus_popup())
        self.entry.bind("<Return>", lambda e: self.add_current())
        self.entry.bind("<Escape>", lambda e: self.hide_popup())

        # 点击空白处关闭下拉
        self.winfo_toplevel().bind("<Button-1>", self._global_click_close, add=True)

        # --- popup 行数上限（下拉最多显示多少行）---
        self._popup_max_visible = 10
        # popup 最大宽度（避免长名字撑到离谱）
        self._popup_max_width = 900
        top = self.winfo_toplevel()
        # 主窗口移动/缩放时，让 popup 跟随位置
        top.bind("<Configure>", lambda e: self._reposition_popup(), add=True)
        # 最小化/显示桌面（Unmap）时，隐藏 popup，避免“浮在桌面上”
        top.bind("<Unmap>", lambda e: self.hide_popup(), add=True)
        # 失去焦点（切到别的软件）时，隐藏 popup（但点 popup 自己不会误关）
        top.bind("<FocusOut>", lambda e: self._schedule_hide_if_inactive(), add=True)

    # ========== 搜索 + 下拉 ==========
    def on_typing(self):
        if self._debounce_after_id:
            self.after_cancel(self._debounce_after_id)
        self._debounce_after_id = self.after(250, self._trigger_search_background)

    def _trigger_search_background(self):
        kw = self.keyword_var.get().strip()
        if not kw:
            self.hide_popup()
            return

        # 后台线程查库，避免卡 UI
        def worker():
            try:
                rows = search_devices(kw, limit=30)
            except Exception:
                rows = []
            self.after(0, lambda: self._render_suggestions(kw, rows))

        threading.Thread(target=worker, daemon=True).start()

    def _render_suggestions(self, kw: str, rows: List[Tuple[int, str]]):
        self._suggestions = rows
        if not rows:
            self.hide_popup()
            return

        self.show_popup()

        # 清空并写入 Text，每行一个设备名，同时把 kw 匹配部分标红
        assert self._text is not None
        self._text.config(state="normal")
        self._text.delete("1.0", "end")

        for idx, (did, name) in enumerate(rows):
            line_start = self._text.index("end-1c")
            self._text.insert("end", name + "\n")

            # 标红：把 name 中所有 kw（不区分大小写）匹配到的片段 tag=match
            low_name = name.lower()
            low_kw = kw.lower()
            start = 0
            while True:
                pos = low_name.find(low_kw, start)
                if pos == -1:
                    break
                # Text index：当前行起点 + pos chars
                tag_start = f"{idx+1}.{pos}"
                tag_end = f"{idx+1}.{pos+len(kw)}"
                self._text.tag_add("match", tag_start, tag_end)
                start = pos + len(kw)

        self._text.tag_config("match", foreground="red")
        self._text.config(state="disabled")
        # 渲染完后重算 popup 尺寸（让高度/宽度立即贴合当前匹配数量）
        self._reposition_popup()

    def show_popup(self):
        if self._popup and self._popup.winfo_exists():
            return

        self._popup = tk.Toplevel(self)
        self._popup.wm_overrideredirect(True)  # 无边框下拉
        self._popup.attributes("-topmost", True)

        self._text = tk.Text(self._popup, height=10, width=60, wrap="none", cursor="hand2")
        self._text.pack(fill="both", expand=True)

        # 点击选择
        self._text.bind("<Button-1>", self._on_popup_click)
        self._text.bind("<Escape>", lambda e: self.hide_popup())

        self._reposition_popup()

    def _reposition_popup(self):
        if not (self._popup and self._popup.winfo_exists()):
            return
        # 放到 entry 下方
        x = self.entry.winfo_rootx()
        y = self.entry.winfo_rooty() + self.entry.winfo_height()
        w = self.entry.winfo_width()
        self._popup.geometry(f"{max(w, 500)}x200+{x}+{y}")

        # ---- 动态高度：随匹配数量变化（最多 self._popup_max_visible 行）----
        if self._text:
            visible = min(len(self._suggestions), self._popup_max_visible)
            visible = max(1, visible)
            self._text.config(height=visible)

            # 用字体行高计算像素高度，更贴合真实显示
            try:
                f = tkfont.Font(font=self._text.cget("font"))
                row_h = max(16, int(f.metrics("linespace"))) + 6
            except Exception:
                row_h = 22
            h = visible * row_h + 8  # 8: 上下 padding

            # 可选：动态宽度（按最长设备名），并限制最大宽度
            try:
                longest = 0
                for _, name in self._suggestions:
                    longest = max(longest, f.measure(name))
                w2 = min(self._popup_max_width, max(max(w, 500), longest + 30))
            except Exception:
                w2 = max(w, 500)

            # 覆盖上面固定 200 的 geometry
            self._popup.geometry(f"{w2}x{h}+{x}+{y}")

    def hide_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.destroy()
        self._popup = None
        self._text = None
        self._suggestions = []

    def focus_popup(self):
        if self._popup and self._popup.winfo_exists():
            self._popup.focus_force()

    def _on_popup_click(self, event):
        if not self._text:
            return
        # 获取点击的行号
        index = self._text.index(f"@{event.x},{event.y}")  # like "3.15"
        line = int(index.split(".")[0])
        i = line - 1
        if 0 <= i < len(self._suggestions):
            _, name = self._suggestions[i]
            self.keyword_var.set(name)
            self.entry.icursor("end")
            self.hide_popup()

    def _global_click_close(self, event):
        # 如果点击不在 popup 上，就关闭 popup
        if self._popup and self._popup.winfo_exists():
            widget = event.widget
            if widget is self._popup or (hasattr(widget, "winfo_toplevel") and widget.winfo_toplevel() is self._popup):
                return
            # 如果点在 entry 上，别关（让用户继续输入）
            if widget is self.entry:
                return
            self.hide_popup()


    def _schedule_hide_if_inactive(self):
        """窗口失焦后：延迟检查焦点是否仍在本程序（含 popup），否则隐藏 popup"""
        self.after(60, self._hide_popup_if_inactive)

    def _hide_popup_if_inactive(self):
        if not (self._popup and self._popup.winfo_exists()):
            return
        try:
            focus_path = self.winfo_toplevel().tk.call("focus")  # 全局焦点（可能在 popup）
        except tk.TclError:
            focus_path = ""

        # 没有焦点：通常是切到别的程序/桌面 → 隐藏 popup
        if not focus_path:
            self.hide_popup()
            return

        # 有焦点：判断焦点是否在 root 或 popup 内
        try:
            w = self.nametowidget(focus_path)
            top = w.winfo_toplevel()
        except Exception:
            self.hide_popup()
            return

        if top is self.winfo_toplevel() or top is self._popup:
            return  # 焦点仍在本程序（含 popup），不隐藏

        self.hide_popup()

    # ========== 添加 / 清空 / 获取结果 ==========
    def add_current(self):
        name = self.keyword_var.get().strip()
        if not name:
            return

        # 优先：如果当前 name 正好是下拉里的某一项，直接用那一项
        for did, n in self._suggestions:
            if n == name:
                self._selected[did] = n
                self._refresh_selected_list()
                self.keyword_var.set("")
                self.hide_popup()
                return

        # 否则：做一次精确匹配（允许用户手动输入完整名字）
        try:
            matches = get_device_by_exact_name(name)
        except Exception:
            matches = []

        if len(matches) == 1:
            did, n = matches[0]
            self._selected[did] = n
            self._refresh_selected_list()
            self.keyword_var.set("")
            self.hide_popup()
            return

        # 0 或 多条都不自动加：避免误选
        # 你后续想要弹窗提示也可以加 messagebox
        self.hide_popup()

    def clear_selected(self):
        self._selected.clear()
        self._refresh_selected_list()

    def _refresh_selected_list(self):
        self.listbox.delete(0, "end")
        for did, name in sorted(self._selected.items(), key=lambda x: x[1].lower()):
            self.listbox.insert("end", f"{name}  (ID={did})")

    def get_selected_device_ids(self) -> List[int]:
        return list(self._selected.keys())


def main():
    root = tk.Tk()
    root.title("Device Picker Demo")
    root.geometry("720x420")

    picker = DevicePicker(root)
    picker.pack(fill="both", expand=True, padx=12, pady=12)

    def print_ids():
        print("Selected IDs:", picker.get_selected_device_ids())

    ttk.Button(root, text="打印已选 IDs（模拟开始下载用）", command=print_ids).pack(pady=10)

    root.mainloop()


if __name__ == "__main__":
    main()
