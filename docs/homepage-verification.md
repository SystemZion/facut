# Homepage refresh verification

Scope: product documentation, an artifact-backed generated demo, repository
metadata, and one directly encountered Review CLI serialization fix.
No media engine redesign, new model, release binary, or community launch.

## Locally verified

- Full test suite: **345 passed**, Windows maintainer environment, 61.03s.
- Focused landing-page / patch / Director Studio / shortcut suite: **17 passed**.
- Privacy scan: passed; no recordings, models, user fonts, or private paths added.
- English landing page: approximately 6 KB instead of approximately 38 KB.
- Chinese landing page and the original detailed Chinese handbook retained.
- Relative documentation links resolve.
- 19.2s animated GIF, approximately 1.5 MB; manifest records SHA-256.
- Source MP4 generated from FFmpeg test patterns; no personal travel clips.
- Color and monochrome review renders both 6.0s; fully decoded without error.
- Original generated source hash unchanged.
- Semantic patch preview and approved dry-run leave the project hash unchanged.
- Unapproved operation is rejected.
- Approved effect reaches the actual rendered frame; measured chroma check passes.
- Social card: 1280×640 PNG and editable SVG, no bundled font files.

## Discoveries

1. Review CLI used plain json.dumps on domain objects after committing an edit.
   This could report failure after successful mutation. The output now uses the
   shared response model; human and JSON paths have a regression test.
2. Fast preview omits clip effects. The demo therefore uses reduced-resolution
   render for truthful before/after comparison; that preview behavior is not
   changed or hidden by this documentation PR.
3. Patch preview reports semantic differences, not rendered A/B media.

## Not claimed

- Not a Review Room browser recording or a human approval session.
- Not an autonomous Agent or visual interpretation benchmark.
- Not a test of 4K output, latest release installation, or launch traction.
- macOS/Linux CI results remain separate from local Windows validation.
- Existing portable v1.0.0 binaries have not been replaced.
- Social Preview asset creation is not proof of upload in repository settings.

The original working checkout and its unrelated local changes remain untouched.
Only this independent product-homepage branch is published for review.
