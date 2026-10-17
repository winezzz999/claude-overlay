"""overlay.py — an always-on-top translucent floating window (tkinter)

Run: python overlay.py
Features: always on top, translucent, borderless, grey background
          (colour and opacity are configurable and saved to config.json)

Two ways to ask:
  type + Enter      -> goes through agent.py's Claude session: has context,
                       can use tools
  · key to select   -> the screenshot goes out with your question (a separate
                       one-shot call, see vision.py)
                       To change the hotkey, edit HOTKEY in capture.py.
"""
import asyncio
import ctypes
import json
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from pynput import keyboard

from agent import ClaudeSession
from capture import HOTKEY, CaptureOverlay
from history import History
from vision import VisionSession


def _allow_foreground():
    """Windows only lets the foreground process steal keyboard focus.

    The hotkey is pressed while some other program is in the foreground, so at
    that moment this process is not it, and SetForegroundWindow gets refused
    (the taskbar button just flashes) — which shows up as "I selected a region
    but typing does nothing". This lifts the restriction once and for all, and
    must be called before the first attempt to steal focus.
    """
    try:
        ctypes.windll.user32.AllowSetForegroundWindow(-1)   # ASFW_ANY
    except Exception:
        pass

CONFIG_FILE = Path(__file__).parent / "config.json"
DEFAULTS = {"bg": "#2e2e2e", "opacity": 0.92}
PRESET_COLORS = ["#2e2e2e", "#1e1e1e", "#23272e", "#3d3d3d"]   # grey/dark/slate/light
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
        _allow_foreground()   # must happen before the first focus grab

        self.cfg = load_config()
        self.session = ClaudeSession(cwd=str(Path(__file__).parent))
        self.sending = False
        self._paint = []   # every widget that follows the background colour

        self.root = tk.Tk()
        self.root.overrideredirect(True)                    # borderless (close button is in the title bar)
        self.root.attributes("-topmost", True)              # always on top
        self.root.attributes("-alpha", self.cfg["opacity"]) # whole-window translucency
        self.root.geometry("430x580+120+120")
        self._paint.append(self.root)

        # --- the screenshot-question side ---
        self.vision = VisionSession()
        self.pending_shot = None    # screenshot waiting to be sent; None = none.
                                    # Consumed by the next send().
        self.capture = CaptureOverlay(   # constructing it doesn't show anything;
            self.root, on_selected=self._on_region_selected,   # open() does
            on_cancel=self._on_capture_cancel)
        self._events = queue.Queue()     # pynput thread -> Tk thread
        self._esc_bound = False          # starts False so the first _sync_esc() binds

        # --- conversation history (survives restarts) ---
        self.history = History()
        self._reply_block = None    # the bot block a streamed answer is filling in

        # The asyncio loop runs on a background thread: streamed answers from the
        # SDK must not block the UI
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        self._build_ui()
        self._restore_conversation()
        self._refresh_tabs()        # populate the tab strip with what we restored
        self._start_hotkeys()
        self.root.after(30, self._drain_events)   # drain signals from the pynput thread
        self.root.mainloop()

    # ---------- UI ----------
    def _build_ui(self):
        fg = "#e8e8e8"

        # Title bar (hold it to drag the window)
        title = tk.Frame(self.root, bg="#3a3a3a")
        title.pack(fill="x")
        label = tk.Label(title, text="Claude", bg="#3a3a3a", fg=fg,
                         font=("Microsoft YaHei", 10, "bold"))
        label.pack(side="left", padx=10, pady=5)
        tk.Button(title, text="✕", bg="#3a3a3a", fg=fg, bd=0, font=FONT,
                  command=self._quit).pack(side="right", padx=8)
        tk.Button(title, text="⚙", bg="#3a3a3a", fg=fg, bd=0, font=FONT,
                  command=self.toggle_settings).pack(side="right", padx=2)
        for w in (title, label) + tuple(title.winfo_children()):
            w.bind("<Button-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

        # Settings panel (collapsed by default)
        self.settings = tk.Frame(self.root, bg=self.cfg["bg"])
        self._paint.append(self.settings)
        tk.Label(self.settings, text="Background", bg=self.cfg["bg"], fg=fg,
                 font=FONT).pack(anchor="w")
        row = tk.Frame(self.settings, bg=self.cfg["bg"])
        self._paint.append(row)
        row.pack(anchor="w", pady=2)
        for c in PRESET_COLORS:
            tk.Button(row, bg=c, width=3, relief="flat", bd=1, cursor="hand2",
                      command=lambda col=c: self.set_bg(col)).pack(side="left", padx=2)
        tk.Label(self.settings, text="Opacity", bg=self.cfg["bg"], fg=fg,
                 font=FONT).pack(anchor="w")
        self.opacity_scale = tk.Scale(self.settings, from_=0.3, to=1.0,
                                      resolution=0.01, orient="horizontal",
                                      bg=self.cfg["bg"], fg=fg, highlightthickness=0,
                                      command=self.set_opacity)
        self._paint.append(self.opacity_scale)
        self.opacity_scale.set(self.cfg["opacity"])
        self.opacity_scale.pack(fill="x")

        # Conversation tabs, browser-style: one tab per conversation, the
        # pinned + at the end starts a new one. Tabs live in a scrollable
        # canvas so twenty of them don't overflow the window.
        self.tab_holder = tk.Frame(self.root, bg="#2c2c2c")
        self._paint.append(self.tab_holder)
        self.tab_canvas = tk.Canvas(self.tab_holder, height=30, bg="#2c2c2c",
                                    highlightthickness=0, bd=0)
        self.tab_canvas.pack(side="left", fill="x", expand=True)
        self.tab_frame = tk.Frame(self.tab_canvas, bg="#2c2c2c")
        self.tab_window = self.tab_canvas.create_window(
            (0, 0), window=self.tab_frame, anchor="nw")
        self.tab_frame.bind("<Configure>", lambda e: self.tab_canvas.configure(
            scrollregion=self.tab_canvas.bbox("all")))
        self.tab_canvas.bind("<MouseWheel>", self._tab_scroll)  # wheel scrolls tabs
        tk.Button(self.tab_holder, text="+", bg="#2c2c2c", fg=fg, bd=0,
                  font=FONT, cursor="hand2",
                  command=self._new_chat).pack(side="left", padx=4)
        self.tab_holder.pack(fill="x")

        # Transcript: Text widget tags stand in for bubbles (you = blue, Claude = grey)
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

        # Input area
        bottom = tk.Frame(self.root, bg=self.cfg["bg"])
        self._paint.append(bottom)
        bottom.pack(fill="x", pady=6, padx=8)
        self.input = tk.Text(bottom, height=3, bg="#3a3a3a", fg=fg, wrap="word",
                             font=FONT, insertbackground=fg)
        self.input.pack(side="left", fill="x", expand=True)
        self.send_btn = tk.Button(bottom, text="Send", bg="#3d7eff", fg="white",
                                  font=FONT, command=self.send, padx=12)
        self.send_btn.pack(side="left", padx=(6, 0))
        self.input.bind("<Return>", self.send)               # Enter sends
        self.input.bind("<Shift-Return>", lambda e: None)    # Shift+Enter newline
        self._sync_esc()                                     # see that method for why

    # ---------- window dragging ----------
    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    # ---------- screenshot questions ----------
    def _start_hotkeys(self):
        # pynput's callbacks run on their own thread; they only drop a signal on
        # the queue and never touch Tk directly
        self._hotkey_listener = keyboard.GlobalHotKeys(
            {HOTKEY: lambda: self._events.put("HOTKEY")})
        self._hotkey_listener.start()
        self._esc_listener = keyboard.Listener(on_press=self._on_esc_press)
        self._esc_listener.start()

    def _on_esc_press(self, key):
        if key == keyboard.Key.esc:
            self._events.put("ESC")

    def _drain_events(self):
        """Every 30ms, run the queued signals on the Tk thread (Tk is not thread-safe)"""
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
            return              # waiting on an answer / selector already open -> ignore
        try:
            self.capture.open()
        except Exception as ex:
            self._append_block(f"\n⚠️ Screenshot failed: {type(ex).__name__}: {ex}\n", "err")
        self._sync_esc()

    def _on_region_selected(self, shot):
        self.pending_shot = shot        # handed to the next send()
        self._append_block(
            f"📷 Selected {shot['w']}x{shot['h']} — type your question, "
            f"then press Enter  (Esc cancels)\n", "shot")
        self._raise_chat_window()       # stage 2: raise the window + take focus

    def _on_capture_cancel(self):
        self.pending_shot = None
        self._sync_esc()

    def cancel_capture(self):
        """Idempotent: the global listener and the widget binding can each fire
        once for the same Esc"""
        self.pending_shot = None
        if self.capture.active:
            self.capture.cancel()
        self._sync_esc()

    def _sync_esc(self):
        """While the selector is open, unbind the window's Esc so the global
        listener owns it exclusively.

        Otherwise a single keypress is handled twice: the global listener closes
        the selector, and then the Tk binding sees "no capture in progress" and
        quits the whole program.
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
        """Only reachable when no capture is in progress (Esc is unbound during one)"""
        self._quit()

    def _raise_chat_window(self):
        """Stage 2: raise the floating window above the selector and take keyboard focus.

        lift() alone only fixes the Z-order — an overrideredirect window has to
        fight for keyboard focus via SetForegroundWindow, which relies on
        _allow_foreground() having been called at startup.
        """
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", False)   # must drop it to be raised
            self.root.lift()
            self.root.attributes("-topmost", True)    # nail it back on
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

    # ---------- conversation history ----------
    def _restore_conversation(self):
        """At startup, bring back the last conversation: replay the text and
        resume Claude's session so it remembers it too."""
        conv = self.history.current()
        if conv is None:
            return
        self.session.session_id = conv.get("session_id")
        self._replay(conv)

    def _replay(self, conv):
        self.chat.config(state="normal")
        self.chat.delete("1.0", "end")
        for m in conv.get("messages", []):
            self.chat.insert("end", m.get("text", ""), m.get("tag", "bot"))
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _new_chat(self):
        """Archive the current conversation and start a blank one."""
        if self.sending:
            return              # don't pull the conversation out from under a stream
        self.history.save()     # derives the title of the one being archived
        self.history.new()
        self.session.session_id = None      # Claude starts fresh as well
        self._reply_block = None
        self.pending_shot = None
        if self.capture.active:
            self.capture.close()
        self._sync_esc()
        self._replay({"messages": []})
        self._refresh_tabs()

    def _tab_scroll(self, event):
        """Mouse wheel over the tab strip scrolls it horizontally."""
        self.tab_canvas.xview_scroll(-1 * int(event.delta / 120), "units")

    def _refresh_tabs(self):
        """Rebuild the tab strip to mirror the conversation list."""
        for w in self.tab_frame.winfo_children():
            w.destroy()
        current = self.history.current()
        cid = current["id"] if current else None
        # oldest first, newest ends up next to the + button (browser order)
        for conv in reversed(self.history.conversations()):
            self._make_tab(conv, conv["id"] == cid)

    def _make_tab(self, conv, is_current):
        bg = "#3d7eff" if is_current else "#2e2e2e"
        fg = "#ffffff" if is_current else "#e8e8e8"
        tab = tk.Frame(self.tab_frame, bg=bg)
        label = tk.Label(tab, text=(conv.get("title") or "(empty)")[:16],
                         bg=bg, fg=fg, font=("Microsoft YaHei", 9))
        label.pack(side="left", padx=(8, 2), pady=3)
        tk.Button(tab, text="✕", bg=bg, fg=fg, bd=0, relief="flat",
                  font=("Microsoft YaHei", 8), cursor="hand2",
                  activebackground=bg, activeforeground=fg,
                  command=lambda c=conv["id"]: self._close_tab(c)
                  ).pack(side="left", padx=(0, 6), pady=3)
        for w in (tab, label):          # clicking the ✕ must NOT switch
            w.bind("<Button-1>", lambda e, c=conv["id"]: self._open_tab(c))
            w.configure(cursor="hand2")
        tab.pack(side="left", padx=(3, 0), pady=3)

    def _open_tab(self, cid):
        """Switch to another conversation."""
        current = self.history.current()
        if self.sending or (current and current["id"] == cid):
            return
        self.history.save()
        opened = self.history.select(cid)
        if opened is None:
            return
        self.session.session_id = opened.get("session_id")
        self._reply_block = None
        self.pending_shot = None
        if self.capture.active:
            self.capture.close()
        self._sync_esc()
        self._replay(opened)
        self._refresh_tabs()

    def _close_tab(self, cid):
        """Close (delete) a conversation, with confirmation."""
        if self.sending:
            return
        conv = next((c for c in self.history.conversations() if c["id"] == cid), None)
        if conv is None:
            return
        name = conv.get("title") or "(empty)"
        if not messagebox.askyesno(
                "Close conversation",
                f"Close “{name}”?\n\nThe transcript is deleted and cannot be undone.",
                parent=self.root):
            return
        was_current = self.history.current() is not None \
            and self.history.current()["id"] == cid
        # if it was the current one, delete() starts a fresh conversation
        self.history.delete(cid)
        self.session.session_id = (self.history.current() or {}).get("session_id")
        self._reply_block = None
        if was_current:
            self.pending_shot = None
            self._replay(self.history.current() or {"messages": []})
        self._refresh_tabs()

    # ---------- background customisation ----------
    def toggle_settings(self):
        if self.settings.winfo_manager():
            self.settings.pack_forget()                 # collapse
        else:
            self.settings.pack(fill="x", before=self.chat)  # expand above the transcript

    def set_bg(self, color):
        self.cfg["bg"] = color
        for w in self._paint:
            w.configure(bg=color)
        save_config(self.cfg)

    def set_opacity(self, value):
        self.cfg["opacity"] = round(float(value), 2)
        self.root.attributes("-alpha", self.cfg["opacity"])
        save_config(self.cfg)

    # ---------- chat ----------
    def send(self, event=None):
        """Enter, or the Send button. Attaches a pending screenshot if there is one,
        otherwise it's a plain conversation turn."""
        if self.sending:
            return "break"
        text = self.input.get("1.0", "end").strip()
        if not text:
            return "break"

        shot = self.pending_shot
        self.pending_shot = None       # a screenshot is sent once, success or not
        self.input.delete("1.0", "end")

        if shot is not None:
            self._append_block(
                f"🙂 You: [screenshot {shot['w']}x{shot['h']}] {text}\n", "user")
            self.capture.close()       # sent = stage 2 over, the overlay goes away
        else:
            if self.capture.active:    # plain text while selecting -> drop the box
                self.capture.close()
            self._append_block(f"🙂 You: {text}\n", "user")
        self._sync_esc()

        # The reply block is created up front so the streamed chunks can grow it,
        # and so a restart can replay exactly what was on screen
        self._reply_block = self.history.add("bot", "🤖 Claude: ")
        self._insert_text("🤖 Claude: ", "bot")
        self.sending = True
        self.send_btn.config(state="disabled", text="Waiting…")
        # Hand the async task to the background thread's event loop; never block the UI
        coro = self._run_vision(text, shot) if shot else self._run(text)
        asyncio.run_coroutine_threadsafe(coro, self.loop)
        return "break"   # stop Enter from inserting a newline in the input box

    def _insert_text(self, s, tag):
        self.chat.config(state="normal")
        self.chat.insert("end", s, tag)
        self.chat.config(state="disabled")
        self.chat.see("end")

    def _append_block(self, s, tag):
        """Append a finished block: show it, and record it in the history."""
        self.history.add(tag, s)
        self._insert_text(s, tag)

    async def _run(self, prompt):
        """Runs on the background thread's event loop: receive the answer in pieces"""
        try:
            async for delta in self.session.ask_stream(prompt):
                # tkinter's after() schedules a UI update safely across threads
                self.root.after(0, self._append, delta)
        except Exception as ex:
            self.root.after(0, self._append_block, f"\n⚠️ Error: {ex}\n", "err")
        finally:
            self.root.after(0, self._finish)

    def _append(self, delta):
        """A streamed chunk: grow the current reply block and show it."""
        if self._reply_block is not None:
            self._reply_block["text"] += delta
        self._insert_text(delta, "bot")

    async def _run_vision(self, prompt, shot):
        """Screenshot question: same shape as _run, but via the image channel (vision.py)"""
        try:
            async for delta in self.vision.ask_stream(
                    prompt, shot["b64"], shot["media_type"]):
                self.root.after(0, self._append, delta)
        except Exception as ex:
            self.root.after(0, self._append_block,
                            f"\n⚠️ Screenshot question failed: "
                            f"{type(ex).__name__}: {ex}\n", "err")
        finally:
            self.root.after(0, self._finish)

    def _finish(self):
        self.sending = False
        self.send_btn.config(state="normal", text="Send")
        if self._reply_block is not None:
            self._reply_block["text"] += "\n"
        self._insert_text("\n", "bot")
        self._reply_block = None
        # Remember Claude's session id so a restart can carry on where it left off
        self.history.set_session(self.session.session_id)
        self.history.save()
        self._refresh_tabs()        # the title may have just been derived


if __name__ == "__main__":
    FloatingChat()
