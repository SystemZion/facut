# Changelog

All notable changes to `facut` are documented here.

## 0.2.0 — 2026-07-31

### Added

- Independent audio tracks for background music and sound effects.
- Audio gain, fade-in, fade-out, delayed placement, and source looping.
- Mixing of independent audio tracks with the original camera audio.
- SRT and VTT import, export, timing shift, listing, and removal.
- Styled text overlays with position, color, outline, and background controls.
- Automatic ASS compilation and subtitle/text burn-in during preview and render.
- Encoding fallback for UTF-8, UTF-16, and GB18030 subtitle files.

### Changed

- Narrow-terminal CLI help tests now use a deterministic terminal width.
- The render graph accepts an optional generated subtitle file.
- Project Schema includes audio-loop, subtitle, and text-overlay data.

### Verified

- Real FFmpeg rendering with transitions, native audio, looped music, text, and
  subtitle burn-in.
- Windows, Linux, Python 3.11, and Python 3.13 remain covered by CI.

## 0.1.0 — 2026-07-28

- Initial installable CLI and Windows single-file executable.
- Project creation, media import and inspection, stable media IDs, and schema
  validation.
- Non-destructive timeline editing with split, trim, move, delete, ripple
  delete, undo, redo, and atomic JSON batches.
- Dissolve, fade-black, fade-white, slide, wipe, zoom, and blur transitions.
- Range preview and H.264/AAC final rendering through FFmpeg.
