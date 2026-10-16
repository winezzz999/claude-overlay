"""capture.py — full-screen region selector: dim the screen, keep the selection bright

Ported and rewritten from skills/capture.py (the mss-based prototype); the two
do not share code.

Three differences from the prototype:
  1. **Screenshot first, window second.** The image is taken before the selector
     appears, so the selector itself, the outline and the size label can never
     end up in the image that gets sent to the model — the prototype's
     `time.sleep(0.05)` was a patch for exactly that, and is no longer needed.
  2. **No `-alpha` for the dimming.** `-alpha` applies to the whole window, so
     the selection would dim along with everything else. Instead: a pre-darkened
     copy of the screenshot as the background, with the *original* pixels
     pasted back in as the selection.
  3. **Two resolutions.** Physical pixels (2560x1600) go to the model so the
     image stays sharp; Tk coordinates (2048x1280) go to the canvas for display.
     The ratio is computed at runtime as `screenshot width / tk screen width`.

WARNING: do not copy `enable_dpi_awareness()` over from skills/capture.py.
That makes Tk report physical coordinates and the ratio become 1.0, which
changes the font size and dimensions of the whole floating window. The runtime
ratio below covers it without touching the process's DPI awareness.
"""
import base64
import io
import time
import tkinter as tk

from PIL import Image, ImageEnhance, ImageGrab, ImageTk

# Global hotkey: the backtick key (left of the 1, which types · under a Chinese IME).
#
# WARNING: two traps here, both hit for real — don't repeat them.
#  1. Do NOT write the vk number "<192>". Before comparing a hotkey, pynput runs
#     a `canonical` normalisation that DROPS the vk whenever the key carries a
#     character, keeping only the character. So `from_vk(192)` (no character)
#     never matches — the symptom is "the hotkey just doesn't fire".
#  2. Do NOT write "·" either. That character is produced by the IME at a higher
#     layer; at the low level pynput sees a backtick, so the characters differ.
# Conclusion: write the character the low level actually sees.
HOTKEY = "`"
MIN_SIZE = 5                   # boxes smaller than 5px count as a mis-click
DARK = 0.35                    # dimming: 1 = original, 0 = fully black
REPAINT_MS = 30                # min interval between hole repaints (~33 fps)
MAX_PNG_BYTES = 4_000_000      # above this, shrink before encoding
HIDE_ROOT_ON_GRAB = False      # hide the floating window while grabbing (~60ms)?
                               # True  = never in the screenshot, at the cost of a flicker
                               # False = no flicker, but a selection covering it
                               #         will capture the window itself


class CaptureOverlay:
    """The selector. No business logic — it only talks through two callbacks.

    States: idle -> armed -> dragging -> selected. cancel() works from any state.
    """

    def __init__(self, root, on_selected=None, on_cancel=None):
        self.root = root
        self.on_selected = on_selected     # callback: receives {b64, media_type, w, h, ...}
        self.on_cancel = on_cancel         # callback: user cancelled (Esc / right-click)
        self.overlay = None                # Toplevel; None = not open
        self.canvas = None
        self.state = "idle"

        self.vx = self.vy = 0
        self.scale = 1.0
        self.shot_full = None              # Pillow Image: physical res (to send)
        self.shot_disp = None              # Pillow Image: Tk res (to display)

        # These two references must be held! A garbage-collected PhotoImage
        # renders as blank on the canvas.
        self._base_photo = None
        self._hole_photo = None

        self._hole = self._rect = self._label = None
        self._box = None                   # current box (canvas coordinates)
        self._prev_box = None              # to restore after a mis-click
        self._origin = None
        self._repaint_job = None

    @property
    def active(self):
        return self.overlay is not None

    # ---------- open / close ----------

    def open(self):
        """Grab the screen and show the selector. No-op if already open."""
        if self.active:
            return

        # (1) Screenshot first. The floating window is topmost and translucent,
        #     so it would show up in the capture unless hidden.
        was_visible = False
        if HIDE_ROOT_ON_GRAB:
            try:
                was_visible = self.root.state() == "normal"
                if was_visible:
                    self.root.withdraw()
                    self.root.update()      # let Tk actually send the hide
                    time.sleep(0.06)        # let the compositor erase it
            except tk.TclError:
                was_visible = False
        try:
            full = ImageGrab.grab(all_screens=True)     # physical, e.g. 2560x1600
        finally:
            if was_visible:
                self.root.deiconify()
                self.root.lift()
                self.root.attributes("-topmost", True)

        vx, vy = self.root.winfo_vrootx(), self.root.winfo_vrooty()
        vw, vh = self.root.winfo_vrootwidth(), self.root.winfo_vrootheight()
        self.scale = full.width / vw        # computed at runtime, never hardcoded
        disp = (full.resize((vw, vh), Image.LANCZOS)
                if (full.width, full.height) != (vw, vh) else full.copy())
        dark = ImageEnhance.Brightness(disp).enhance(DARK)

        self.shot_full, self.shot_disp = full, disp
        self.vx, self.vy = vx, vy

        # (2) The window. Note: no -alpha!
        ov = tk.Toplevel(self.root)
        ov.overrideredirect(True)
        ov.geometry(f"{vw}x{vh}+{vx}+{vy}")
        ov.attributes("-topmost", True)
        cv = tk.Canvas(ov, cursor="cross", highlightthickness=0, bd=0, bg="black")
        cv.pack(fill="both", expand=True)
        self.overlay, self.canvas = ov, cv

        # (3) Stacking order is creation order. Afterwards we only change the
        #     contents of these items, never rebuild them, so the order holds.
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

        # (4) Events
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<Button-3>", lambda e: self.cancel())      # right-click cancels

        # (5) Grab the mouse. Must wait until the window is viewable, otherwise
        #     TclError: grab failed: window not viewable
        ov.update_idletasks()
        self._take_grab()
        ov.focus_force()        # best effort; Esc does NOT rely on this
        self.state = "armed"

    def cancel(self):
        """Esc / right-click: close the selector and tell the owner to drop the image."""
        was_open = self.active
        self.close()
        if was_open and self.on_cancel:
            self.on_cancel()

    def close(self):
        """Just close the window. Does NOT fire on_cancel (the send path uses this)."""
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
        # release ~30MB of bitmaps
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
            pass                # not mapped yet; the implicit grab still covers this drag

    def _release_grab(self):
        if self.overlay is None:
            return
        try:
            self.overlay.grab_release()
        except tk.TclError:
            pass                # nothing was grabbed

    # ---------- mouse ----------

    def _on_press(self, e):
        if self.overlay is None:
            return
        # The grab is released after a selection (so the floating window can be
        # clicked and typed into); take it back when drawing a new box.
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
        self._on_drag(e)                    # settle the box at the final position
        box, self._origin = self._box, None
        w = box[2] - box[0] if box else 0
        h = box[3] - box[1] if box else 0

        if w < MIN_SIZE or h < MIN_SIZE:
            # Mis-click: don't close, don't lose the previous box (Esc is the
            # only way out)
            self._box = self._prev_box
            if self._box:
                self._paint_now()
                self.state = "selected"
                self._release_grab()
            else:
                self._hide_hole()
                self.state = "armed"        # grab still held; keep waiting
            return

        self._paint_now()                   # draw the final frame first: instant feedback
        self.state = "selected"
        self._release_grab()                # let go so the floating window can be used
        if self.on_selected:
            self.on_selected(self._build_shot(box))

    # ---------- drawing the bright hole ----------

    def _schedule_repaint(self):
        """Dirty flag + a single `after`: the mouse can report 100+ times a second,
        and repainting every time would avalanche."""
        if self._repaint_job is None and self.overlay is not None:
            self._repaint_job = self.overlay.after(REPAINT_MS, self._paint_now)

    def _paint_now(self):
        self._repaint_job = None
        if self.overlay is None or not self._box:
            return
        x0, y0, x1, y1 = self._box
        if x1 - x0 < 1 or y1 - y0 < 1:
            self._hide_hole()               # Tk cannot draw a zero-sized image
            return

        # Display crop uses Tk coordinates, cut from the already-resized shot_disp
        photo = ImageTk.PhotoImage(self.shot_disp.crop((x0, y0, x1, y1)))
        self.canvas.coords(self._hole, x0, y0)
        self.canvas.itemconfig(self._hole, image=photo, state="normal")
        # Order matters: point the canvas at the new image BEFORE dropping the old
        # reference. The other way round briefly points the canvas at a destroyed
        # Tk image, which flashes white.
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

    # ---------- coordinate conversion + encoding ----------

    def _to_physical(self, box):
        """Canvas coordinates -> physical pixels. The only place that multiplies
        by `scale` in this file."""
        x0, y0, x1, y1 = box
        s, vx, vy = self.scale, self.vx, self.vy
        return (round((vx + x0) * s), round((vy + y0) * s),
                round((vx + x1) * s), round((vy + y1) * s))

    def _build_shot(self, box):
        """Encoded synchronously on release, so send() only has to send."""
        pbox = self._to_physical(box)
        crop = self.shot_full.crop(pbox)    # physical original, not the resized copy
        buf = io.BytesIO()
        crop.save(buf, "PNG")
        if buf.tell() > MAX_PNG_BYTES:      # soft guard: shrink before encoding
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
