"""history.py — conversation history that survives a restart

One JSON file next to config.json. Each conversation stores two things:

  * the transcript blocks  — so you see the same text next time
  * the Claude session id  — so Claude remembers it too (resume on load)

Storage shape::

    {
      "current": "<conversation id>",
      "conversations": [
        {"id": ..., "title": ..., "session_id": ...,
         "messages": [{"tag": "user"|"bot"|"err"|"shot", "text": "..."}],
         "updated": "2026-10-17 19:30:00"}
      ]
    }

The text in `messages` is stored exactly as displayed (emoji prefix included),
so replaying it is a straight insert with the same tag.
"""
import json
import re
import time
import uuid
from pathlib import Path

FILE = Path(__file__).parent / "history.json"

MAX_CONVERSATIONS = 20      # oldest are dropped beyond this
MAX_MESSAGES = 400          # per conversation, oldest dropped
TITLE_LEN = 38

# "🙂 You: " / "🙂 You: [screenshot 800x600] " -> the actual question
_USER_PREFIX = re.compile(r"^[^\w]*You:\s*(\[screenshot \d+x\d+\]\s*)?")


class History:
    def __init__(self, path=FILE):
        self.path = path
        self.data = {"current": None, "conversations": []}
        self._load()
        if self.current() is None:
            self.new()

    # ---------- disk ----------

    def _load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("conversations"), list):
                self.data = raw
        except Exception:
            pass            # first run, or a corrupt file: start clean

    def save(self):
        conv = self.current()
        # Only derive a title once there is something to derive it from.
        # (new() saves immediately, so an empty conversation would otherwise be
        # labelled "(empty)" forever.)
        if conv is not None and not conv.get("title") and conv.get("messages"):
            conv["title"] = self._derive_title(conv)
        try:
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except Exception:
            pass            # never let a disk hiccup take down the UI

    # ---------- conversations ----------

    def conversations(self):
        """Newest first — that's the order the history list should show."""
        return sorted(self.data["conversations"],
                      key=lambda c: c.get("updated", ""), reverse=True)

    def current(self):
        cid = self.data.get("current")
        for c in self.data["conversations"]:
            if c.get("id") == cid:
                return c
        return None

    def new(self):
        """Start a fresh conversation and return it. The old one stays archived."""
        conv = {
            "id": time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:4],
            "title": "",
            "session_id": None,
            "messages": [],
            "updated": self._now(),
        }
        self.data["conversations"].append(conv)
        self.data["current"] = conv["id"]
        self._trim()
        self.save()
        return conv

    def select(self, cid):
        for c in self.data["conversations"]:
            if c.get("id") == cid:
                self.data["current"] = cid
                self.save()
                return c
        return None

    def delete(self, cid):
        """Remove one conversation. If it was the current one, start a new one."""
        self.data["conversations"] = [
            c for c in self.data["conversations"] if c.get("id") != cid]
        if self.data.get("current") == cid:
            self.data["current"] = None
            self.new()
        else:
            self.save()

    def add(self, tag, text):
        """Append one block and return it (the caller may keep mutating its text
        while a streamed answer is arriving)."""
        conv = self.current() or self.new()
        block = {"tag": tag, "text": text}
        conv["messages"].append(block)
        conv["updated"] = self._now()
        # keep the file bounded
        if len(conv["messages"]) > MAX_MESSAGES:
            del conv["messages"][:len(conv["messages"]) - MAX_MESSAGES]
        return block

    def set_session(self, session_id):
        conv = self.current()
        if conv is not None and session_id:
            conv["session_id"] = session_id

    # ---------- helpers ----------

    @staticmethod
    def _now():
        return time.strftime("%Y-%m-%d %H:%M:%S")

    def _derive_title(self, conv):
        for m in conv["messages"]:
            if m.get("tag") in ("user", "shot"):
                text = _USER_PREFIX.sub("", m.get("text", "")).strip()
                if text:
                    text = " ".join(text.split())
                    return text[:TITLE_LEN] + ("…" if len(text) > TITLE_LEN else "")
        return "(empty)"

    def _trim(self):
        convs = self.data["conversations"]
        if len(convs) <= MAX_CONVERSATIONS:
            return
        current = self.data.get("current")
        # drop the oldest, but never the one being used right now
        excess = len(convs) - MAX_CONVERSATIONS
        kept = []
        for c in convs:                 # insertion order == oldest first
            if excess > 0 and c.get("id") != current:
                excess -= 1
                continue
            kept.append(c)
        self.data["conversations"] = kept
