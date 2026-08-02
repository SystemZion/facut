# Bundled FFmpeg

The Windows release embeds `ffmpeg.exe` and `ffprobe.exe`, but these third-party
binaries are intentionally excluded from Git history.

Download an FFmpeg essentials build and place both executables in this directory
before running:

```powershell
.\tools\build_exe.ps1 -Clean
```

You may instead pass explicit paths:

```powershell
.\tools\build_exe.ps1 `
  -FFmpeg C:\path\to\ffmpeg.exe `
  -FFprobe C:\path\to\ffprobe.exe `
  -Clean
```

The official `facut.exe` release asset includes the media tools and does not
require a system FFmpeg installation.

## v0.4 Windows release baseline

FACUT 0.4 is built and hardware-tested with Gyan FFmpeg `8.0.1-essentials_build`.
FFmpeg 8.1.2 requires NVENC API 13.1 and failed on otherwise healthy NVIDIA
581.80 drivers that expose API 13.0, so the release intentionally stays on
8.0.1 until the driver compatibility floor is broadly available.

```text
ffmpeg.exe  SHA-256 5af82a0d4fe2b9eae211b967332ea97edfc51c6b328ca35b827e73eac560dc0d
ffprobe.exe SHA-256 192a1d6899059765ac8c39764fc3148d4e6049955956dc2029f81f4bd6a8972d
```

Both executables were checked with `facut doctor --sample-render`; NVENC and
QSV completed real sample encodes on the Windows release machine.
