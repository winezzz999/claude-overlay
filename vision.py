"""vision.py — screenshot questions: a one-shot "image + question" call

Why this does not go through agent.py's ClaudeSession:
  that one is claude-agent-sdk — Claude Code's tool-using, context-keeping
  loop. A screenshot question is one question, one answer, no context, with
  an image attached. Hitting the Anthropic-compatible endpoint directly with
  the anthropic SDK is the simplest thing that works.

The endpoint is DeepSeek's Anthropic-compatible proxy, NOT api.anthropic.com:
  - send the smallest possible request body; no thinking / system fields
  - streaming support is not guaranteed, so there has to be a fallback
  - the token is read at runtime from ~/.claude/settings.json and never
    written into the code (this repository gets pushed)
"""
import os
import re

import anthropic

from agent import _load_env

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"
MAX_TOKENS = 4096       # matches capture_ask.py; raising it may be rejected
TIMEOUT = 120.0         # so a wedged proxy doesn't hang forever


def _plain_model(name):
    """Strip Claude Code's model-suffix syntax: 'deepseek-flash[1m]' → 'deepseek-flash'.

    `[1m]` is Claude Code's own "1M context" marker, which it strips before
    sending the request. Passing a name with the suffix straight to the raw SDK
    will most likely 404 on a compatible proxy.
    """
    if not name:
        return DEFAULT_MODEL
    return re.sub(r"\[[^\]]*\]\s*$", "", name).strip() or DEFAULT_MODEL


def _pick(name, default, env):
    """Precedence: environment variable > settings.json env > default"""
    return os.environ.get(name) or env.get(name) or default


class VisionSession:
    """One-shot screenshot Q&A. Same shape as agent.ClaudeSession (async generator)."""

    def __init__(self):
        env = _load_env()
        self.model = _plain_model(_pick("ANTHROPIC_MODEL", "", env))
        self.base_url = _pick("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL, env)
        self.token = (_pick("ANTHROPIC_AUTH_TOKEN", "", env)
                      or os.environ.get("DEEPSEEK_API_KEY"))
        self._client = None
        self._stream_ok = True      # set False if the proxy rejects streaming

    def _get_client(self):
        """Built lazily: don't crash at startup without a token — complain when
        the user actually asks something."""
        if self._client is None:
            if not self.token:
                raise RuntimeError(
                    "No API token found. Check ANTHROPIC_AUTH_TOKEN in "
                    "~/.claude/settings.json, or set the DEEPSEEK_API_KEY "
                    "environment variable.")
            # auth_token rather than api_key: the compatible proxy uses
            # Authorization: Bearer
            self._client = anthropic.AsyncAnthropic(
                auth_token=self.token, base_url=self.base_url, timeout=TIMEOUT)
        return self._client

    async def ask_stream(self, question, image_b64, media_type="image/png"):
        """Async generator: yields the answer in pieces."""
        messages = [{"role": "user", "content": [
            {"type": "image", "source": {
                "type": "base64", "media_type": media_type, "data": image_b64}},
            {"type": "text", "text": question},
        ]}]

        if self._stream_ok:
            emitted = False
            try:
                async with self._get_client().messages.stream(
                        model=self.model, max_tokens=MAX_TOKENS,
                        messages=messages) as stream:
                    async for text in stream.text_stream:
                        emitted = True
                        yield text
                return
            except anthropic.APIStatusError as ex:
                # Only fall back when nothing has been emitted yet AND the proxy
                # said 400/404 (the usual "I don't do stream=true" signals).
                # Retrying after text was already sent would duplicate the
                # answer; 429/5xx are left to the SDK's own retries.
                if emitted or ex.status_code not in (400, 404):
                    raise
                self._stream_ok = False

        resp = await self._get_client().messages.create(
            model=self.model, max_tokens=MAX_TOKENS, messages=messages)
        # content may contain thinking blocks; keep only the text ones
        yield "".join(b.text for b in resp.content if b.type == "text")
