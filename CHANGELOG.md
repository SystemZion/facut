# Changelog

## 0.6.0 — 2026-08-02

- Added `facut install`, `facut update`, user-PATH registration, atomic EXE
  replacement, resumable GitHub Release updates, and separately relocatable
  model storage.
- Added `facut models path/link/status`; existing Whisper/CosyVoice model
  directories can be reused without copying or living beside the EXE.
- Added automatic pip NVIDIA cuDNN/cuBLAS DLL discovery before CTranslate2 and
  faster-whisper imports; `doctor --json` now reports real ASR GPU usability.
- Added safe `render --fast-path auto|off|force` concat stream-copy assembly for
  unchanged, identical full-file sequential timelines.
- Added two-pass EBU R128 master normalization with `--loudness`, `--true-peak`,
  and `--lra`, plus external `--burn-subtitle` ASS/SRT/VTT rendering.
- Added persistent project render logs with full argument arrays, filter graphs,
  FFmpeg stderr, and bounded terminal diagnostics on failure.
- Added `timeline add --append`, sub-frame boundary snapping, one-frame source
  duration tolerance, and `import --porcelain` for Agent/shell workflows.
- Synchronized package and runtime version metadata at `0.6.0`.

All notable changes to `facut` are documented here.

## 0.5.3 — 2026-08-02

### Added

- `facut voice record <VOICE_ID>` opens a loopback-only browser studio with
  microphone selection, live level metering, prompts, playback, retakes and QC.
- Browser recordings are resampled to 48 kHz mono PCM16 before local import;
  no recording endpoint is exposed outside `127.0.0.1`.
- Agent capability discovery describes the interactive recording action while
  keeping it out of unattended JSON-RPC execution.

### Fixed

- Windows voice storage now resolves to `%LOCALAPPDATA%\facut\voices` instead
  of the former duplicated `facut\facut\voices` directory.
- Existing profiles are copied non-destructively on first use; legacy data is
  retained and existing canonical files are never overwritten.

### Verified

- 117 automated tests passed with the bundled FFmpeg/FFprobe; 3 legacy-PATH
  media tests were skipped by their own environment guards.
- Local HTTP import, invalid-audio rejection, token enforcement, CLI/schema
  discovery and a headed browser layout check all passed.

## 0.5.2 — 2026-08-02

### Added

- Consent-gated local digital voice profiles with multiple speakers and styles.
- Atomic profile JSON, private copied PCM WAV samples, SHA-256 deduplication,
  and recoverable delete/restore.
- Bounded-memory voice QC for duration, PCM format, channels, sample rate,
  clipping, RMS level and digital-silence ratio.
- Deterministic Mandarin recording prompt plans with 48 kHz mono guidance.
- CLI, Agent Schema and JSON-RPC actions for profile lifecycle, validation,
  recording plans and provider discovery.
- `facut-voice-provider/1.0` JSON-stdio protocol boundary for explicitly
  configured offline synthesis engines.

### Safety

- Full consent statements remain in the private local profile; public JSON
  returns only a consent-present flag and SHA-256 fingerprint.
- No provider means `NOT_IMPLEMENTED`; FACUT does not silently upload samples or
  claim that cloned speech was generated.

### Verified

- 113 automated tests passed with the bundled modern FFmpeg/FFprobe.
- Real 48 kHz mono PCM16 import and QC completed with zero clipping and a
  deterministic recording plan.

## 0.5.1 — 2026-08-02

### Added

- `facut narration suggest` produces evidence-grounded VLOG talking points,
  exact timeline ranges, confidence, provenance, and review-first draft text.
- Agent capability/schema and persistent JSON-RPC support for
  `narration.suggest`.
- QC delivery checks for pixel format, BT.709 metadata, VFR indicators, and
  audio channel layout.
- A VLOG-first roadmap covering common edit templates, local visual workers,
  and consent-gated personal-voice synthesis.

### Fixed

- Subject-aware x/y keyframes are now evaluated in a frame-capable crop node,
  so applied reframe plans render successfully.
- Final H.264 output is normalized to `yuv420p` after transitions, overlays,
  subtitles, and adjustment layers.
- Public JSON render errors retain a bounded FFmpeg diagnostic tail.
- External semantic observation IDs no longer collide with metadata IDs, and
  duplicate caller-supplied IDs are rejected.
- B-roll coverage diagnosis now consumes overlapping semantic `a-roll`
  observations.
- BT.709 delivery presets now block HDR/HLG/Log or BT.2020 source media instead
  of silently relabelling it without tone mapping.

### Verified

- RTX 5060 Ti NVENC acceptance: animated horizontal-to-vertical reframe,
  360x640, exact 3.000-second H.264/AAC output, `yuv420p`, BT.709.
- Automated suite: 104 tests when modern FFmpeg/FFprobe are supplied.

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
