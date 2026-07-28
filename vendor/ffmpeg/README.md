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
