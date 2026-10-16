"""capture.py — 全屏框选层：整屏变暗 + 选区亮洞

从 skills/capture.py（mss 版原型）移植并重写，两者不共用代码。

和原型的三点不同：
  1. **先截图、后弹窗**：图在框选层出现之前就拍好了，所以框选层自己、
     红框、尺寸数字永远不会被拍进发给模型的图里 —— 原型里那句
     time.sleep(0.05) 就是为这个打的补丁，这里从根上不需要了
  2. **不用 -alpha 做变暗**：-alpha 作用于整个窗口，选区会跟着一起暗。
     这里是「预先调暗的整屏截图当底图 + 同一张图的原图裁剪当亮洞」
  3. **分辨率分离**：物理像素(2560x1600)发给模型保证清晰，Tk 坐标
     (2048x1280)给画布显示，比例运行时算 = 截图宽 / Tk屏宽

⚠️ 不要照搬 skills/capture.py 里的 enable_dpi_awareness()。
那个函数会让 Tk 报物理坐标、比例变成 1.0，整个悬浮窗的字号和窗口尺寸
都会跟着变。这里靠运行时算比例来兜，不用动进程的 DPI 感知。
"""
import base64
import io
import time
import tkinter as tk

from PIL import Image, ImageEnhance, ImageGrab, ImageTk

# 全局热键：反引号键（数字 1 左边那个，中文输入法下打出 ·）。
#
# ⚠️ 这里踩过两个坑，改热键时别再踩：
#  1. 不能写 vk 编号 "<192>"。pynput 比对前会先做一次 canonical 规范化，
#     只要按键带字符就把 vk 丢掉、只留字符，于是 from_vk(192)（无字符）
#     永远比不中 —— 表现就是「按了没反应」。
#  2. 不能写 "·"。那是输入法在上层加工出来的，pynput 在底层拿到的是反引号 `，
#     字符对不上。
# 所以：老老实实写底层能看到的那个字符。
HOTKEY = "`"
MIN_SIZE = 5                   # 小于 5 像素的框当作误触
DARK = 0.35                    # 变暗程度：1=原图，0=全黑
REPAINT_MS = 30                # 拖动时重画亮洞的最小间隔（约 33 帧/秒）
MAX_PNG_BYTES = 4_000_000      # 超过就先缩小再编码，避免请求体过大被通道拒
HIDE_ROOT_ON_GRAB = False      # 抓屏前要不要把悬浮窗藏起来（藏约 60 毫秒，抓完立刻恢复）
                               # True  = 截图里绝不会有悬浮窗，代价是会闪一下
                               # False = 悬浮窗不闪，但框选区如果盖住它，它会被拍进图里


class CaptureOverlay:
    """框选层。零业务逻辑，只通过 on_selected / on_cancel 两个回调对外说话。

    状态：idle → armed → dragging → selected，任意状态都能 cancel() 回 idle。
    """

    def __init__(self, root, on_selected=None, on_cancel=None):
        self.root = root
        self.on_selected = on_selected     # 回调：收到 {b64, media_type, w, h, ...}
        self.on_cancel = on_cancel         # 回调：用户取消（Esc / 右键）
        self.overlay = None                # Toplevel；None = 没开
        self.canvas = None
        self.state = "idle"

        self.vx = self.vy = 0
        self.scale = 1.0
        self.shot_full = None              # Pillow Image：物理分辨率整屏（发送用）
        self.shot_disp = None              # Pillow Image：Tk 坐标分辨率整屏（显示用）

        # 这两个引用必须一直拿着！PhotoImage 被垃圾回收后画布上会变空白
        self._base_photo = None
        self._hole_photo = None

        self._hole = self._rect = self._label = None
        self._box = None                   # 当前框（画布坐标）
        self._prev_box = None              # 误触时用来还原上一个框
        self._origin = None
        self._repaint_job = None

    @property
    def active(self):
        return self.overlay is not None

    # ---------- 开关 ----------

    def open(self):
        """截图并弹出框选层。已经开着就忽略。"""
        if self.active:
            return

        # ① 先截图。悬浮窗是置顶半透明的，不藏起来就会出现在截图里
        was_visible = False
        if HIDE_ROOT_ON_GRAB:
            try:
                was_visible = self.root.state() == "normal"
                if was_visible:
                    self.root.withdraw()
                    self.root.update()      # 让 Tk 把隐藏指令真的发出去
                    time.sleep(0.06)        # 等桌面合成器擦掉它，否则可能残留
            except tk.TclError:
                was_visible = False
        try:
            full = ImageGrab.grab(all_screens=True)     # 物理像素，如 2560x1600
        finally:
            if was_visible:
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)

        vx, vy = self.root.winfo_vrootx(), self.root.winfo_vrooty()
        vw, vh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        self.scale = full.width / vw        # 运行时算，别写死 1.25
        disp = (full.resize((vw, vh), Image.LANCZOS)
                if (full.width, full.height) != (vw, vh) else full.copy())
        dark = ImageEnhance.Brightness(disp).enhance(DARK)

        self.shot_full, self.shot_disp = full, disp
        self.vx, self.vy = vx, vy

        # ② 弹窗。注意：不设 -alpha！
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{vw}x{vh}+{vx}+{vy}")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, cursor="cross", highlightthickness=0, bd=0, bg="black")
        cv.pack(fill="both", expand=True)
        self.overlay, self.canvas = ov, cv

        # ③ 叠放顺序就是创建顺序，之后只改内容不重建，Z 序自然不乱
        self._base_photo = ImageTk.PhotoImage(dark)
        cv.create_image(0, 0, anchor="nw", image=self._base_photo)
        self._hole = cv.create_image(0, 0, anchor="nw", state="hidden")
        self._rect = cv.create_rectangle(0, 0, 0, 0, outline="#3d7eff",
                                         width=2, state="hidden")
        self._label = cv.create_text(0, 0, text="", fill="#ffd479",
                                     font=("Microsoft YaHei", 11, "bold"),
                                     state="hidden")
        cv.create_text(vw // 2, 34, text="Drag to select  ·  Esc to cancel",
                       fill="#e8e8e8", font=("Microsoft YaHei", 12))

        # ④ 事件
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Button-3>", lambda e: self.cancel())      # 右键 = 取消

        # ⑤ 抢鼠标。必须等窗口可见，否则 TclError: grab failed: window not viewable
        ov.update_idletasks()
        self._take_grab()
        ov.focus_force()        # 尽力而为；Esc 不指望这里，靠全局监听
        self.state = "armed"

    def cancel(self):
        """Esc / 右键：关掉框选层，并通知主人清掉待发图。"""
        was_open = self.active
        self.close()
        if was_open and self.on_cancel:
            self.on_cancel()

    def close(self):
        """只关窗，不触发 on_cancel（发出去之后走这个）。"""
        if self._repaint_job is not None and self.overlay is not None:
            try:
                self.overlay.after_cancel(self._repaint_job)
            except tk.TclError:
                pass
        self._repaint_job = None
        self._release_grab()
        if self.overlay is not None:
            try:
                self.overlay.destroy()
            except tk.TclError:
                pass
        self.overlay = self.canvas = None
        # 放掉约 30MB 的位图
        self._base_photo = self._hole_photo = None
        self.shot_full = self.shot_disp = None
        self._box = self._prev_box = self._origin = None
        self.state = "idle"

    def _take_grab(self):
        if self.overlay is None:
            return
        try:
            self.overlay.grab_set()
        except tk.TclError:
            pass                # 窗口还没 map 好；隐式抓仍能覆盖本次拖拽

    def _release_grab(self):
        if self.overlay is None:
            return
        try:
            self.overlay.grab_release()
        except tk.TclError:
            pass                # 本来就没抓着

    # ---------- 鼠标 ----------

    def _on_press(self, e):
        if self.overlay is None:
            return
        # 松手后抓是放开的（为了悬浮窗能点/能打字），重新画框时再抢回来
        if self.overlay.grab_current() is None:
            self._take_grab()
        self._origin = (e.x, e.y)
        self._prev_box = self._box
        self._box = None
        self.state = "dragging"
        self._hide_hole()

    def _on_drag(self, e):
        if self._origin is None:
            return
        x0, y0 = self._origin
        self._box = (min(x0, e.x), min(y0, e.y), max(x0, e.x), max(y0, e.y))
        self._schedule_repaint()

    def _on_release(self, e):
        if self._origin is None:
            return
        self._on_drag(e)                    # 用最后位置定框
        box, self._origin = self._box, None
        w = box[2] - box[0] if box else 0
        h = box[3] - box[1] if box else 0

        if w < MIN_SIZE or h < MIN_SIZE:
            # 误触：不关窗、不丢旧框（Esc 才是唯一出口）
            self._box = self._prev_box
            if self._box:
                self._paint_now()
                self.state = "selected"
                self._release_grab()
            else:
                self._hide_hole()
                self.state = "armed"        # 抓还在，继续等
            return

        self._paint_now()                   # 先把最终画面画出来，立刻有反馈
        self.state = "selected"
        self._release_grab()                # 放开鼠标，悬浮窗才能点、才能打字
        if self.on_selected:
            self.on_selected(self._build_shot(box))

    # ---------- 画亮洞 ----------

    def _schedule_repaint(self):
        """脏标记 + 单次 after：鼠标每秒能报 100+ 次，全画一遍会雪崩。"""
        if self._repaint_job is None and self.overlay is not None:
            self._repaint_job = self.overlay.after(REPAINT_MS, self._paint_now)

    def _paint_now(self):
        self._repaint_job = None
        if self.overlay is None or not self._box:
            return
        x0, y0, x1, y1 = self._box
        if x1 - x0 < 1 or y1 - y0 < 1:
            self._hide_hole()               # 0 尺寸的图 Tk 画不出来
            return

        # 显示用的裁剪走 Tk 坐标，在已经缩放好的 shot_disp 上裁
        photo = ImageTk.PhotoImage(self.shot_disp.crop((x0, y0, x1, y1)))
        self.canvas.coords(self._hole, x0, y0)
        self.canvas.itemconfig(self._hole, image=photo, state="normal")
        # 顺序要紧：先让画布换上新图，再放掉旧图的引用。
        # 反过来写会让画布短暂指向一个已经销毁的 Tk image，闪白
        self._hole_photo = photo

        self.canvas.coords(self._rect, x0, y0, x1, y1)
        self.canvas.itemconfig(self._rect, state="normal")
        lx = min(x1 + 8, self.canvas.winfo_width() - 80)
        ly = min(y1 + 8, self.canvas.winfo_height() - 24)
        self.canvas.coords(self._label, lx, ly)
        self.canvas.itemconfig(self._label, text=f"{x1 - x0} x {y1 - y0}",
                               state="normal")

    def _hide_hole(self):
        if self.canvas is None:
            return
        for item in (self._hole, self._rect, self._label):
            self.canvas.itemconfig(item, state="hidden")

    # ---------- 坐标换算 + 编码 ----------

    def _to_physical(self, box):
        """画布坐标 → 物理像素。整份代码里唯一一处 ×scale。"""
        x0, y0, x1, y1 = box
        s, vx, vy = self.scale, self.vx, self.vy
        return (round((vx + x0) * s), round((vy + y0) * s),
                round((vx + x1) * s), round((vy + y1) * s))

    def _build_shot(self, box):
        """松手时同步编码好，send() 里就只剩发送了。"""
        pbox = self._to_physical(box)
        crop = self.shot_full.crop(pbox)    # 发送用物理原图，不是缩放版
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        if buf.tell() > MAX_PNG_BYTES:      # 软保护：太大先缩再编码
            k = (MAX_PNG_BYTES / buf.tell()) ** 0.5
            crop = crop.resize((max(1, int(crop.width * k)),
                                max(1, int(crop.height * k))), Image.LANCZOS)
            buf = io.BytesIO()
            crop.save(buf, "PNG")
        return {
            "b64": base64.standard_b64encode(buf.getvalue()).decode("utf-8"),
            "media_type": "image/png",
            "w": crop.width,
            "h": crop.height,
            "box_tk": box,
            "box_phys": pbox,
            "scale": self.scale,
        }
