# Changelog

All notable changes to `facut` are documented here.

## 0.3.0 — 2026-08-02

### Added

- Native proxy create/link/relink/status workflow with preview/original switching.
- Safe clip-hash incremental rendering cache and exact timeline-end muxing.
- Transform, crop, fit, rotation metadata control, stabilization, opacity,
  freeze-frame insertion, linear transform keyframes, PIP, masks, blend modes,
  clip effects, and timed adjustment layers.
- Native and independent audio gain, mute, fades, high-pass, denoise,
  compressor, limiter, loudness normalization, channel repair, pan, and
  crossfades.
- `facut qc` full-decode, black, silence, loudness, Markdown, JSON, and contact
  sheet reports.
- Quality, scene, beat, optional local ASR, and song-fingerprint analysis
  interfaces.
- Markers with labels, categories, ratings, recommended ranges, CSV, and XLSX.
- Named sequences, batch sequence rendering, title templates, CSV title import,
  and safe-area text styling.
- Persistent newline JSON-RPC `facut serve` mode.
- JSONL render progress with stage, percent, speed, FPS, ETA, and current clip.

### Fixed

- Added `facut help` and the documented `clip duplicate` command.
- Doctor now reports absolute tool paths and separately measures detected,
  usable, and implemented hardware encoders.
- Rendering now trims both streams to the exact timeline duration.

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
