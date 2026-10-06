# Reproducible homepage demo

This is a **workflow visualization of executed commands**, not a recording of
the Review Room or an autonomous AI session.

The script creates FFmpeg test-pattern video, builds a real FACUT timeline,
renders a color review file, records an English feedback request, plans a
`black-white` effect patch, verifies preview does not change the project,
explicitly approves that synthetic test operation, applies it, and renders
the monochrome result. The homepage GIF uses actual frames from those renders.
Both review files use reduced-resolution `render`, because the fast `preview`
graph currently omits clip effects.

No human voices, copyrighted music, travel clips, models, or private evidence
are used. Approval is automated **only for this predefined synthetic test**.

This run uses the source checkout with the accompanying Review CLI serialization
fix. The v1.0.0 portable release does not yet include that fix: applying a patch
may commit successfully but then return an error while formatting its result.
Inspect the project before retrying; do not assume the error means rollback.

## Run

From the repository with its Python dependencies and modern FFmpeg/FFprobe:

```bash
python tools/build_homepage_demo.py --work-dir /tmp/facut-homepage-demo --assets-dir docs/assets
```

Choose a new writable work directory. Existing outputs are not overwritten.
FFmpeg may be specified with `--ffmpeg`, and FFprobe with `--ffprobe`;
otherwise they use FACUT's resolver.
The script runs against this checkout's `src`, not a globally editable install.

On the maintainer's Windows host, the run used `mediakit.FFMPEG`.
On another host, pass the full path to your modern FFmpeg.

Generated work files include source, before/after MP4, input/planned/approved
patches, stdout/stderr transcript and render durations. Only the GIF and a
sanitized verification manifest belong in the public repository. Work files
remain outside the repository.

## What this proves / does not prove

Proves: import, explicit timeline placement, request capture, patch planning,
non-mutating semantic preview, approval-gated apply, real rendered effect,
source hash preservation, and unchanged output duration.

Does not prove: creative quality, LLM interpretation, a user's approval,
Review Room browser interaction, changed-range performance, or 4K delivery.
