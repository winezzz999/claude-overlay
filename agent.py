"""agent.py — talking to Claude Code (streaming responses)"""
import json
import os
from claude_agent_sdk import query, ClaudeAgentOptions


def _load_env():
    """Read Claude Code's own configuration (API endpoint / model / token) so the
    overlay uses the same login and model as the Claude you use in your editor.

    The token only ever lives in settings.json — it is read at runtime here and
    never written into the source."""
    p = os.path.expanduser("~/.claude/settings.json")
    try:
        return dict(json.load(open(p, encoding="utf-8")).get("env") or {})
    except Exception:
        return {}


class ClaudeSession:
    """A Claude session that keeps its context across turns.

    Usage:
        session = ClaudeSession(cwd="...")
        async for delta in session.ask_stream("hello"):
            ...append delta to the UI...

    Calling ask_stream repeatedly on the same session preserves context, so it
    remembers what you talked about before.
    """

    def __init__(self, cwd):
        self._base = dict(
            cwd=cwd,
            env=_load_env(),
            include_partial_messages=True,   # streaming: receive per-token deltas
        )
        self.session_id = None   # None on the first call (new session); then resume

    async def ask_stream(self, prompt):
        """Send one message and yield the answer in pieces (a generator)."""
        options = ClaudeAgentOptions(resume=self.session_id, **self._base)
        async for msg in query(prompt=prompt, options=options):
            name = type(msg).__name__
            if name == "ResultMessage":
                # Answer finished: remember the session id so the next question
                # continues this same conversation
                self.session_id = msg.session_id
                return
            if name == "StreamEvent":
                # Streaming event: take only the text deltas, ignore thinking
                # and the other event kinds
                ev = msg.event or {}
                if ev.get("type") == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        yield d.get("text", "")
