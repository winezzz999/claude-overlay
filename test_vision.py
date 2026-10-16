"""Quick test: verify the screenshot-question channel on its own (no tkinter at all)

Run: python test_vision.py [path/to/image.png]
Defaults to verify_screen.png in this directory.
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
        print(f"x image not found: {path.resolve()}")
        return
    raw = path.read_bytes()
    b64 = base64.standard_b64encode(raw).decode()
    print(f"image : {path.name}  {len(raw) / 1024:.0f} KB  "
          f"-> base64 {len(b64) / 1024:.0f} KB")

    v = VisionSession()
    print(f"model : {v.model}")
    print(f"url   : {v.base_url}")
    print(f"token : {'found' if v.token else 'NOT FOUND'}")
    print("-" * 50)

    t = time.perf_counter()
    first_at = None
    total = 0
    async for delta in v.ask_stream("Briefly describe what is in this image.", b64):
        if first_at is None:
            first_at = time.perf_counter() - t
        total += len(delta)
        print(delta, end="", flush=True)
    dt = time.perf_counter() - t
    print(f"\n{'-' * 50}")
    print(f"first token {first_at:.1f}s   {total} chars in {dt:.1f}s")
    print(f"streaming: {'ok' if v._stream_ok else 'unavailable -- fell back to non-streaming'}")


asyncio.run(main())
