# overlay — Claude 悬浮窗对话器

置顶半透明悬浮窗，窗口内直接与 Claude 对话（voice-claude 项目的一部分，当前独立开发）。

## 功能

- 窗口置顶、无边框、整体半透明
- 窗口内直接打字与 Claude 对话，回答流式显示（Markdown 渲染）
- 同一窗口内连续追问保持上下文
- 背景颜色 / 透明度可自定义，自动保存到 config.json

## 运行

```bash
pip install -r requirements.txt
python overlay.py
```

界面基于 tkinter（Python 自带），无需安装任何 GUI 框架。

## 文件

- overlay.py — 悬浮窗界面（tkinter：置顶/半透明/无边框/背景自定义）
- agent.py — 与 Claude Code 通信（claude-agent-sdk，流式）
- test_sdk.py — SDK 验证脚本（开发用）
- config.json — 背景设置（首次修改设置后自动生成）
