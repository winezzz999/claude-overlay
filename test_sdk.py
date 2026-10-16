"""Quick test: verify claude-agent-sdk login state and streaming API

Run: python test_sdk.py
"""
import asyncio
import json
import os
from claude_agent_sdk import query, ClaudeAgentOptions

# Read the environment (API endpoint / model / token) from Claude Code's own
# config and pass it to the SDK unchanged
settings = json.load(open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8"))
env = dict(settings.get("env") or {})


def describe_ev(ev):
    """Compress the raw event dict inside a StreamEvent into one line"""
    t = ev.get("type")
    if t == "stream_event":
        inner = ev.get("event", {})
        it = inner.get("type")
        if it == "content_block_delta":
            d = inner.get("delta", {})
            if d.get("type") == "text_delta":
                return f"text_delta: {d.get('text','')!r}"
            return f"delta[{d.get('type')}]"
        return f"stream[{it}]"
    if t == "assistant":
        return f"assistant: {str(ev.get('message', {}).get('content'))[:80]}"
    return f"event[{t}]"


async def main():
    print("starting SDK test ...")
    async for m in query(
        prompt="Reply with exactly two words: hello there",
        options=ClaudeAgentOptions(env=env, include_partial_messages=True),
    ):
        name = type(m).__name__
        if name == "SystemMessage":
            if m.subtype == "init":
                print(f"init: cwd={m.data.get('cwd')} model={m.data.get('model')}")
            elif m.subtype == "result":
                print(f"result: {str(m.data)[:100]}")
            # skip noise like status / thinking_tokens
        elif name == "StreamEvent":
            print(describe_ev(m.event or {}))
        elif name == "ResultMessage":
            print(f"final: {str(m.result)[:100]}")
            print(f"session_id: {m.session_id}")
        else:
            print(f"{name}: {str(m)[:100]}")
    print("done")


asyncio.run(main())
