# Atlas evidence planning and local ASR quality profile

## Evidence planning

Run `facut --json -p PROJECT vlog inspect batch`. A deep batch can report an
`evidence_plan` with up to four explicit requests of at most 12 seconds each.
Each request identifies the source media, source SHA-256, observed event
interval, proposed clip interval, and whether the event fits in the clip.
`omitted_windows` counts windows not included in this plan, not skipped assets.
The inspection batch still lists every asset and makes no new semantic claims.
Use each `argv` with `facut --json -p PROJECT` to create the actual clip.
Review whether the clip returned source audio; a source can have no audio.
Nothing is rendered or sent by inspecting the batch alone.

## Quality ASR

The default ASR and voice model do not change. To compare a compatible model
you already have locally, first link its directory, then opt in per command:

```powershell
facut models link srt_quality D:\Models\faster-whisper-large-v3
facut --json analyze transcript video.mp4 --model-profile quality
facut --json analyze transcript video.mp4
```

The quality profile requires local CTranslate2 Whisper files
(`config.json`, `model.bin`, and `tokenizer.json`); it never downloads model
weights or silently falls back. For a fair comparison, do not use `--save`
until you choose a result. Use the same audio and score the edited subtitle
effort, words near cuts, and false subtitles on non-speech, as well as runtime.
The [Systran large-v3 conversion](https://huggingface.co/Systran/faster-whisper-large-v3)
is a candidate, not a quality claim for unseen footage.
