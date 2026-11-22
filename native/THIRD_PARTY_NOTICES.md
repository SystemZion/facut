# Facut Native Accelerator third-party notices

The Windows native accelerator dynamically links to replaceable FFmpeg 9.1
shared DLLs built from the signed upstream FFmpeg 9.1 release, with
`--enable-version3` and without GPL or nonfree components. These libraries are
licensed under the GNU Lesser General Public License, version 3 or later.
The corresponding FFmpeg source archive is available alongside the binaries
on the Facut release page and from [FFmpeg](https://ffmpeg.org/releases/ffmpeg-9.1.tar.xz).
The reproducible build recipe is `tools/build_ffmpeg_windows_sdk.sh`.

The separate `ffmpeg.exe` and `ffprobe.exe` programs bundled for subprocess use
are the GPLv3 [Gyan Windows 9.1 essentials build](https://github.com/GyanD/codexffmpeg/releases/tag/9.1).
Its release identifies the [exact FFmpeg source revision](https://github.com/FFmpeg/FFmpeg/tree/74e4393595)
and [bundled third-party libraries](https://www.gyan.dev/ffmpeg/builds/#libraries).
These executables are not linked into the Facut Python program or C++ accelerator.
The notices and license supplied with the CLI distribution are carried in the
Facut ZIP.

The native executable embeds nlohmann/json 3.12.0, licensed under MIT. Source:
[nlohmann/json](https://github.com/nlohmann/json).

Facut does not bundle model weights, user media, voice samples, caches, or private
project records.
