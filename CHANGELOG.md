# Changelog

English · [中文](CHANGELOG.zh-CN.md)

Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/) —
`MAJOR.MINOR.PATCH`, where a leading `0` means the interface may still change.

## [0.1.0] - 2026-10-16

First working version: floating window + region-select screenshot questions.

### Added

- Always-on-top, borderless, translucent tkinter window; background colour and
  opacity are configurable and persisted to `config.json`
- Chat by typing in the window — streamed answers, with context kept across turns
- Press <kbd>·</kbd> (the key left of <kbd>1</kbd>) to select a screen region:
  **the whole screen dims while your selection stays at full brightness**
- Type your question in the floating window after selecting; the screenshot goes
  out alongside it at **full physical resolution**
- Drag a new box after releasing the mouse — nothing is locked in until you send
- <kbd>Esc</kbd> or right-click cancels the flow at any point

### Notes

- Screenshot questions go through a separate one-shot request (`vision.py`) and
  are **not** merged into the text conversation history
- At 125% display scaling, Tk's coordinate space and the actual screenshot pixels
  are not the same thing. The ratio is computed at runtime
  (`screenshot width / tk screen width`) rather than hardcoded, so changing
  displays or scaling needs no code changes
- Credentials are read at runtime from `~/.claude/settings.json` — the repository
  contains none

### Known limitations

- If the selection covers the floating window itself, the window ends up in the
  screenshot
- The first token of a screenshot answer takes 5–7 seconds (the image is large;
  the model has to read it first)
- Voice input is not wired up yet

## Roadmap

See the [roadmap in the README](README.md#roadmap) — kept in one place so the two
don't drift apart.

[0.1.0]: https://github.com/winezzz999/claude-overlay/releases/tag/v0.1.0
