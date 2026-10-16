"""Generate the demo image at the top of the README (docs/demo.gif).

Run: python make_demo.py

WARNING: a full-screen window covers your desktop for about 5 seconds — that is
unavoidable, because the selector works by grabbing the screen, so there has to
be something on it to grab. It grabs the SYNTHETIC desktop this script draws,
not your real screen, so nothing you have open is captured.

Re-run this after changing how the selector looks (dim level, outline colour,
hint text) and the demo image updates with it.
"""
import sys
import time
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from capture import CaptureOverlay

HERE = Path(__file__).resolve().parent
OUT = HERE / "docs"

# What the demo frames are cropped to (Tk logical coordinates)
CROP_TK = (0, 0, 1700, 1150)
# How long each frame is held, in milliseconds: a beat on "the screen dims",
# a beat on the finished selection. Must match the actual frame count — 5 frames:
# one fully dimmed plus four steps of the selection.
# (Don't append another identical "hold" frame at the end: PIL merges consecutive
#  identical frames when saving a GIF and folds their duration into the previous
#  one, which leaves the frame count out of sync with DURATIONS.)
DURATIONS = [700, 250, 250, 250, 1400]


def make_desktop(w, h):
    """Draw a plausible-looking "code editor" so the dimming has something to contrast with.

    Deliberately made of coloured blocks rather than real text — it reads as
    content at a glance without inventing any particular code or UI.
    """
    img = Image.new("RGB", (w, h), "#1b1d23")
    d = ImageDraw.Draw(img)

    # Title bar + three window dots
    d.rectangle([0, 0, w, 52], fill="#23262e")
    for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840")):
        x = 24 + i * 30
        d.ellipse([x, 18, x + 16, 34], fill=c)
    d.rounded_rectangle([130, 14, 560, 38], radius=8, fill="#2d313a")

    # Left sidebar
    d.rectangle([0, 52, 330, h], fill="#22242b")
    for i in range(20):
        y = 84 + i * 48
        if y > h - 40:
            break
        if i == 5:      # pretend this row is selected
            d.rounded_rectangle([12, y - 10, 318, y + 26], radius=8, fill="#2f3540")
        d.rounded_rectangle([34, y, 34 + (170 + (i * 41) % 110), y + 13], radius=6,
                            fill="#6f7d94" if i == 5 else "#464e5c")

    # Main area: pretend this is syntax-highlighted code — coloured rounded bars
    palette = ("#7aa2f7", "#9ece6a", "#e0af68", "#bb9af7", "#7dcfff", "#f7768e")
    for row in range(26):
        y = 92 + row * 46
        if y > h - 50:
            break
        x = 390 + (row % 4) * 34          # indentation
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

    # The reference must be held, or the PhotoImage is collected and the
    # background turns white
    photo = ImageTk.PhotoImage(make_desktop(vw, vh))
    tk.Label(root, image=photo, bd=0, highlightthickness=0).pack(fill="both",
                                                                 expand=True)
    root.update()
    time.sleep(0.4)              # let the compositor actually paint this backdrop

    frames = []
    cap = CaptureOverlay(root)
    try:
        cap.open()               # the real selector: grab the screen, build the dark copy

        # First frame: just opened, nothing selected yet — so "the whole screen
        # dims" is visible on its own
        cap._hide_hole()
        root.update()
        time.sleep(0.5)
        frames.append(ImageGrab.grab(all_screens=True))

        # Then the box grows, through the real drawing code
        boxes = [
            (620, 300, 760, 400),      # just started dragging
            (620, 300, 980, 580),
            (620, 300, 1180, 760),
            (620, 300, 1340, 900),     # settled (this frame is held longest)
        ]
        for box in boxes:
            cap._box = box
            cap._paint_now()           # real code: crop the original, paste the hole,
            root.update()              # draw the outline and size label
            time.sleep(0.12)           # let it land on screen
            frames.append(ImageGrab.grab(all_screens=True))
    finally:
        cap.close()
        root.destroy()

    # Crop to the region of interest, then shrink. (The grabs are physical pixels,
    # so the Tk-space crop has to be scaled.)
    scale = frames[0].width / vw
    crop = tuple(int(v * scale) for v in CROP_TK)
    small = []
    for f in frames:
        c = f.crop(crop)
        small.append(c.resize((720, round(720 * c.height / c.width)), Image.LANCZOS))

    out = OUT / "demo.gif"
    small[0].save(out, save_all=True, append_images=small[1:],
                  duration=DURATIONS, loop=0, optimize=True)
    print(f"wrote {out}  ({len(small)} frames, {out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    sys.exit(main())
