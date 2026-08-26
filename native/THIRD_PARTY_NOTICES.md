# FACUT Native Accelerator third-party notices

FACUT Native Accelerator dynamically links to FFmpeg shared libraries. The
Windows bundle uses the LGPL shared build from
[BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds). FFmpeg is licensed
under the GNU Lesser General Public License, version 3 or later for this build.
The shared DLLs remain separate and replaceable. FFmpeg source and build scripts
are available from the linked project and from
[ffmpeg.org](https://ffmpeg.org/).

The native executable embeds nlohmann/json 3.12.0, licensed under the MIT
License. Source is available from
[nlohmann/json](https://github.com/nlohmann/json).

FACUT does not bundle models, user media, voice samples, fonts, caches, or
project-specific absolute paths in the native distribution.
