# Changelog

## 1.0.0 — 2026-08-27

- Added Director Studio Review Room on loopback-only random ports with token
  authentication, A/B playback, timeline, StoryGraph, waveform, evidence and
  timecode-feedback panels. Large media is streamed with HTTP byte ranges.
- Added evidence-bound `timeline_patch.v1` planning, semantic dry-run diff,
  protected speech/event gates and one-commit CutGraph application. Natural
  language feedback remains unapplied until an external Agent returns a valid
  patch.
- Added Music Library v2 with non-destructive JSON-to-SQLite migration, FTS,
  content hashes, beats, loudness, technical energy/section candidates, fixed
  semantic dimensions, license evidence and platform hard filters.
- Added review-first music search, audition, cue planning and approved-loop
  duration coverage. Unreviewed loop candidates and unverified licenses cannot
  satisfy delivery gates.
- Added visible-browser source sessions for Pixabay music and YouTube Audio
  Library. Downloads require captured license evidence; YouTube-only rights are
  not inferred to cover other platforms.
- Added explicit `taste show/remember/forget/export`; project feedback never
  becomes a global preference unless the user deliberately remembers it.
- Added Director Studio action schemas, Recipe Timeline Patch support, JSON-RPC
  parity, paged music results, macOS CI, and version-aligned C++ native builds.

## 0.9.3 — 2026-08-26

- Added clear top-level `scan`, `cut`, `resume`, `next`, `say`, `check`,
  `explain`, and `defaults` commands while preserving every canonical command.
- Added shortcut-to-canonical metadata to structured responses and Agent
  capability discovery without changing canonical response payloads.
- Added adaptive Scene Atlas sampling with a configurable maximum blind gap.
- Added Trip Bible ambiguity/evidence auditing and safe entity rename with
  dependent-plan invalidation.
- Added optional original-source EBU R128 measurements to Soundscape analysis
  and explicit quality-gate readiness in VLOG status.
- Added render-cache size and SHA-256 integrity checks so empty or corrupted
  segment artifacts cannot be reused.

## 0.9.2 — 2026-08-23

- Added hard speech-span and atomic-event cut protection with an explicit reviewed override.
- Made LRF/proxy files video-only for preview; camera audio always comes from original media.
- Added speaker identity guards for existing profiles and recoverable per-sample removal.
- Isolated TTS requests by request ID and restart desynchronized warm providers after timeout.
- Added optional ASR round-trip narration verification for entities and factual numbers.
- Added build commit/hash identity, PATH shadowing diagnostics, and a strict GitHub Release gate.
- Restored structured JSON errors for malformed and unknown CLI commands.

## 0.9.1 — 2026-08-22

- Fixed fresh external-director proposals so they bind the current project,
  evidence and Trip Bible and can be refined without first generating a legacy
  deterministic plan.
- Rejected stale evidence/project plans, invalid media ranges, timeline overlap,
  reversed event chains, mixed subjects and incident-only stories when matching
  recovery evidence exists.
- Kept chapter/location text and subtitle layers identical between candidate
  preview and the applied final timeline, with a plan digest for safe retries.
- Made Director Review creation idempotent until submission, counted only
  submitted rounds, embedded review evidence references and rejected unknown
  citations.
- Converted decoded frames to a real gray8 analysis plane before native quality
  metrics and perceptual hashing, fixing 10/12/16-bit HEVC interpretation.
- Removed stale single-file executables when producing the preferred onedir
  Windows build so the output directory cannot silently offer an older FACUT.

## 0.9.0 — 2026-08-22

- Added Scene Atlas with stable 12-asset review batches, content-hash resume,
  partial idempotent submissions, explicit exclusions and adaptive deep review.
- Added StoryGraph 3 external-director briefs and proposals with episodes,
  events, causal constraints, visual motifs and a clearly labeled deterministic
  fallback.
- Added Opening Lab, Ending Lab and Continuity Guard for location, daypart,
  motion, composition, B-roll, event-outcome and hard-stop checks.
- Added a three-round evidence-bound Director Review Loop. Approved commands
  apply as one atomic CutGraph revision and stale plans are rejected.
- Added review-first Soundscape role inventory, ducking/mastering plans and
  atomic approved settings without claiming unmeasured audio results.
- Registered executable VLOG transitions and restrained effects; unsupported
  anchor-dependent polish remains explicitly review-required.
- Exposed the new workflow through CLI, Agent schemas, Recipes and loopback
  JSON-RPC while keeping all visual interpretation external.

## 0.8.3 — 2026-08-22

- Updated Windows install and self-update to carry the complete portable ZIP,
  including `facut_runtime`, with safe extraction, staged directory switching
  and rollback; legacy single-file releases remain supported.
- Bound StoryGraph and narration plans to a semantic Trip Bible digest so a
  changed or rejected fact invalidates stale plans before timeline mutation.
- Bound resolved Director Inbox items to source fingerprints and made status
  reconstruction non-persistent, preventing stale decisions and query writes.
- Reclaimed abandoned background-warmup locks and made `cleanram` verify that
  the voice process actually stopped instead of reporting success on timeout.
- Corrected the lightweight `cleanram` and `warmup` help syntax.

## 0.8.2 — 2026-08-19

- Added the optional C++20 `facut-native` sidecar for persistent batch probing,
  targeted representative-frame extraction, cached waveforms, lightweight
  quality metrics and media fingerprints.
- Added `native doctor`, `native benchmark` and `analyze batch`, plus native
  acceleration in `vlog prepare` with explicit `auto/native/python` selection.
- Kept the versioned JSONL boundary process-safe: one native crash is restarted
  once, then `auto` reports the failure and uses the Python/FFmpeg path.
- Added an allowlisted Windows x64 bundle and installer support for the native
  executable, replaceable LGPL FFmpeg DLLs and third-party notices. Models,
  user media, voice data and fonts remain outside the application package.
- Kept the Python project engine, StoryGraph, Agent contract and final rendering
  authoritative; the native layer is optional acceleration, not a second editor.
- Added continuous `analyze batch --jsonl-progress` events with current media,
  completion rate and ETA; per-asset timeouts and failures remain isolated.
- Added the public `daily-chat` delivery across CLI, Agent schema, JSON-RPC and
  CosyVoice reference selection.
- Added a quarantine/review/approve workflow for audio found in source videos.
  Similarity alone never makes a recording synthesis-eligible; explicit
  same-speaker confirmation is required and raw files remain unchanged.
- Voice validation now separates file quality, synthesis usability and optional
  style coverage instead of treating a fixed ten-minute target as one verdict.
- Added default non-blocking background warmup for registered heavy services,
  with per-service `autoload`, `warmup`, and explicit `cleanram --service`
  controls. RAM cleanup never removes models, media caches, or recordings.
- Added a lightweight console bootstrap for version and runtime lifecycle
  commands; background warmup failure never blocks the requested edit command.
- Exposed runtime status, warmup, selective cleanup and autoload configuration
  to the Agent schema and JSON-RPC session.

## 0.8.1 — 2026-08-18

- Registered atomic `vlog.apply` across Agent schema, batch, Recipe, and JSON-RPC surfaces.
- Hardware `auto` now initializes listed encoders and skips unusable NVENC/AMF devices before selecting QSV or software.
- Preserved text-template typography with `explicit option > template > system default` precedence.
- Added same-subject event-chain evidence and deterministic incident/recovery ordering with review-required failures.
- Recorded built/delivered VLOG outputs and QC state for accurate `vlog status` responses.
- Extended incremental rendering to cache unchanged clips and connected transition units instead of always falling back to a full render.
- Forced non-preset renders to the project's audio sample rate, preventing 48 kHz projects from inheriting 96 kHz camera audio.
- Rendered timeline-anchored fade-in/out transitions on the completed picture and cached terminal fades with only their affected segment.
- Replaced long independent-track delays with silence-prefix concatenation so music and narration survive still-image epilogues.
- Added `--no-project-subtitles` for clean-master delivery without modifying editable subtitle or title tracks.
- Classified short terminal fade silence separately from abnormal QC silence, while retaining the measured interval in reports.
- Skipped NVENC initialization entirely in `auto` mode when no NVIDIA device is reported, allowing direct QSV selection.

## 0.8.0 — 2026-08-17

### Vlog Director

- Added a quality-first, resumable large-library workflow: `vlog prepare`,
  `inspect next`, idempotent `observe`, three StoryGraph candidates, comparison,
  refinement, proxy preview, one-command resume and original-media delivery.
- Every playable non-duplicate source requires baseline external-AI evidence;
  incomplete coverage stops with `REVIEW_REQUIRED` instead of silently dropping
  footage. FACUT does not download or embed a local visual model.
- Added evidence-addressed transition/effect/music intentions. Unsupported
  comedy effects remain explicitly review-required and are never reported as
  rendered.

### Captions, typography and licensed media

- Added review-first word-timestamp ASR plans, glossary support, optional local
  diarization provider, readable caption wrapping, subtitle proofs and atomic
  application of approved cues. Raw dialogue is retained separately from its
  display formatting.
- Added system-font scanning, logical font roles, license/hash records, CJK
  glyph audits and content-adaptive typography plans. Existing Lingang title
  templates are retained alongside science, humanities, comedy, cinematic,
  warm-family and food variants.
- Added a local music/SFX catalog with platform license audit and seven
  inspectable VLOG style packs.
- Final VLOG delivery now blocks missing fonts or glyphs and writes a full
  decode/audio/video QC report next to the project render records.

### Agent contract

- Exposed VLOG, subtitle, typography, font, library and style operations through
  schemas and loopback JSON-RPC. Long visual decisions remain the responsibility
  of the calling multimodal AI and are stored as strict `evidence.v2` records.

## 0.7.2 — 2026-08-17

### Fixed

- Unified one-shot and warm-service voice device selection. A configured CPU
  PyTorch overlay now protects both paths when `device=auto`, while
  `--require-cuda` and explicit `--device cuda` are never silently downgraded.
- Rejected the contradictory `--device cpu --require-cuda` combination before
  provider startup, preserved device requirements during one-shot fallback,
  and prevented inherited CUDA-required environment flags from leaking into
  CPU workers.
- Isolated the frozen warm-service process from its parent PyInstaller
  extraction directory so repeated service starts do not leave `_MEI` folders.
- Kept generated PyInstaller spec files under the ignored build directory so
  local absolute paths cannot overwrite the portable tracked release spec.
- Added `device` and service-selection fields to narration and Agent schemas,
  and reject undeclared parameters for the affected RPC actions.

## 0.7.1 — 2026-08-12

### Fixed

- Unified tests and runtime media-tool selection around FACUT's embedded or
  reviewed FFmpeg 5+ resolver; legacy PATH binaries are rejected.
- Still-image probe durations are no longer treated as real clip lengths.
  `timeline add`, Recipes and Agent Schema now accept `duration`, with a
  non-destructive five-second default for images.
- Two-pass EBU R128 mastering now scans audio only, stream-copies the rendered
  video master, emits continuous JSONL progress, reserves an AAC true-peak
  safety margin and verifies the muxed result.
- Doctor now distinguishes runtime extraction, stable install identity, tool
  source, and detected/usable/implemented video encoders.
- The eager version path avoids importing every editing subsystem.

### Release safety

- Added a tracked-file privacy gate and ignored local voice auditions, model
  fragments, provider builds and machine-only download helpers.
- PyInstaller paths are now checkout-relative rather than author-machine
  absolute paths.
- CPU voice PyTorch overlays are explicit configuration and do not replace the
  CUDA runtime or modify retained voice recordings.

### Verified

- 201 automated tests pass on the Windows development environment with the
  same modern-tool resolver used by the product.

## 0.6.2 — 2026-08-04

- Fixed Windows installation so the frozen FACUT directory is de-duplicated
  and placed first in the user and current-process PATH. This prevents an older
  Python console-script launcher from shadowing the installed EXE.

## 0.6.1 — 2026-08-04

- Added `voice studio` with no-ID startup, arbitrary profile names, profile
  switching, resumable progress, and quick/recommended/style-capsule modes.
- Added stable ID/alias/unique-display-name voice selection, recoverable profile
  rename, and project/global default voices without changing existing WAV files.
- Added the authenticated loopback `voice serve` daemon and persistent CosyVoice
  JSONL protocol so CUDA models can remain loaded between synthesis requests.
- Added audition-first `voice say`, deterministic `auto` style selection,
  intensity/speed controls, multi-take outputs, and explicit CUDA enforcement.
- Added strict narration plans and `narration generate/review/synthesize/apply`;
  only reviewed previews enter a dedicated narration track in one undoable
  revision, with hash, project and revision preflight checks.
- Added real render-graph music ducking ramps for narration regions while
  preserving camera/original audio.
- Added declarative `recipe validate/plan/build`, JSON Schemas and Agent actions.
  CLI builds render to a temporary file and only publish a completed output.
- Added the hidden frozen-EXE daemon entry point and synchronized package/runtime
  version metadata at `0.6.1`.

Known limitation: deterministic narration drafting is built in; requesting an
unconfigured local text model returns `PROVIDER_NOT_CONFIGURED` rather than
pretending that AI text generation occurred.

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

## 0.7.0 — 2026-08-04

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
