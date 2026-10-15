"""overlay.py — 置顶半透明悬浮窗（tkinter 版，Python 自带零依赖）

运行：python overlay.py
特性：置顶、半透明、无边框、灰色背景（颜色/透明度可自定义，保存到 config.json）
"""
import asyncio
import json
import threading
import tkinter as tk
from pathlib import Path
from agent import ClaudeSession

CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULTS = {"bg": "#2e2e2e", "opacity": 0.92}
PRESET_COLORS = ["#2e2e2e", "#1e1e1e", "#23272e", "#3d3d3d"]   # 灰/深灰/蓝灰/浅灰
FONT = ("Microsoft YaHei", 10)


def load_config():
    try:
        return {**DEFAULTS, **json.loads(CONFIG_FILE.read_text(encoding="utf-8"))}
    except Exception:
        return dict(DEFAULTS)


def save_config(cfg):
    CONFIG_FILE.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class FloatingChat:
    def __init__(self):
        self.cfg = load_config()
        self.session = ClaudeSession(cwd=str(Path(__file__).parent))
        self.sending = False
        self._paint = []   # 所有需要跟着换背景色的控件

        self.root = tk.Tk()
        self.root.overrideredirect(True)                    # 无边框（关闭按钮在标题条上）
        self.root.attributes("-topmost", True)              # 置顶
        self.root.attributes("-alpha", self.cfg["opacity"]) # 整体半透明
        self.root.geometry("430x580+120+120")
        self._paint.append(self.root)

        # 异步事件循环跑在后台线程：SDK 的流式回答不阻塞界面
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        self._build_ui()
        self.root.mainloop()

    # ---------- 界面 ----------
    def _build_ui(self):
        fg = "#e8e8e8"

        # 标题条（按住它拖动窗口）
        title = tk.Frame(self.root, bg="#3a3a3a")
        title.pack(fill="x")
        label = tk.Label(title, text="Claude 悬浮窗", bg="#3a3a3a", fg=fg,
                         font=("Microsoft YaHei", 10, "bold"))
        label.pack(side="left", padx=10, pady=5)
        tk.Button(title, text="✕", bg="#3a3a3a", fg=fg, bd=0, font=FONT,
                  command=self.root.destroy).pack(side="right", padx=8)
        tk.Button(title, text="⚙", bg="#3a3a3a", fg=fg, bd=0, font=FONT,
                  command=self.toggle_settings).pack(side="right", padx=2)
        for w in (title, label) + tuple(title.winfo_children()):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # 设置面板（默认收起）
        self.settings = tk.Frame(self.root, bg=self.cfg["bg"])
        self._paint.append(self.settings)
        tk.Label(self.settings, text="背景颜色", bg=self.cfg["bg"], fg=fg,
                 font=FONT).pack(anchor="w")
        row = tk.Frame(self.settings, bg=self.cfg["bg"])
        self._paint.append(row)
        row.pack(anchor="w", pady=2)
        for c in PRESET_COLORS:
            tk.Button(row, bg=c, width=3, relief="flat", bd=1, cursor="hand2",
                      command=lambda col=c: self.set_bg(col)).pack(side="left", padx=2)
        tk.Label(self.settings, text="透明度", bg=self.cfg["bg"], fg=fg,
                 font=FONT).pack(anchor="w")
        self.opacity_scale = tk.Scale(self.settings, from_=0.3, to=1.0,
                                      resolution=0.01, orient="horizontal",
                                      bg=self.cfg["bg"], fg=fg, highlightthickness=0,
                                      command=self.set_opacity)
        self._paint.append(self.opacity_scale)
        self.opacity_scale.set(self.cfg["opacity"])
        self.opacity_scale.pack(fill="x")

        # 聊天区：用 Text 的 tag 模拟气泡（你=蓝底，Claude=灰底）
        self.chat = tk.Text(self.root, bg=self.cfg["bg"], fg=fg, wrap="word",
                            bd=0, font=FONT, state="disabled", padx=10, pady=8,
                            cursor="arrow")
        self.chat.pack(fill="both", expand=True)
        self._paint.append(self.chat)
        self.chat.tag_configure("user", background="#3d7eff", foreground="#ffffff",
                                lmargin1=10, lmargin2=10, rmargin=10,
                                spacing1=4, spacing3=4)
        self.chat.tag_configure("bot", background="#3a3a3a", foreground="#e8e8e8",
                                lmargin1=10, lmargin2=10, rmargin=10,
                                spacing1=4, spacing3=4)
        self.chat.tag_configure("err", background="#5a2e2e", foreground="#ffb0b0")

        # 输入区
        bottom = tk.Frame(self.root, bg=self.cfg["bg"])
        self._paint.append(bottom)
        bottom.pack(fill="x", pady=6, padx=8)
        self.input = tk.Text(bottom, height=3, bg="#3a3a3a", fg=fg, wrap="word",
                             font=FONT, insertbackground=fg)
        self.input.pack(side="left", fill="x", expand=True)
        self.send_btn = tk.Button(bottom, text="发送", bg="#3d7eff", fg="white",
                                  font=FONT, command=self.send, padx=12)
        self.send_btn.pack(side="left", padx=(6, 0))
        self.input.bind("<Return>", self.send)               # Enter 发送
        self.input.bind("<Shift-Return>", lambda e: None)    # Shift+Enter 换行
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    # ---------- 窗口拖动 ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------- 背景自定义 ----------
    def toggle_settings(self):
        if self.settings.winfo_manager():
            self.settings.pack_forget()                 # 收起
        else:
            self.settings.pack(fill="x", before=self.chat)  # 展开在聊天区上方

    def set_bg(self, color):
        self.cfg["bg"] = color
        for w in self._paint:
            w.configure(bg=color)
        save_config(self.cfg)

    def set_opacity(self, value):
        self.cfg["opacity"] = round(float(value), 2)
        self.root.attributes("-alpha", self.cfg["opacity"])
        save_config(self.cfg)

    # ---------- 聊天 ----------
    def send(self, event=None):
        """Enter 或点发送：把输入框文字发给 Claude"""
        if self.sending:
            return "break"
        text = self.input.get("1.0", "end").strip()
        if not text:
            return "break"
        self.input.delete("1.0", "end")
        self._append_block(f"🙂 你：{text}\n", "user")
        self._append_block("🤖 Claude：", "bot")
        self.sending = True
        self.send_btn.config(state="disabled", text="等待中")
        # 把异步任务提交给后台线程的事件循环，绝不阻塞界面
        asyncio.run_coroutine_threadsafe(self._run(text), self.loop)
        return "break"   # 阻止 Enter 在输入框里插入换行

    def _append_block(self, s, tag):
        self.chat.config(state="normal")
        self.chat.insert("end", s, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    async def _run(self, prompt):
        """在后台线程的事件循环里执行：逐段接收回答"""
        try:
            async for delta in self.session.ask_stream(prompt):
                # tkinter 的 after 可以跨线程安全地调度UI更新
                self.root.after(0, self._append, delta)
        except Exception as ex:
            self.root.after(0, self._append_block, f"\n⚠️ 出错：{ex}\n", "err")
        finally:
            self.root.after(0, self._finish)

    def _append(self, delta):
        self.chat.config(state="normal")
        self.chat.insert("end", delta, "bot")
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _finish(self):
        self.sending = False
        self.send_btn.config(state="normal", text="发送")
        self._append_block("\n", "bot")


if __name__ == "__main__":
    FloatingChat()
