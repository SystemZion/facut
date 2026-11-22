# Getting started

[Documentation](index.md)

## Windows portable build

1. Download the complete Windows ZIP from [Releases](https://github.com/SystemZion/facut/releases/latest).
2. Extract to a writable directory. Keep the runtime, bundled tools, accelerator,
   and license files together.
3. Open a terminal in that directory and run `./facut.exe doctor`.
4. Add the directory to PATH if you want to use `facut` from elsewhere.

Do not substitute an old system FFmpeg for the bundled modern tools.
Models, recordings, user fonts, and caches are not part of the download.

## macOS / Linux / development

Install Python 3.11+ and modern FFmpeg and FFprobe using your OS package manager.
Then clone the source and run:

```bash
python -m pip install .
facut --version
facut doctor
```

There is no macOS native app bundle in the Windows release. CUDA, QSV, NVENC,
and other accelerators are host-dependent; `--hardware none` uses software.

## Explicit first edit

Use an input with at least six seconds of valid video.

```bash
facut init demo
facut --json -p demo import input.mp4
facut -p demo timeline track add --type video --name V1
facut -p demo timeline add MEDIA_ID --track V1 --at 0 --in 1s --out 6s
facut -p demo preview timeline --output demo/previews/review.mp4
facut -p demo review open
facut -p demo review close
facut -p demo render --hardware none --output demo/renders/final.mp4
```

Replace MEDIA_ID with the returned ID; filenames are not timeline identifiers.
Look at the actual preview before rendering. Outputs are not overwritten by
default. Keep source media and output directories separate.

Global options come before the command: `facut --json -p demo timeline show`.
Quote paths with spaces. Time inputs accept seconds, `1.5s`, `1500ms`,
`00:00:01.500`, and frame counts such as `45f`.

For Agent-led VLOGs, continue with [the evidence workflow](agent-api.md).
For an entirely reproducible generated sample, run [the demo](../examples/homepage-demo/README.md).
