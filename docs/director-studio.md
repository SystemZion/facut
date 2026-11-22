# Director Studio

[Documentation](index.md)

## Open, resume, close

```bash
facut -p demo review open
facut --json -p demo review status
facut -p demo review export --output review.json
facut -p demo review close
```

The Review Room binds to 127.0.0.1 and uses a random port and token.
Reopen with `review open`; project feedback and patch artifacts remain on disk.
Do not share authenticated session URLs or expose the service publicly.

## Opinions are not edits

```bash
facut -p demo edit "Make the opening calmer."
```

This saves feedback and returns the next structured action.
An external Agent must supply the actual timeline operations.

## Plan, inspect, approve, apply

```bash
facut -p demo review patch plan agent.patch.json --output planned.patch.json
facut -p demo review patch preview planned.patch.json
facut -p demo review patch apply approved.patch.json --approved-only --dry-run
facut -p demo review patch apply approved.patch.json --approved-only
facut -p demo undo
```

Plan binds the patch to the current project and evidence. Preview validates
operations and returns the diff and affected ranges; **it is not a video render**.
Current patches may become stale after an unrelated project revision; dependent
subgraph invalidation is not claimed as implemented.

After reviewing, mark only accepted operations `approved: true` in a copy of the
planned patch. Automated tests/demo scripts may approve a fixed synthetic edit;
that is not evidence of a person reviewing a real film.

## Real comparison

Render the existing timeline to a new preview file. Inspect the patch diff,
apply approved operations, then render a second preview:

```bash
facut -p demo render --output demo/previews/before.mp4 --width 1920 --height 1080 --hardware none
# Plan, inspect and approve the patch before this operation.
facut -p demo review patch apply approved.patch.json --approved-only
facut -p demo render --output demo/previews/after.mp4 --width 1920 --height 1080 --hardware none
facut -p demo review open
```

This produces real full-timeline A/B files. It does not claim that current
`review patch preview` performs changed-range rendering.
Use reduced-resolution `render` for effect approval: current fast `preview`
omits clip effects, so it cannot prove that an effect or grade looks correct.
CutGraph comparison also supports
`facut -p demo preview compare main HEAD --changed-only`; inspect its output and
fallback warnings on your installed version.

Natural-language requests, machine-valid diffs, visually reviewed files, and
approved delivery are distinct stages. Do not present a planned patch as an
already-reviewed 4K result.
