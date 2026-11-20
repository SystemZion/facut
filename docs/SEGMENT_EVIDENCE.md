# Facut segment evidence (first version)

From a source checkout containing this patch (not the installed 0.9.3 executable):

```sh
PYTHONPATH=src python -m facut --json -p /path/to/project proxy evidence MEDIA_ID \
  --start 12.5 --end 20 --height 540 --output /path/to/NEW-bundle --timings
```

`--output` is an optional **new directory**, never an existing directory/file.
Without it, a unique directory is created under the project's `evidence/`.
The result uses the existing Facut JSON success/error envelope and adds data with
schema `facut.segment-evidence.v1`: registered source identity, requested half-open
range in seconds, original-audio status, generated clip path, manifest and SHA256.

## Contract

- Video plus the first original audio stream are transcoded together to H.264/AAC.
  The registered `asset.path` is used, **never** `proxy_path`; a missing original
  fails even when an LRF exists. No mute, mix or volume adjustment. Timestamp gaps are padded with silence to preserve alignment.
- No original audio means successful video evidence with `absent_in_source`, not
  an unexplained missing track. `included` means a track exists, not that it was
  listened to or that speech is intelligible. A source track wholly outside the requested
  interval returns `outside_selected_range` with video only. Explicit audio stream timing
  is required; missing/ambiguous timing fails closed.
- Finite nonnegative start, increasing end, actual source-duration bounds and at
  least one frame are required. An end at the source duration is allowed. There
  is no silent clamping. Requests over 60 seconds fail; split longer requests.
- V1 requires explicit video-stream duration and aligned video start time; ambiguous
  duration or offset video fails rather than using audio/container duration as proof.
- Resolution is bounded (even height 120..1080, default 540); video scale avoids
  height upscaling. Software encode uses two threads and a 300-second timeout.
- No project save/relink, source overwrite, remote transfer, ASR, semantic search,
  automatic observation approval or delivery-master claims.
- The source's registered SHA256 is retained but **not rehashed** on each call;
  the manifest states `hash_reverified: false`. Size/mtime changes during the run
  fail. Generated artifact SHA256 is computed and actual output stream/duration
  presence checked before success. This is not a full decoded-media QA guarantee.
- Output timestamps start around zero; provenance maps them to requested source
  seconds. Frame quantization/AAC delay can introduce small duration differences.
  Exact VFR boundary behavior, unusual stream start offsets, HDR/color and Windows
  codec behavior still require target-machine validation.
- Encoding uses a private staging directory, cleaned on failure. Publication uses exclusive
  directory creation and hard links; existing destinations/files are never overwritten.
  Public paths are never removed on failure, since another writer may own them. A rare
  publication race/error can leave a partial bundle for inspection. Filesystems must support
  hard links; unsupported volumes fail safely rather than falling back to unsafe overwrite. Repeated calls currently re-encode (no cache claim).

## Timings

`--timings` adds `data.timings` only to the returned JSON, using StageTimer.
Probe, combined proxy encode, validation, and manifest write are measured. Audio
extraction is explicitly marked as part of proxy encode; still-frame extraction is
not applicable. No fabricated FFmpeg CPU/GPU measurements. The persistent manifest
excludes timings to avoid recording its own still-running write stage.

## Validation and application limits

Built against public main `b63331f1f31f60183a60be374093810e19e1bb38`.
Windows checkout was disconnected before any writes. Its dirty state is **not**
equivalent to this baseline. Review/apply the patch with `git apply --check` first;
never replace the whole local checkout or installed application.

Focused regression command:

```sh
python -m pytest tests/test_segment_evidence.py tests/test_performance.py tests/test_proxy_manager.py -q
```

Tests use short synthetic video/audio, not personal footage. Include original audio
with a silent proxy linked, no audio, invalid/NaN/Inf/out-of-range boundaries,
missing original, output/source protection, failed encoder cleanup, source changes,
JSON errors, end-of-source, optional timings, single-frame float boundaries, competing
public output writers, and decoded delayed AAC silence/440Hz/880Hz alignment. Windows
codec and filesystem validation is still required. See accompanying test log for full-suite results.
