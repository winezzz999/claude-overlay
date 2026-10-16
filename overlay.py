"""overlay.py — 置顶半透明悬浮窗（tkinter）

运行：python overlay.py
特性：置顶、半透明、无边框、灰色背景（颜色/透明度可自定义，保存到 config.json）

两种问法：
  打字回车   → 走 agent.py 的 Claude 会话，有上下文、能用工具
  F1 框选    → 截图随问题一起发给模型（独立的一次性问答，见 vision.py）
               热键想改的话，改 capture.py 顶部的 HOTKEY 一行即可
"""
import asyncio
import ctypes
import json
import queue
import threading
import tkinter as tk
from pathlib import Path

from pynput import keyboard

from agent import ClaudeSession
from capture import HOTKEY, CaptureOverlay
from vision import VisionSession


def _allow_foreground():
    """Windows 只允许前台进程抢键盘焦点。

    热键是从别的程序里按下的，那一刻本进程不是前台，SetForegroundWindow
    会被拒绝（只在任务栏闪一下）—— 表现出来就是「框完选截图，打字没反应」。
    这一句一次性放开限制，必须在第一次抢焦点之前调用。
    """
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        pass

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
        _allow_foreground()   # 必须赶在第一次抢焦点之前

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

        # --- 截图提问这条线 ---
        self.vision = VisionSession()
        self.pending_shot = None    # 待发送的截图；None = 没有。下一次 send() 消费它
        self.capture = CaptureOverlay(   # 构造不弹窗，open() 才弹
            self.root, on_selected=self._on_region_selected,
            on_cancel=self._on_capture_cancel)
        self._events = queue.Queue()     # pynput 线程 → Tk 线程
        self._esc_bound = False          # 初值 False，好让首次 _sync_esc() 真的去绑

        # 异步事件循环跑在后台线程：SDK 的流式回答不阻塞界面
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        self._build_ui()
        self._start_hotkeys()
        self.root.after(30, self._drain_events)   # 排空 pynput 线程投来的信号
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
                  command=self._quit).pack(side="right", padx=8)
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
        self.chat.tag_configure("shot", background="#33452f", foreground="#c8e6c8",
                                lmargin1=10, lmargin2=10, rmargin=10,
                                spacing1=4, spacing3=4)

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
        self._sync_esc()                                     # Esc 的归属见该方法

    # ---------- 窗口拖动 ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------- 截图提问 ----------
    def _start_hotkeys(self):
        # pynput 的回调跑在它自己的线程里，只往队列丢信号，绝不直接碰 Tk
        self._hotkey_listener = keyboard.GlobalHotKeys(
            {HOTKEY: lambda: self._events.put("HOTKEY")})
        self._hotkey_listener.start()
        self._esc_listener = keyboard.Listener(on_press=self._on_esc_press)
        self._esc_listener.start()

    def _on_esc_press(self, key):
        if key == keyboard.Key.esc:
            self._events.put("ESC")

    def _drain_events(self):
        """每 30ms 在 Tk 线程里把队列里的信号取出来执行（Tk 不是线程安全的）"""
        try:
            while True:
                ev = self._events.get_nowait()
                if ev == "HOTKEY":
                    self._start_capture()
                elif ev == "ESC":
                    self.cancel_capture()
        except queue.Empty:
            pass
        self._sync_esc()
        self.root.after(30, self._drain_events)

    def _start_capture(self):
        if self.sending or self.capture.active:
            return              # 正在等回答 / 框选层已经开着 → 忽略这次热键
        try:
            self.capture.open()
        except Exception as ex:
            self._append_block(f"\n⚠️ 截图失败：{type(ex).__name__}: {ex}\n", "err")
        self._sync_esc()

    def _on_region_selected(self, shot):
        self.pending_shot = shot        # 交给下一次 send() 消费
        self._append_block(
            f"📷 已框选 {shot['w']}x{shot['h']}，输入问题后回车发送"
            f"（Esc 取消）\n", "shot")
        self._raise_chat_window()       # 阶段2：抬窗 + 抢键盘焦点

    def _on_capture_cancel(self):
        self.pending_shot = None
        self._sync_esc()

    def cancel_capture(self):
        """幂等：全局监听和窗口绑定可能对同一次 Esc 各触发一次"""
        self.pending_shot = None
        if self.capture.active:
            self.capture.cancel()
        self._sync_esc()

    def _sync_esc(self):
        """框选层开着时把窗口的 Esc 解绑，让全局监听独占 Esc。

        否则同一次按键会被两条路径各处理一次：全局监听先关掉框选层，
        紧接着 Tk 绑定看到「没有截图在进行」，就把整个程序退掉了。
        """
        want = not self.capture.active
        if want == self._esc_bound:
            return
        self._esc_bound = want
        if want:
            self.root.bind("<Escape>", self._on_escape_key)
        else:
            self.root.unbind("<Escape>")

    def _on_escape_key(self, event):
        """只有没有截图在进行时才会走到这里（框选期间 Esc 是解绑的）"""
        self._quit()

    def _raise_chat_window(self):
        """阶段2：把悬浮窗抬到框选层之上，并且抢到键盘焦点。

        只 lift() 只解决 Z 序 —— overrideredirect 窗口的键盘焦点得靠
        SetForegroundWindow 争，这依赖启动时调过的 _allow_foreground()。
        """
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", False)   # 先摘掉才抬得动
            self.root.lift()
            self.root.attributes("-topmost", True)    # 再钉回去
            ctypes.windll.user32.SetForegroundWindow(self.root.winfo_id())
        except Exception:
            pass
        self.root.focus_force()
        self.input.focus_set()

    def _quit(self):
        for lis in (getattr(self, "_hotkey_listener", None),
                    getattr(self, "_esc_listener", None)):
            try:
                lis.stop()
            except Exception:
                pass
        self.root.destroy()

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
        """Enter 或点发送。有待发截图就一起带上，没有就走普通对话。"""
        if self.sending:
            return "break"
        text = self.input.get("1.0", "end").strip()
        if not text:
            return "break"

        shot = self.pending_shot
        self.pending_shot = None       # 图只发一次，不管成败
        self.input.delete("1.0", "end")

        if shot is not None:
            self._append_block(
                f"🙂 你：[截图 {shot['w']}x{shot['h']}] {text}\n", "user")
            self.capture.close()       # 发出去了 = 阶段2结束，覆盖层消失
        else:
            if self.capture.active:    # 开着框选层却在打纯文字 → 放弃那个框
                self.capture.close()
            self._append_block(f"🙂 你：{text}\n", "user")
        self._sync_esc()

        self._append_block("🤖 Claude：", "bot")
        self.sending = True
        self.send_btn.config(state="disabled", text="等待中")
        # 把异步任务提交给后台线程的事件循环，绝不阻塞界面
        coro = self._run_vision(text, shot) if shot else self._run(text)
        asyncio.run_coroutine_threadsafe(coro, self.loop)
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

    async def _run_vision(self, prompt, shot):
        """截图提问：结构和 _run 一样，只是换成带图的一次性通道（vision.py）"""
        try:
            async for delta in self.vision.ask_stream(
                    prompt, shot["b64"], shot["media_type"]):
                self.root.after(0, self._append, delta)
        except Exception as ex:
            self.root.after(0, self._append_block,
                            f"\n⚠️ 截图提问失败：{type(ex).__name__}: {ex}\n", "err")
        finally:
            self.root.after(0, self._finish)

    def _finish(self):
        self.sending = False
        self.send_btn.config(state="normal", text="发送")
        self._append_block("\n", "bot")


if __name__ == "__main__":
    FloatingChat()
