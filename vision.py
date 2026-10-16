"""vision.py — 截图提问：一次性的「图 + 问题」调用

为什么不走 agent.py 的 ClaudeSession：
  那是 claude-agent-sdk（Claude Code 那套带工具、带会话的循环），
  而截图提问是一问一答、不带上下文、要把图片塞进消息里，
  用 anthropic SDK 直接打 Anthropic 兼容通道最简单 —— 和 capture_ask.py 同一条路。

通道是 DeepSeek 的 Anthropic 兼容代理，不是 api.anthropic.com：
  - 只发最小请求体，别带 thinking / system 这些 Claude 专有字段
  - 流式支持没有保证，所以要能退回非流式
  - 密钥从 ~/.claude/settings.json 的 env 里读，绝不写进代码（这仓库要 push）
"""
import os
import re

import anthropic

from agent import _load_env

DEFAULT_BASE_URL = "https://api.deepseek.com/anthropic"
DEFAULT_MODEL = "deepseek-flash"
MAX_TOKENS = 4096       # 和 capture_ask.py 一致；调大可能被通道拒
TIMEOUT = 120.0         # 代理卡死时别永远挂着


def _plain_model(name):
    """剥掉 Claude Code 的模型后缀语法：'deepseek-flash[1m]' → 'deepseek-flash'。

    [1m] 是 Claude Code 自己的「1M 上下文」标记，由它发请求前剥掉；
    原生 SDK 把带后缀的名字直接传过去，兼容通道多半会 404。
    """
    if not name:
        return DEFAULT_MODEL
    return re.sub(r"\[[^\]]*\]\s*$", "", name).strip() or DEFAULT_MODEL


def _pick(name, default, env):
    """优先级：环境变量 > settings.json 的 env > 默认值"""
    return os.environ.get(name) or env.get(name) or default


class VisionSession:
    """一次性的截图问答。接口形状和 agent.ClaudeSession 保持一致（异步生成器）。"""

    def __init__(self):
        env = _load_env()
        self.model = _plain_model(_pick("ANTHROPIC_MODEL", "", env))
        self.base_url = _pick("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL, env)
        self.token = (_pick("ANTHROPIC_AUTH_TOKEN", "", env)
                      or os.environ.get("DEEPSEEK_API_KEY"))
        self._client = None
        self._stream_ok = True      # 通道不吃流式时置 False，之后直接走非流式

    def _get_client(self):
        """懒创建：没有密钥时不要一启动就崩，等用户真提问了再报错。"""
        if self._client is None:
            if not self.token:
                raise RuntimeError(
                    "没找到 API 密钥：查一下 ~/.claude/settings.json 里的 "
                    "ANTHROPIC_AUTH_TOKEN，或者设环境变量 DEEPSEEK_API_KEY")
            # 用 auth_token 而不是 api_key：兼容通道走 Authorization: Bearer
            self._client = anthropic.AsyncAnthropic(
                auth_token=self.token, base_url=self.base_url, timeout=TIMEOUT)
        return self._client

    async def ask_stream(self, question, image_b64, media_type="image/png"):
        """异步生成器：逐段产出回答文本。"""
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
                # 只在「一个字都还没吐」且是 400/404（通道不认 stream）时降级。
                # 已经吐过字就不能重来，否则答案会重复；429/5xx 交给 SDK 自己重试
                if emitted or ex.status_code not in (400, 404):
                    raise
                self._stream_ok = False

        resp = await self._get_client().messages.create(
            model=self.model, max_tokens=MAX_TOKENS, messages=messages)
        # content 里可能有 thinking 块，只挑 text 块拼答案
        yield "".join(b.text for b in resp.content if b.type == "text")
