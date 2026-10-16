"""快速测试：验证 claude-agent-sdk 的登录状态和流式 API

运行：python test_sdk.py
"""
import asyncio
import json
import os
from claude_agent_sdk import query, ClaudeAgentOptions

# 从 Claude Code 的配置里读取环境变量（API地址/模型/令牌），原样传给SDK
settings = json.load(open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8"))
env = dict(settings.get("env") or {})


def describe_ev(ev):
    """把 StreamEvent 里的原始事件字典压缩成一行描述"""
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
            # 跳过 status / thinking_tokens 等噪音
        elif name == "StreamEvent":
            print(describe_ev(m.event or {}))
        elif name == "ResultMessage":
            print(f"final: {str(m.result)[:100]}")
            print(f"session_id: {m.session_id}")
        else:
            print(f"{name}: {str(m)[:100]}")
    print("done")


asyncio.run(main())
