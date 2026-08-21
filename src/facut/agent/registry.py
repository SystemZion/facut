"""Stable action vocabulary and JSON Schemas for AI callers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from facut import __version__


def _object(
    properties: dict[str, Any], required: list[str] | None = None
) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required or [],
    }


_ACTIONS: dict[str, dict[str, Any]] = {
    "project.snapshot": {
        "summary": "Read the complete Agent-facing project state.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "timeline.show": {
        "summary": "Read tracks, clips, transitions and markers.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "timeline.track.add": {
        "summary": "Add a typed timeline track.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "type": {"enum": ["video", "audio", "image", "subtitle", "adjustment", "mask"]},
                "name": {"type": "string", "minLength": 1},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["type", "name"],
        ),
    },
    "timeline.add": {
        "summary": "Place a source range on a track or append it to the track end.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "media_id": {"type": "string"},
                "track": {"type": "string"},
                "at": {
                    "oneOf": [
                        {"type": "number", "minimum": 0},
                        {"type": "string", "minLength": 1},
                    ],
                    "default": 0,
                },
                "append": {"type": "boolean", "default": False},
                "in": {
                    "oneOf": [
                        {"type": "number", "minimum": 0},
                        {"type": "string", "minLength": 1},
                    ],
                    "default": 0,
                },
                "out": {
                    "oneOf": [
                        {"type": "number", "exclusiveMinimum": 0},
                        {"type": "string", "minLength": 1},
                    ]
                },
                "duration": {
                    "oneOf": [
                        {"type": "number", "exclusiveMinimum": 0},
                        {"type": "string", "minLength": 1},
                    ],
                    "description": "Clip timeline duration; still images default to 5 seconds.",
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            ["media_id", "track"],
        ),
    },
    "clip.move": {
        "summary": "Move a clip without modifying source media.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string"},
                "to": {"type": "number", "minimum": 0},
                "track_id": {"type": "string"},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id"],
        ),
    },
    "clip.split": {
        "summary": "Split a clip at an exact timeline time.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string"},
                "at": {"type": "number", "exclusiveMinimum": 0},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id", "at"],
        ),
    },
    "exchange.export": {
        "summary": "Export OTIO or FCPXML with an explicit loss report.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "output": {"type": "string", "minLength": 1},
                "format": {"enum": ["otio", "fcpxml"]},
                "sequence": {"type": ["string", "null"]},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["output", "format"],
        ),
    },
    "exchange.import": {
        "summary": "Plan or apply an OTIO/FCPXML timeline import.",
        "mutates": True,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "format": {"enum": ["otio", "fcpxml"]},
                "apply": {"type": "boolean", "default": False},
            },
            ["source", "format"],
        ),
    },
    "semantic.index": {
        "summary": "Build a hash-addressed semantic index from project metadata and optional vision observations.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {"observations": {"type": ["string", "null"]}}
        ),
    },
    "semantic.search": {
        "summary": "Find exact source ranges with score, confidence, provider and evidence.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "query": {"type": "string", "minLength": 1},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "minimum_score": {"type": "number", "minimum": 0, "default": 0.05},
            },
            ["query"],
        ),
    },
    "story.build": {
        "summary": "Build an inspectable travel-story plan and optionally apply it to a named sequence.",
        "mutates": True,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "style": {"enum": ["travel-documentary", "immersive-vlog", "cinematic-travel"], "default": "travel-documentary"},
                "target_duration": {"type": "number", "minimum": 1, "default": 600},
                "sequence": {"type": "string", "minLength": 1, "default": "ai-story"},
                "apply": {"type": "boolean", "default": False},
            }
        ),
    },
    "broll.diagnose": {
        "summary": "Inspect B-roll coverage, source repetition, and narration/visual mismatches.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {"maximum_aroll": {"type": "number", "minimum": 1, "default": 8}}
        ),
    },
    "narration.suggest": {
        "summary": "Suggest evidence-grounded VLOG talking points and review-first draft narration.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "style": {
                    "enum": ["natural-vlog", "travel-documentary", "cinematic-travel"],
                    "default": "natural-vlog",
                },
                "language": {"type": "string", "default": "zh-CN"},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 100, "default": 12},
                "minimum_confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.55},
            }
        ),
    },
    "narration.synthesize": {
        "summary": "Synthesize audition candidates and attach them to a narration plan.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "plan": {"type": "string", "minLength": 1},
                "voice": {"type": "string", "minLength": 1},
                "preview_dir": {"type": "string", "minLength": 1},
                "style": {"enum": ["auto", "natural", "broadcast", "chat", "comedy", "excited"], "default": "auto"},
                "takes": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "instruction": {"type": ["string", "null"]},
                "device": {"enum": ["auto", "cuda", "cpu"], "default": "auto"},
                "require_cuda": {"type": "boolean", "default": False},
                "use_service": {"type": "boolean", "default": True},
                "provider": {"type": ["string", "null"]},
            },
            ["plan", "voice", "preview_dir"],
        ),
    },
    "vlog.prepare": {
        "summary": "Create complete baseline frame coverage and resumable external-vision inspection tasks.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": ["string", "null"]},
                "proxy": {"enum": ["none", "auto"], "default": "auto"},
                "batch_size": {"type": "integer", "minimum": 1, "maximum": 100, "default": 12},
                "frames": {"type": "boolean", "default": True},
                "engine": {"enum": ["auto", "native", "python"], "default": "auto"},
                "native_mode": {"enum": ["fast", "deep"], "default": "fast"},
            }
        ),
    },
    "vlog.inbox.next": {
        "summary": "Return the highest-priority truthful review tasks without reducing coverage.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {"limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 8}}
        ),
    },
    "vlog.inbox.list": {
        "summary": "List the complete Director Inbox and stable review IDs.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "vlog.inbox.resolve": {
        "summary": "Resolve one Director Inbox item with an auditable explanation.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "item_id": {"type": "string", "minLength": 1},
                "resolution": {"type": "string", "minLength": 1},
            },
            ["item_id", "resolution"],
        ),
    },
    "vlog.bible.show": {
        "summary": "Read confirmed and uncertain trip context used to constrain generated claims.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "vlog.bible.import": {
        "summary": "Validate and atomically replace a reviewed Trip Bible document.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {"bible": {"type": "object", "additionalProperties": True}}, ["bible"]
        ),
    },
    "native.doctor": {
        "summary": "Verify the optional C++ sidecar, protocol, FFmpeg ABI, and features.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "runtime.status": {
        "summary": "Inspect configured warm background services without loading media models.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "runtime.cleanram": {
        "summary": "Stop explicitly selected warm services without deleting models or caches.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "services": {
                    "type": "array",
                    "items": {"enum": ["voice", "all"]},
                    "minItems": 1,
                    "uniqueItems": True,
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            ["services"],
        ),
    },
    "runtime.warmup": {
        "summary": "Load explicitly selected heavy services and wait for readiness.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "services": {
                    "type": "array",
                    "items": {"enum": ["voice", "all"]},
                    "minItems": 1,
                    "uniqueItems": True,
                }
            },
            ["services"],
        ),
    },
    "runtime.autoload.configure": {
        "summary": "Enable or disable default background warmup for one service.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "service": {"enum": ["voice", "all"]},
                "enabled": {"type": "boolean"},
                "stop_now": {"type": "boolean", "default": False},
            },
            ["service", "enabled"],
        ),
    },
    "analyze.batch": {
        "summary": "Analyze a media tree with the persistent native sidecar or Python fallback.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "folder": {"type": "string", "minLength": 1},
                "engine": {"enum": ["auto", "native", "python"], "default": "auto"},
                "mode": {"enum": ["fast", "deep"], "default": "fast"},
                "output_directory": {"type": ["string", "null"]},
                "limit": {"type": ["integer", "null"], "minimum": 1},
                "jobs": {"type": "integer", "minimum": 1, "maximum": 8, "default": 3},
                "asset_timeout": {"type": "number", "exclusiveMinimum": 0, "default": 180},
            },
            ["folder"],
        ),
    },
    "vlog.inspect.next": {
        "summary": "Return the next bounded visual-inspection task and evidence.v2 schema.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "vlog.observe": {
        "summary": "Validate and idempotently store external visual observations.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "observations": {"type": "array", "items": {"type": "object"}, "minItems": 1},
                "task_id": {"type": ["string", "null"]},
            },
            ["observations"],
        ),
    },
    "vlog.status": {
        "summary": "Report coverage, evidence gaps, director stage, and the exact next command.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "vlog.plan": {
        "summary": "Build three quality-first evidence-grounded StoryGraph candidates.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "style": {"type": "string", "default": "natural-vlog"},
                "target_duration": {"type": "number", "minimum": 1, "default": 480},
                "candidates": {"const": 3},
            }
        ),
    },
    "vlog.compare": {
        "summary": "Compare candidate shot choices, duration, score, and unresolved gaps.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "vlog.refine": {
        "summary": "Run deterministic continuity gates and select one candidate.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {"candidate_id": {"type": "string", "minLength": 1}},
            ["candidate_id"],
        ),
    },
    "vlog.preview": {
        "summary": "Render real 540p candidate previews without changing the saved timeline.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "candidate_id": {"type": ["string", "null"]},
                "all_candidates": {"type": "boolean", "default": False},
                "output_dir": {"type": "string", "minLength": 1},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["output_dir"],
        ),
    },
    "vlog.apply": {
        "summary": "Atomically apply one ready StoryGraph candidate without rendering it.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "candidate_id": {"type": "string", "minLength": 1},
                "preset": {"type": "string", "default": "youtube-4k"},
            },
            ["candidate_id"],
        ),
    },
    "vlog.build": {
        "summary": "Commit one ready candidate and render an original-media platform delivery.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "candidate_id": {"type": "string", "minLength": 1},
                "output": {"type": "string", "minLength": 1},
                "preset": {"type": "string", "default": "youtube-4k"},
                "hardware": {"type": "string", "default": "auto"},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["candidate_id", "output"],
        ),
    },
    "clip.motion": {
        "summary": "Apply a deterministic digital camera-movement preset.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string", "minLength": 1},
                "preset": {"enum": ["slow-push", "slow-pull", "pan-left", "pan-right", "tilt-up", "tilt-down"]},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.35},
                "easing": {"enum": ["linear", "ease-in", "ease-out", "ease-in-out", "cubic"], "default": "ease-in-out"},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id", "preset"],
        ),
    },
    "clip.speed": {
        "summary": "Apply a constant speed, target duration, or negative rate for reverse playback.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string", "minLength": 1},
                "rate": {"type": "number", "not": {"const": 0}},
                "duration": {"oneOf": [{"type": "number", "exclusiveMinimum": 0}, {"type": "string", "minLength": 1}]},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id"],
        ),
    },
    "clip.speed_curve": {
        "summary": "Apply a step or sampled-linear speed curve using source-relative points.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string", "minLength": 1},
                "curve": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "version": {"type": "string", "default": "1.0"},
                        "mode": {"enum": ["step", "linear"], "default": "linear"},
                        "steps": {"type": "integer", "minimum": 1, "maximum": 64, "default": 8},
                        "points": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {"at": {"oneOf": [{"type": "number"}, {"type": "string"}]}, "rate": {"type": "number", "exclusiveMinimum": 0}},
                                "required": ["at", "rate"],
                            },
                        },
                    },
                    "required": ["points"],
                },
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id", "curve"],
        ),
    },
    "audio.process": {
        "summary": "Configure non-destructive high-pass, denoise, compressor and limiter processing.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string", "minLength": 1},
                "highpass_hz": {"type": "number", "minimum": 20, "maximum": 20000},
                "denoise_strength": {"type": "number", "minimum": 0.01, "maximum": 1},
                "compressor_preset": {"enum": ["vlog", "dialogue", "gentle", "off"]},
                "limiter_db": {"type": "number", "minimum": -20, "maximum": 0},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["clip_id"],
        ),
    },
    "audio.crossfade": {
        "summary": "Crossfade two overlapping independent audio clips.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "from": {"type": "string", "minLength": 1},
                "to": {"type": "string", "minLength": 1},
                "duration": {"oneOf": [{"type": "number", "exclusiveMinimum": 0}, {"type": "string", "minLength": 1}]},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["from", "to", "duration"],
        ),
    },
    "history.status": {
        "summary": "Read the active CutGraph branch and redo state.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "history.diff": {
        "summary": "Compare two CutGraph commits using semantic timeline entities.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"before": {"type": "string"}, "after": {"type": "string"}}, ["before", "after"]),
    },
    "history.restore": {
        "summary": "Restore a prior CutGraph state as a new commit.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"commit": {"type": "string", "minLength": 1}}, ["commit"]),
    },
    "branch.create": {
        "summary": "Create a named CutGraph branch without copying media.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"name": {"type": "string", "minLength": 1}, "start": {"type": "string", "default": "HEAD"}, "switch": {"type": "boolean", "default": False}}, ["name"]),
    },
    "branch.switch": {
        "summary": "Switch the project working state to a CutGraph branch.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"name": {"type": "string", "minLength": 1}}, ["name"]),
    },
    "branch.accept": {
        "summary": "Fast-forward a target branch; divergent histories fail safely.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"source": {"type": "string", "minLength": 1}, "target": {"type": "string", "default": "main"}}, ["source"]),
    },
    "proxy.scan": {
        "summary": "Score LRF/proxy candidates and optionally link unique high-confidence matches.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"media_id": {"type": ["string", "null"]}, "search": {"type": "array", "items": {"type": "string"}}, "link": {"type": "boolean", "default": False}, "dry_run": {"type": "boolean", "default": False}}),
    },
    "proxy.link_auto": {
        "summary": "Automatically link one unambiguous proxy candidate for a media asset.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"media_id": {"type": "string"}, "search": {"type": ["string", "null"]}, "dry_run": {"type": "boolean", "default": False}}, ["media_id"]),
    },
    "preview.compare": {
        "summary": "Render paired A/B previews for changed CutGraph ranges.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"before": {"type": "string"}, "after": {"type": "string"}, "output_dir": {"type": "string"}, "changed_only": {"type": "boolean", "default": True}, "padding": {"type": "number", "minimum": 0, "default": 0.5}, "height": {"type": "integer", "minimum": 64, "default": 360}, "fps": {"type": "number", "exclusiveMinimum": 0, "default": 24}, "hardware": {"type": "string", "default": "auto"}, "overwrite": {"type": "boolean", "default": False}}, ["before", "after", "output_dir"]),
    },
    "qc.run": {
        "summary": "Run deterministic decode, black, freeze, silence, loudness and output-spec QC.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"target": {"type": "string"}, "contact_sheet": {"type": ["string", "null"]}, "expected_duration": {"type": ["number", "null"]}, "overwrite": {"type": "boolean", "default": False}}, ["target"]),
    },
    "narration.generate": {
        "summary": "Generate a strict evidence-grounded narration plan without inventing facts.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "output": {"type": "string", "minLength": 1},
                "style": {"type": "string", "default": "weekend-vlog"},
                "language": {"type": "string", "default": "zh-CN"},
                "provider": {"type": "string", "default": "deterministic"},
                "max_lines": {"type": "integer", "minimum": 1, "maximum": 100, "default": 12},
                "minimum_confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.55},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["output"],
        ),
    },
    "narration.apply": {
        "summary": "Apply approved narration previews in one undoable project revision.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "plan": {"type": "string", "minLength": 1},
                "approved_only": {"type": "boolean", "default": True},
                "duck_music": {"type": "boolean", "default": False},
                "preserve_original": {"const": True},
                "track": {"type": "string", "default": "A_NARRATION"},
                "allow_stale": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["plan"],
        ),
    },
    "voice.profile.create": {
        "summary": "Create one consent-gated local digital voice profile.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "name": {"type": "string", "minLength": 1},
                "speaker": {"type": "string", "minLength": 1},
                "language": {"type": "string", "default": "zh-CN"},
                "style": {"type": "string", "default": "natural-vlog"},
                "consent": {"enum": ["self", "authorized"]},
                "consent_statement": {"type": "string", "minLength": 12},
            },
            ["name", "speaker", "consent", "consent_statement"],
        ),
    },
    "voice.profile.list": {
        "summary": "List local voice profiles without exposing private absolute paths.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "voice.profile.rename": {
        "summary": "Rename a voice profile without changing its stable ID or recordings.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {"profile": {"type": "string", "minLength": 1}, "name": {"type": "string", "minLength": 1}},
            ["profile", "name"],
        ),
    },
    "voice.sample.propose": {
        "summary": "Quarantine possible speaker audio for identity review before synthesis.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "profile": {"type": "string", "minLength": 1},
                "transcript": {"type": ["string", "null"]},
                "category": {"type": ["string", "null"]},
                "delivery": {"type": ["string", "null"]},
                "source_media_id": {"type": ["string", "null"]},
                "source_start": {"type": ["number", "null"], "minimum": 0},
                "source_end": {"type": ["number", "null"], "minimum": 0},
                "identity_basis": {"enum": ["unknown", "similarity", "manual"], "default": "similarity"},
            },
            ["source", "profile"],
        ),
    },
    "voice.sample.list": {
        "summary": "List quarantined voice candidates and review state.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "profile": {"type": ["string", "null"]},
                "status": {"enum": ["pending", "approved", "rejected", None]},
            }
        ),
    },
    "voice.sample.show": {
        "summary": "Inspect one quarantined voice candidate.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"candidate_id": {"type": "string", "minLength": 1}}, ["candidate_id"]),
    },
    "voice.sample.approve": {
        "summary": "Make candidate audio eligible after explicit same-speaker confirmation.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "candidate_id": {"type": "string", "minLength": 1},
                "speaker_confirmed": {"const": True},
                "confirmation_statement": {"type": "string", "minLength": 12},
            },
            ["candidate_id", "speaker_confirmed", "confirmation_statement"],
        ),
    },
    "voice.sample.reject": {
        "summary": "Reject candidate audio while retaining review evidence.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "candidate_id": {"type": "string", "minLength": 1},
                "reason": {"type": "string", "minLength": 1},
            },
            ["candidate_id", "reason"],
        ),
    },
    "subtitle.transcribe": {
        "summary": "Create a review-first word-timestamped transcript plan from active timeline media.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "output": {"type": "string", "minLength": 1},
                "media": {"type": "array", "items": {"type": "string"}},
                "model": {"type": ["string", "null"]},
                "language": {"type": "string", "default": "zh"},
                "speaker_diarization": {"type": "boolean", "default": False},
                "word_timestamps": {"type": "boolean", "default": True},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["output"],
        ),
    },
    "subtitle.apply": {
        "summary": "Apply reviewed transcript cues with a readable installed font in one revision.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "plan": {"type": "string", "minLength": 1},
                "approved_only": {"type": "boolean", "default": True},
                "track": {"type": "string", "default": "S_DIALOGUE"},
            },
            ["plan"],
        ),
    },
    "subtitle.glossary.add": {
        "summary": "Add a project-specific person, place, attraction, or domain term.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {"term": {"type": "string", "minLength": 1}, "type": {"type": "string", "default": "term"}},
            ["term"],
        ),
    },
    "typography.plan": {
        "summary": "Match story sections to logical typography roles and real installed fonts.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object({"language": {"type": "string", "default": "zh-CN"}}),
    },
    "typography.apply": {
        "summary": "Apply the reviewed typography plan to compatible short-title overlays.",
        "mutates": True,
        "rpc": True,
        "parameters": _object({"plan": {"type": ["string", "null"]}}),
    },
    "font.scan": {
        "summary": "Scan installed and user-registered fonts without copying them.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"refresh": {"type": "boolean", "default": False}}),
    },
    "font.register": {
        "summary": "Register an external font reference and immutable license evidence.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "font_file": {"type": "string", "minLength": 1},
                "license_file": {"type": "string", "minLength": 1},
            },
            ["font_file", "license_file"],
        ),
    },
    "font.match": {
        "summary": "Resolve a logical typography role to a real font with fallback evidence.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {"role": {"type": "string", "minLength": 1}, "language": {"type": "string", "default": "zh-CN"}},
            ["role"],
        ),
    },
    "font.audit": {
        "summary": "Detect missing font families and glyphs before final delivery.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"language": {"type": "string", "default": "zh-CN"}}),
    },
    "library.music.add": {
        "summary": "Register local music with mood, beat, platform, and license evidence.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "moods": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "license_file": {"type": ["string", "null"]},
                "analyze": {"type": "boolean", "default": True},
            },
            ["source"],
        ),
    },
    "library.sfx.add": {
        "summary": "Register a local sound effect with tags and license evidence.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "tags": {"type": "array", "items": {"type": "string"}},
                "platforms": {"type": "array", "items": {"type": "string"}},
                "license_file": {"type": ["string", "null"]},
            },
            ["source"],
        ),
    },
    "library.search": {
        "summary": "Search local licensed music and SFX by mood, tag, kind, and platform.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "kind": {"type": ["string", "null"]},
                "mood": {"type": ["string", "null"]},
                "tag": {"type": ["string", "null"]},
                "platform": {"type": ["string", "null"]},
            }
        ),
    },
    "library.audit": {
        "summary": "Detect offline, unlicensed, or platform-ineligible library assets.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"platform": {"type": "string", "minLength": 1}}, ["platform"]),
    },
    "style.list": {
        "summary": "List inspectable VLOG directing style packs.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "style.describe": {
        "summary": "Describe one VLOG style's story, sound, typography, transition, and restraint rules.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"name": {"type": "string", "minLength": 1}}, ["name"]),
    },
    "style.validate": {
        "summary": "Warn when the active timeline overuses transitions or effects for its style.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"name": {"type": "string", "minLength": 1}}, ["name"]),
    },
    "voice.alias.set": {
        "summary": "Add a globally unique human-readable alias to a voice profile.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {"profile": {"type": "string", "minLength": 1}, "alias": {"type": "string", "minLength": 1}},
            ["profile", "alias"],
        ),
    },
    "voice.default.set": {
        "summary": "Set the project or global default narration voice.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "profile": {"type": "string", "minLength": 1},
                "scope": {"enum": ["project", "global"], "default": "project"},
            },
            ["profile"],
        ),
    },
    "voice.say": {
        "summary": "Generate audition-first voice candidates with deterministic style selection.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "text": {"type": "string", "minLength": 1},
                "voice": {"type": ["string", "null"]},
                "output": {"type": "string", "minLength": 1},
                "style": {"enum": ["auto", "natural", "broadcast", "chat", "daily-chat", "comedy", "excited"], "default": "auto"},
                "takes": {"type": "integer", "minimum": 1, "maximum": 10, "default": 1},
                "speed": {"type": "number", "minimum": 0.5, "maximum": 2.0, "default": 1.0},
                "intensity": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
                "instruction": {"type": ["string", "null"]},
                "purpose": {"type": ["string", "null"]},
                "device": {"enum": ["auto", "cuda", "cpu"], "default": "auto"},
                "require_cuda": {"type": "boolean", "default": False},
                "use_service": {"type": "boolean", "default": True},
                "provider": {"type": ["string", "null"]},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["text", "output"],
        ),
    },
    "voice.serve.status": {
        "summary": "Inspect the authenticated loopback warm voice service.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({}),
    },
    "voice.studio": {
        "summary": "Open the local multi-profile microphone studio.",
        "mutates": True,
        "rpc": False,
        "requires_user_interaction": True,
        "cli": "facut voice studio",
        "parameters": _object(
            {
                "voice": {"type": ["string", "null"]},
                "mode": {"enum": ["quick", "recommended", "styles"], "default": "recommended"},
                "port": {"type": "integer", "minimum": 0, "maximum": 65535, "default": 0},
            }
        ),
    },
    "voice.profile.import": {
        "summary": "Copy authorized PCM WAV samples into a voice profile with hash deduplication.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "profile_id": {"type": "string"},
                "samples": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "transcript": {"type": ["string", "null"]},
            },
            ["profile_id", "samples"],
        ),
    },
    "voice.profile.validate": {
        "summary": "Run bounded PCM voice-sample QC for one profile.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "profile_id": {"type": "string"},
                "recommended_seconds": {"type": "number", "minimum": 1, "default": 120},
            },
            ["profile_id"],
        ),
    },
    "voice.profile.delete": {
        "summary": "Move a voice profile to recoverable local trash.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {"profile_id": {"type": "string"}, "confirm": {"const": True}},
            ["profile_id", "confirm"],
        ),
    },
    "voice.profile.restore": {
        "summary": "Restore one voice profile from FACUT's recoverable local trash.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {"trash_name": {"type": "string", "minLength": 1}}, ["trash_name"]
        ),
    },
    "voice.record-plan": {
        "summary": "Generate a deterministic balanced recording prompt plan.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "profile_id": {"type": "string"},
                "target_minutes": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
                "script": {"type": "string", "default": "mandarin-balanced-v1"},
            },
            ["profile_id"],
        ),
    },
    "voice.record": {
        "summary": "Open the localhost-only FACUT microphone recording studio for one authorized profile.",
        "mutates": True,
        "rpc": False,
        "requires_user_interaction": True,
        "cli": "facut voice record <profile_id>",
        "parameters": _object(
            {
                "profile_id": {"type": "string"},
                "target_minutes": {"type": "integer", "minimum": 1, "maximum": 60, "default": 10},
                "script": {"type": "string", "default": "mandarin-balanced-v1"},
                "port": {"type": "integer", "minimum": 0, "maximum": 65535, "default": 0},
                "no_open": {"type": "boolean", "default": False},
            },
            ["profile_id"],
        ),
    },
    "voice.provider.status": {
        "summary": "Inspect the explicitly configured offline voice synthesis provider.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"provider": {"type": ["string", "null"]}}),
    },
    "map.inspect": {
        "summary": "Parse GPX route statistics and normalized track points.",
        "mutates": False,
        "rpc": True,
        "parameters": _object({"source": {"type": "string", "minLength": 1}}, ["source"]),
    },
    "map.animate": {
        "summary": "Render a reproducible offline route animation to a video file.",
        "mutates": False,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "output": {"type": "string", "minLength": 1},
                "duration": {"type": "number", "minimum": 1, "default": 8},
                "width": {"type": "integer", "minimum": 320, "default": 1920},
                "height": {"type": "integer", "minimum": 240, "default": 1080},
                "fps": {"type": "integer", "minimum": 1, "maximum": 120, "default": 30},
                "encoder": {"type": "string", "default": "libx264"},
                "overwrite": {"type": "boolean", "default": False},
                "dry_run": {"type": "boolean", "default": False},
            },
            ["source", "output"],
        ),
    },
    "reframe.plan": {
        "summary": "Compile normalized subject observations into transform keyframes and optionally apply them.",
        "mutates": True,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {
                "clip_id": {"type": "string", "minLength": 1},
                "trajectory": {"type": "string", "minLength": 1},
                "width": {"type": "integer", "minimum": 320, "default": 1080},
                "height": {"type": "integer", "minimum": 320, "default": 1920},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.4},
                "smoothing": {"type": "integer", "minimum": 1, "default": 5},
                "apply": {"type": "boolean", "default": False},
            },
            ["clip_id", "trajectory"],
        ),
    },
    "recipe.validate": {
        "summary": "Validate a declarative recipe and its complete edit plan.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object({"source": {"type": "string", "minLength": 1}}, ["source"]),
    },
    "recipe.plan": {
        "summary": "Compile a recipe into a reviewable atomic command plan.",
        "mutates": False,
        "rpc": True,
        "plan_first": True,
        "parameters": _object(
            {"source": {"type": "string", "minLength": 1}, "output": {"type": "string", "minLength": 1}},
            ["source", "output"],
        ),
    },
    "recipe.build": {
        "summary": "Apply a recipe atomically and execute its render/QC delivery request.",
        "mutates": True,
        "rpc": True,
        "parameters": _object(
            {
                "source": {"type": "string", "minLength": 1},
                "output": {"type": ["string", "null"]},
                "dry_run": {"type": "boolean", "default": False},
                "overwrite": {"type": "boolean", "default": False},
            },
            ["source"],
        ),
    },
    "run": {
        "summary": "Apply an atomic semantic command batch.",
        "mutates": True,
        "rpc": True,
        "parameters": {
            "$ref": "facut://schemas/command.schema.json"
        },
    },
}


def capabilities() -> dict[str, Any]:
    """Return a compact self-description suitable for an Agent handshake."""

    return {
        "protocol": "facut-agent",
        "protocol_version": "1.0",
        "facut_version": __version__,
        "transport": ["cli-json", "stdio-jsonrpc"],
        "response_contract": {
            "status": ["success", "error"],
            "revisioned": True,
            "stable_error_codes": True,
            "plan_first_actions": [
                name for name, value in _ACTIONS.items() if value.get("plan_first")
            ],
        },
        "actions": [
            {
                "name": name,
                "summary": value["summary"],
                "mutates": value["mutates"],
                "rpc": value["rpc"],
                "schema_uri": f"facut://actions/{name}",
            }
            for name, value in sorted(_ACTIONS.items())
        ],
    }


def action_schema(name: str) -> dict[str, Any]:
    if name not in _ACTIONS:
        raise ValueError(f'Unknown Agent action "{name}".')
    return deepcopy(_ACTIONS[name])
