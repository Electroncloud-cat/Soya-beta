# -*- coding: utf-8 -*-
"""
涟宗也 · 活动监控悬浮窗  v3
颜色含义：
  🟢 绿色 — 正在聊天页面，鼠标活跃
  🟡 黄色 — 鼠标无动作超过5分钟（可能离开了）
  🔴 红色 — 已切离聊天页面 / 超时未响应
  ⚫ 灰色 — 无法连接服务器（窗口将自动退出）

交互：
  - 鼠标悬停 → 完全不透明
  - 鼠标离开 → 半透明（不遮挡后方内容）
  - 拖动窗口移动
  - 双击收缩 / 再次双击展开
  - 右上角 ✕ 关闭

服务器地址配置：
  优先级：命令行参数 > 环境变量 SOYA_SERVER > widget_config.json > 默认 localhost
  示例（Windows远程连接Zero3W）：
    python monitor_widget.py https://你的tunnel地址.trycloudflare.com
    或设置环境变量 SOYA_SERVER=https://你的tunnel地址.trycloudflare.com
"""

import tkinter as tk
import json, threading, sys, os

try:
    import urllib.request as urlreq
except ImportError:
    urlreq = None

# ── 服务器地址解析（支持本地/远程/Cloudflare Tunnel）──────────────────────
def _resolve_server() -> str:
    # 1. 命令行参数
    if len(sys.argv) > 1 and sys.argv[1].startswith('http'):
        return sys.argv[1].rstrip('/')
    # 2. 环境变量
    env = os.environ.get('SOYA_SERVER', '').strip()
    if env:
        return env.rstrip('/')
    # 3. 同目录下的 widget_config.json
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'widget_config.json')
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if cfg.get('server'):
                return cfg['server'].rstrip('/')
        except Exception:
            pass
    # 4. 默认本机
    return 'http://localhost:5000'

SERVER           = _resolve_server()
POLL_MS          = 2000
MAX_FAILURES     = 4
ALPHA_HOVER      = 0.95
ALPHA_IDLE       = 0.32
W, H             = 250, 118
W_MINI, H_MINI   = 148, 28

C_BG      = '#0e0e1c'
C_PANEL   = '#14142a'
C_BORDER  = '#28284a'
C_SEP     = '#1e1e38'
C_TITLE   = '#9090bb'
C_TEXT    = '#d2d2f0'
C_DIM     = '#50507a'
C_GREEN   = '#3ecf8e'
C_YELLOW  = '#f5a623'
C_RED     = '#f25757'
C_GREY    = '#3a3a5a'
C_TYPING  = '#b39ddb'


class FloatingMonitor:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('涟宗也监控')
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', ALPHA_IDLE)
        self.root.configure(bg=C_BORDER)

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f'{W}x{H}+{sw - W - 22}+{sh - H - 66}')

        self._minimized  = False
        self._fail_count = 0
        self._dx = self._dy = 0

        self._build_ui()
        self._bind_events()
        self._poll()

    def _build_ui(self):
        self._inner = tk.Frame(self.root, bg=C_PANEL)
        self._inner.place(x=1, y=1, width=W - 2, height=H - 2)

        title_row = tk.Frame(self._inner, bg=C_PANEL)
        title_row.pack(fill='x', padx=10, pady=(8, 0))

        self.dot_cv = tk.Canvas(title_row, width=10, height=10,
                                bg=C_PANEL, highlightthickness=0)
        self.dot_cv.pack(side='left', padx=(0, 6))
        self.dot = self.dot_cv.create_oval(1, 1, 9, 9, fill=C_GREY, outline='')

        tk.Label(title_row, text='涟宗也', bg=C_PANEL, fg=C_TITLE,
                 font=('微软雅黑', 8, 'bold')).pack(side='left')
        # 显示连接目标（本机显示"本机"，远程显示域名/IP）
        _srv_label = '本机' if 'localhost' in SERVER or '127.0.0.1' in SERVER else SERVER.replace('https://','').replace('http://','')[:28]
        tk.Label(title_row, text=f' 监控 · {_srv_label}', bg=C_PANEL, fg=C_DIM,
                 font=('微软雅黑', 7)).pack(side='left')

        self._close_btn = tk.Label(title_row, text='✕', bg=C_PANEL, fg=C_DIM,
                                   font=('Arial', 9), cursor='hand2')
        self._close_btn.pack(side='right')
        self._close_btn.bind('<Button-1>', lambda e: self.root.destroy())

        tk.Frame(self._inner, bg=C_SEP, height=1).pack(fill='x', padx=8, pady=(6, 0))

        self.status_var = tk.StringVar(value='连接中…')
        self._status_lbl = tk.Label(self._inner, textvariable=self.status_var,
                                    bg=C_PANEL, fg=C_TEXT,
                                    font=('微软雅黑', 9), anchor='w')
        self._status_lbl.pack(fill='x', padx=10, pady=(6, 0))

        self.window_var = tk.StringVar(value='')
        tk.Label(self._inner, textvariable=self.window_var, bg=C_PANEL, fg=C_DIM,
                 font=('微软雅黑', 7), anchor='w', wraplength=228
                 ).pack(fill='x', padx=10, pady=(2, 0))

        stats = tk.Frame(self._inner, bg=C_PANEL)
        stats.pack(fill='x', padx=10, pady=(5, 8))
        self.clicks_var = tk.StringVar(value='')
        tk.Label(stats, textvariable=self.clicks_var, bg=C_PANEL, fg=C_DIM,
                 font=('Arial', 7), anchor='w').pack(side='left')
        self.time_var = tk.StringVar(value='')
        tk.Label(stats, textvariable=self.time_var, bg=C_PANEL, fg=C_DIM,
                 font=('Arial', 7), anchor='e').pack(side='right')

    def _bind_events(self):
        self.root.bind('<Enter>', lambda e: self.root.attributes('-alpha', ALPHA_HOVER))
        self.root.bind('<Leave>', lambda e: self.root.attributes('-alpha', ALPHA_IDLE))
        self._bind_drag_recursive(self.root)
        self.root.bind('<Double-Button-1>', self._toggle_minimize)

    def _bind_drag_recursive(self, widget):
        if widget is self._close_btn:
            return  # preserve the close button's own click binding
        widget.bind('<ButtonPress-1>',  self._drag_start)
        widget.bind('<B1-Motion>',      self._drag_move)
        for child in widget.winfo_children():
            self._bind_drag_recursive(child)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        x = self.root.winfo_x() + (e.x - self._dx)
        y = self.root.winfo_y() + (e.y - self._dy)
        self.root.geometry(f'+{x}+{y}')

    def _toggle_minimize(self, e=None):
        self._minimized = not self._minimized
        if self._minimized:
            self.root.geometry(f'{W_MINI}x{H_MINI}')
            self._inner.place(x=1, y=1, width=W_MINI - 2, height=H_MINI - 2)
            self.status_var.set('双击展开')
        else:
            self.root.geometry(f'{W}x{H}')
            self._inner.place(x=1, y=1, width=W - 2, height=H - 2)

    def _poll(self):
        def _fetch():
            try:
                r = urlreq.Request(f'{SERVER}/api/activity-status',
                                   headers={'Accept': 'application/json'})
                with urlreq.urlopen(r, timeout=2) as resp:
                    data = json.loads(resp.read().decode())
                self.root.after(0, self._update_ui, data, None)
            except Exception as err:
                self.root.after(0, self._update_ui, None, str(err))

        threading.Thread(target=_fetch, daemon=True).start()
        self.root.after(POLL_MS, self._poll)

    def _update_ui(self, data, err):
        if err or data is None:
            self._fail_count += 1
            self._set_dot(C_GREY)
            if self._fail_count >= MAX_FAILURES:
                self.root.destroy()
                return
            self.status_var.set(f'⚫ 连接中断 ({self._fail_count}/{MAX_FAILURES})')
            self._status_lbl.config(fg=C_DIM)
            self.window_var.set('')
            self.clicks_var.set('')
            self.time_var.set('')
            return

        self._fail_count = 0

        page_visible   = data.get('page_visible', True)
        mouse_idle     = data.get('mouse_idle', False)
        is_typing      = data.get('is_typing', False)
        active_window  = data.get('active_window', '')
        last_update    = data.get('last_update', '')
        session_clicks = data.get('session_clicks', 0)
        timed_out      = data.get('timed_out', False)

        if timed_out or not page_visible:
            color, status = C_RED,    '🔴 已离开聊天页面'
        elif mouse_idle:
            color, status = C_YELLOW, '🟡 鼠标无动作'
        elif is_typing:
            color, status = C_TYPING, '⌨ 正在输入…'
        else:
            color, status = C_GREEN,  '🟢 在线，活跃'

        self._set_dot(color)
        self._status_lbl.config(fg=color)
        self.status_var.set(status)

        win = active_window or ''
        if len(win) > 44:
            win = win[:44] + '…'
        self.window_var.set(f'📌 {win}' if win else '')

        self.clicks_var.set(f'点击 {session_clicks} 次' if session_clicks else '')
        self.time_var.set(last_update if last_update else '')

    def _set_dot(self, color):
        self.dot_cv.itemconfig(self.dot, fill=color)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    FloatingMonitor().run()
