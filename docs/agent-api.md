# Agent integration

[Documentation](index.md)

## Responsibilities

The Agent reads media evidence and makes creative decisions. FACUT validates and
executes deterministic operations. A human reviews subjective choices and approves
changes. FACUT has no embedded LLM or general visual model.

## Discover, do not guess

```bash
facut --json schema commands
facut --json schema project
facut --json schema director-studio
facut --json schema workflow vlog
facut --json schema action review.patch.plan
```

Query an action schema only after confirming that the installed version
advertises it. Some timeline operations are CLI/batch operations but not separately
advertised Agent actions.

## Batch and persistent transport

```bash
facut --json -p demo run edits.json --dry-run
facut --json -p demo run edits.json
facut -p demo serve
```

Example batch, replacing identifiers with values from the actual project:

```json
{
  "version": "1.0",
  "atomic": true,
  "commands": [
    {"action": "clip.move", "clip_id": "CLIP_ID", "to": 2}
  ]
}
```

Atomic timeline batches roll back on failure. Cross-domain operations that
produce external artifacts are not automatically atomically reversible.
For stdio JSON-RPC request examples and response envelopes, see the
[full protocol guide](handbook.zh-CN.md#ai-agent-调用).

## Travel workflow

```powershell
facut scan "D:\Trip" -p "D:\TripProject"
facut --json -p "D:\TripProject" next
facut --json -p "D:\TripProject" vlog inspect next
# The external visual Agent inspects the supplied evidence and writes observations.
facut --json -p "D:\TripProject" vlog observe observations.json
facut --json -p "D:\TripProject" next
```

Continue using the returned next step. Do not infer content from filenames,
exclude silent scenery as empty, or invent observations to unlock a gate.
Confirm duration and story direction with the user before timeline construction.
Draft previews precede final delivery.

`cut` is a compact entry into the VLOG workflow, not an autonomous visual model.
`--auto` does not bypass missing evidence, factual uncertainty, or approval.

Quality comes first. Cache repeated observations and batch small operations
to reduce overhead; do not trade away first-pass coverage to save tokens.
