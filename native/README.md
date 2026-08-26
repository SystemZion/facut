# FACUT Native Accelerator

`facut-native` is an optional C++20 sidecar for bounded, high-throughput media analysis. It does not replace FACUT's Python CLI, deterministic project model, StoryGraph, or FFmpeg rendering backend.

## Windows build

Use an x64 Visual Studio installation and an LGPL shared FFmpeg development distribution containing `include`, `lib`, and `bin` directories:

```powershell
powershell -ExecutionPolicy Bypass -File tools\build_native.ps1 `
  -VSRoot D:\Path\To\VisualStudio `
  -FFmpegRoot D:\path\to\ffmpeg-lgpl-shared
```

The build fetches pinned nlohmann/json 3.12.0 with SHA-256 verification. FFmpeg remains dynamically replaceable and must be distributed with its LGPL notices and corresponding build/source information.

## Protocol

Run `facut-native --jsonl`. Standard output contains JSON Lines only; diagnostics use standard error. Protocol version 1 supports `doctor` and `media.batch_scan`.
