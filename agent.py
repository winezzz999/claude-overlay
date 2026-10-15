"""agent.py — 与 Claude Code 通信的封装（流式返回）"""
import json
import os
from claude_agent_sdk import query, ClaudeAgentOptions


def _load_env():
    """读取 Claude Code 自身的配置（API地址/模型/令牌），
    让副屏助手和你在 VS Code 插件里用的 Claude 走同一套登录与模型。
    令牌只存在于 settings.json 里，这里运行时读一下，绝不写进代码。"""
    p = os.path.expanduser("~/.claude/settings.json")
    try:
        return dict(json.load(open(p, encoding="utf-8")).get("env") or {})
    except Exception:
        return {}


class ClaudeSession:
    """一个可持续对话的 Claude 会话。

    用法：
        session = ClaudeSession(cwd="...")
        async for delta in session.ask_stream("你好"):
            ...把 delta 追加显示到界面...
    同一个 session 多次调用 ask_stream 会保持上下文（记得之前聊过什么）。
    """

    def __init__(self, cwd):
        self._base = dict(
            cwd=cwd,
            env=_load_env(),
            include_partial_messages=True,   # 开启流式：能收到逐字更新的增量
        )
        self.session_id = None   # 首次为 None（新会话），之后用 resume 续接

    async def ask_stream(self, prompt):
        """发送一条消息，逐段产出回答文本（生成器，一次 yield 一小段）。"""
        options = ClaudeAgentOptions(resume=self.session_id, **self._base)
        async for msg in query(prompt=prompt, options=options):
            name = type(msg).__name__
            if name == "ResultMessage":
                # 回答结束：记住会话ID，下次问话时续接这个会话
                self.session_id = msg.session_id
                return
            if name == "StreamEvent":
                # 流式事件：只取"文字增量"，忽略思考过程等其他事件
                ev = msg.event or {}
                if ev.get("type") == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        yield d.get("text", "")
