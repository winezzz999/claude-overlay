"""临时测试：单独验证「截图提问」通道（完全不碰 tkinter）

运行：python test_vision.py [图片路径]
默认用目录里的 verify_screen.png。
"""
import asyncio
import base64
import sys
import time
from pathlib import Path

from vision import VisionSession


async def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "verify_screen.png")
    if not path.exists():
        print(f"✗ 找不到图片：{path.resolve()}")
        return
    raw = path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode()
    print(f"图片：{path.name}  {len(raw) / 1024:.0f} KB  →  base64 {len(b64) / 1024:.0f} KB")

    v = VisionSession()
    print(f"模型：{v.model}")
    print(f"地址：{v.base_url}")
    print(f"密钥：{'已找到' if v.token else '✗ 没找到'}")
    print("-" * 50)

    t = time.perf_counter()
    first_at = None
    total = 0
    async for delta in v.ask_stream("用中文简单描述这张图里有什么。", b64):
        if first_at is None:
            first_at = time.perf_counter() - t
        total += len(delta)
        print(delta, end="", flush=True)
    dt = time.perf_counter() - t
    print(f"\n{'-' * 50}")
    print(f"首字 {first_at:.1f}s   共 {total} 字 / {dt:.1f}s")
    print(f"流式：{'可用' if v._stream_ok else '不可用（已自动降级为非流式）'}")


asyncio.run(main())
