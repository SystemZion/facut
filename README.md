# FACUT · Fast AI Cut

**Video editing built for AI agents—with human approval.**

[![Tests](https://github.com/SystemZion/facut/actions/workflows/tests.yml/badge.svg)](https://github.com/SystemZion/facut/actions/workflows/tests.yml)
[![Release](https://img.shields.io/github/v/release/SystemZion/facut)](https://github.com/SystemZion/facut/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

English · [简体中文](README.zh-CN.md)

Agents make creative decisions. FACUT validates and executes structured edits
without changing your original media.

**Prompt → Timeline Patch → Review → Approve → Render**

![A real FACUT patch changes generated color footage to monochrome](docs/assets/workflow-demo.gif)

*Reproducible workflow visualization, not a UI screen recording. The before/after
frames are real FACUT renders of synthetic footage. No travel footage, personal
recordings, or embedded AI model is used. [Reproduce the demo](examples/homepage-demo/README.md).*

[Download for Windows](https://github.com/SystemZion/facut/releases/latest) ·
[Quick start](docs/getting-started.md) ·
[Agent integration](docs/agent-api.md) ·
[Documentation](docs/index.md)

## Why FACUT?

- **Agent-native:** discoverable schemas, stable media IDs, JSON responses,
  atomic edit batches, and persistent stdio JSON-RPC.
- **Deterministic:** your Agent supplies decisions; FACUT checks and executes them.
- **Reviewable:** preserve evidence, inspect patches, compare rendered versions,
  and apply approved operations on an experimental history branch.
- **Non-destructive:** source media stays untouched; edits live in project files.
- **Local-first:** FFmpeg rendering and a loopback-only, token-authenticated Review
  Room. An external cloud Agent may transmit the evidence you choose to give it.

Designed for travel VLOGs, everyday stories, and repeatable scripted edits—not
a promise that one command understands a folder and produces a finished film.

## Start in five minutes

### Windows

Download **the complete portable ZIP** from [Releases](https://github.com/SystemZion/facut/releases/latest),
extract it, and run `facut.exe` from that directory. Keep its runtime, tools,
native accelerator, and license files together. Optional voice/ASR models are
downloaded separately; they are not included in the release.

The current v1.0.0 portable package has a Patch-result formatting bug fixed in
this source checkout. See [the known release boundary](docs/limitations.md).

### macOS / Linux / source installation

Install Python 3.11+ and a modern FFmpeg/FFprobe, then:

```bash
git clone https://github.com/SystemZion/facut.git
cd facut
python -m pip install .
facut doctor
```

These platforms use the Python package, not the Windows EXE. Hardware acceleration
depends on the host; see [installation and limitations](docs/getting-started.md).

### A small, explicit edit

```bash
facut init demo
facut --json -p demo import input.mp4
facut -p demo timeline track add --type video --name V1
# Replace MEDIA_ID with the ID returned by import.
# The source must contain at least six seconds.
facut -p demo timeline add MEDIA_ID --track V1 --at 0 --in 1s --out 6s
facut -p demo preview timeline --output demo/previews/review.mp4
facut -p demo review open
# Inspect the preview before rendering:
facut -p demo render --hardware none --output demo/renders/final.mp4
```

For an Agent-led trip workflow, start with `facut scan`, supply real observations,
and follow `facut next`. Missing evidence requires review; `--auto` does not
invent it. [Travel workflow](docs/agent-api.md#travel-workflow).

## From an opinion to a safe edit

```bash
facut -p demo edit "Make this shot black and white."
# An external Agent supplies a structured patch—not FACUT itself.
facut -p demo review patch plan agent.patch.json --output planned.patch.json
facut -p demo review patch preview planned.patch.json
# After inspecting the preview, explicitly approve the intended operations.
facut -p demo review patch apply approved.patch.json --approved-only --dry-run
facut -p demo review patch apply approved.patch.json --approved-only
```

The patch preview command reports a semantic diff and affected ranges.
It does **not** by itself render an A/B movie.
[Review Room and the complete comparison procedure](docs/director-studio.md).

## What's inside?

| Capability | Purpose |
|---|---|
| Scene Atlas / evidence | Present media to an external visual Agent and retain its observations |
| Timeline / effects | Trim, layer, transform, add text, subtitles, audio, and registered effects |
| CutGraph | Branches, diffs, undo/redo, and safe fast-forward acceptance |
| Music Library | Search local assets with platform/license checks and audition-first plans |
| Voice / narration | Optional authorized local voice providers and reviewed narration plans |
| Recipe / JSON-RPC | Declarative builds and persistent automation |
| Proxy / QC / exchange | Preview proxies, final-source checks, media QC, OTIO/FCPXML exchange |

## Honest boundaries

FACUT does **not** include an LLM or a general visual model. Story quality depends
on the Agent's observations and human review. Natural-language `edit` saves a
request; it does not autonomously interpret and execute it.

Final encoding currently centers on H.264/AAC. FCPXML is a lossy subset.
Transition-bearing timelines can fall back from incremental to full rendering.
Do not assume automatic HDR→SDR conversion or subject tracking without supplied
trajectory data. [Detailed limitations](docs/limitations.md).

## Documentation & contributing

- [Documentation index](docs/index.md)
- [Complete technical handbook (Chinese)](docs/handbook.zh-CN.md)
- [Examples](examples/) · [Architecture and development](docs/architecture.md)
- [Report a reproducible issue](https://github.com/SystemZion/facut/issues)

Tests generate media fixtures rather than storing large source videos.
The CI badge above links to actual workflow results; test counts and hardware
timings are version- and machine-specific.

FACUT source is MIT-licensed. Bundled tools and third-party media, fonts, models,
and music retain their own licenses; the source license does not grant rights to
your assets.
