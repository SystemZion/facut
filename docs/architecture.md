# Architecture and development

[Documentation](index.md)

```text
Agent / human
    → CLI JSON / batches / stdio JSON-RPC
    → validated project + evidence + Timeline Patch
    → timeline / CutGraph / media / effects / subtitles
    → FFmpeg graph + optional C++ accelerator
    → preview / Review Room / QC / render
```

Source modules are under `src/facut/`: `cli`, `core`, `media`, `render`,
`director`, `vlog`, `agent`, `subtitles`, `voice`, `qc`, `exchange`,
and `travel`. Native acceleration is optional; it is not a separate creative AI.

```bash
python -m pip install -e ".[dev]"
pytest
```

Media fixtures are generated with FFmpeg. Use safe argument-list subprocess
calls, not shell-string interpolation. Preserve original media and authorization
assets. Public examples must not include personal recordings, user font files,
private absolute paths, or models.

The CI badge links to the current test matrix; consult individual jobs for
actual platform coverage and skips. Reproduce a real render with
[the homepage demo](../examples/homepage-demo/README.md).
