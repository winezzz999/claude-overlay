"""生成 README 顶部那张演示图（docs/demo.gif）。

跑法：python make_demo.py

⚠️ 运行时会有一个全屏窗口盖住你的桌面约 5 秒 —— 这是必须的，因为
   框选层是靠「抓屏」工作的，得先有东西可抓。**它抓的是脚本自己画的
   合成桌面，不是你的真实屏幕**，所以不会把你桌面上开着的东西拍进去。

改了框选层的外观（变暗程度、边框颜色、提示文字）之后重跑一次，
演示图就跟着更新了。
"""
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from capture import CaptureOverlay

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs"

# 演示图的取景（Tk 逻辑坐标），想让取景框挪个位置就改这里
CROP_TK = (0, 0, 1700, 1150)
# 各帧停留时长（毫秒）：先停一下看清「整屏变暗」，最后停一下看清「框选结果」
# 注意要和实际帧数对齐 —— 共 5 帧：1 帧全暗 + 4 帧框选过程。
# （别在后面再加一帧一模一样的「停一拍」：PIL 存 GIF 时会把连续相同的帧
#   合并掉，时长叠加到前一帧，结果帧数和 DURATIONS 对不上。）
DURATIONS = [700, 250, 250, 250, 1400]


def make_desktop(w, h):
    """画一个像模像样的「代码编辑器」，让变暗/变亮有对比可看。

    这里故意用色块而不是真文字 —— 一眼能看出是内容，又不用编造任何
    具体的代码或界面。
    """
    img = Image.new("RGB", (w, h), "#1b1d23")
    d = ImageDraw.Draw(img)

    # 顶栏 + 三个窗口圆点
    d.rectangle([0, 0, w, 52], fill="#23262e")
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        x = 24 + i * 30
        d.ellipse([x, 18, x + 16, 34], fill=c)
    d.rounded_rectangle([130, 14, 560, 38], radius=8, fill="#2d313a")

    # 左侧边栏
    d.rectangle([0, 52, 330, h], fill="#22242b")
    for i in range(20):
        y = 84 + i * 48
        if y > h - 40:
            break
        if i == 5:      # 假装这一项是选中的
            d.rounded_rectangle([12, y - 10, 318, y + 26], radius=8, fill="#2f3540")
        d.rounded_rectangle([34, y, 34 + (170 + (i * 41) % 110), y + 13], radius=6,
                            fill="#6f7d94" if i == 5 else "#464e5c")

    # 主区：假装是语法高亮的代码 —— 不同颜色的圆角条
    palette = ("#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#7dcfff", "#f7768e")
    for row in range(26):
        y = 92 + row * 46
        if y > h - 50:
            break
        x = 390 + (row % 4) * 34          # 缩进
        for k in range(2 + (row * 7) % 5):
            width = 70 + ((row * 53 + k * 71) % 260)
            d.rounded_rectangle([x, y, x + width, y + 17], radius=5,
                                fill=palette[(row + k) % len(palette)])
            x += width + 26
            if x > w - 90:
                break
    return img


def main():
    OUT.mkdir(exist_ok=True)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.config(bg="black")
    vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
    root.geometry(f"{vw}x{vh}+{root.winfo_vrootx()}+{root.winfo_vrooty()}")

    # 必须留引用，否则 PhotoImage 被回收、背景变白
    photo = ImageTk.PhotoImage(make_desktop(vw, vh))
    tk.Label(root, image=photo, bd=0, highlightthickness=0).pack(fill="both",
                                                                 expand=True)
    root.update()
    time.sleep(0.4)              # 等桌面合成器真的把这块背景画出来

    frames = []
    cap = CaptureOverlay(root)
    try:
        cap.open()               # 走真实的框选层：抓屏 + 建变暗底图

        # 第一帧：刚弹出、还没开始框，让人看清「整屏变暗」这件事本身
        cap._hide_hole()
        root.update()
        time.sleep(0.5)
        frames.append(ImageGrab.grab(all_screens=True))

        # 接着框从小到大，走的是真实的绘制代码
        boxes = [
            (620, 300, 760, 400),      # 刚拖出一点
            (620, 300, 980, 580),
            (620, 300, 1180, 760),
            (620, 300, 1340, 900),     # 定稿（这一帧停留最久）
        ]
        for box in boxes:
            cap._box = box
            cap._paint_now()           # 真实代码：裁原图贴亮洞 + 画边框和尺寸
            root.update()
            time.sleep(0.12)           # 等渲染落屏
            frames.append(ImageGrab.grab(all_screens=True))
    finally:
        cap.close()
        root.destroy()

    # 裁到取景范围再缩小（截到的是物理像素，要按比例换算）
    scale = frames[0].width / vw
    crop = tuple(int(v * scale) for v in CROP_TK)
    small = []
    for f in frames:
        c = f.crop(crop)
        small.append(c.resize((720, round(720 * c.height / c.width)), Image.LANCZOS))

    out = OUT / "demo.gif"
    small[0].save(out, save_all=True, append_images=small[1:],
                  duration=DURATIONS, loop=0, optimize=True)
    print(f"已写出 {out}  （{len(small)} 帧，{out.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    sys.exit(main())
