"""Persistent newline-delimited JSON-RPC session for agents."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from facut import __version__
from facut.agent import action_schema, capabilities
from facut.cli.common import compact_project, manager_for, public_error
from facut.core.command_engine import CommandEngine
from facut.core.sequences import materialize_sequence
from facut.intelligence import (
    apply_story_plan,
    build_narration_plan,
    build_semantic_index,
    build_story_plan,
    diagnose_broll,
    load_semantic_index,
    search_semantic_index,
)
from facut.voice import (
    VoiceProfileStore,
    build_recording_plan,
    provider_status,
    synthesize_with_provider,
    validate_voice_samples,
)


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def _success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        payload["error"]["data"] = data
    return payload


def serve_command(
    ctx: typer.Context,
    handshake: Annotated[
        bool,
        typer.Option("--handshake/--no-handshake", help="Emit a ready event first."),
    ] = True,
) -> None:
    """Keep facut alive and process JSON-RPC requests over stdin/stdout."""

    from facut.cli.main import CliState

    state: CliState = ctx.ensure_object(CliState)
    try:
        manager = manager_for(state)
        document = manager.require_document()
    except Exception as error:
        public = public_error(error)
        _write(
            _error(
                None,
                -32000,
                public.message,
                {"code": public.code, "suggestion": public.suggestion},
            )
        )
        raise typer.Exit(public.exit_code) from error
    engine = CommandEngine(manager)
    if handshake:
        _write(
            {
                "jsonrpc": "2.0",
                "method": "facut.ready",
                "params": {
                    "version": __version__,
                    "project": str(manager.project_file.resolve()),
                    "revision": document.revision,
                    "transport": "stdio-jsonl",
                    "protocol": "facut-agent/1.0",
                    "capabilities_method": "agent.capabilities",
                    "schema_method": "agent.schema",
                },
            }
        )
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id: Any = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Request must be a JSON object.")
            request_id = request.get("id")
            method = request.get("method") or request.get("action")
            if not isinstance(method, str):
                raise ValueError("Request requires method or action.")
            params = dict(request.get("params") or {})
            if not isinstance(params, dict):
                raise ValueError("Request params must be an object.")
            if method == "ping":
                result = {
                    "status": "ok",
                    "version": __version__,
                    "revision": manager.require_document().revision,
                }
            elif method == "shutdown":
                _write(_success(request_id, {"status": "shutdown"}))
                return
            elif method == "project.snapshot":
                result = compact_project(manager.require_document())
            elif method == "agent.capabilities":
                result = capabilities()
            elif method == "agent.schema":
                action = params.get("action")
                if not isinstance(action, str):
                    raise ValueError("agent.schema requires a string action.")
                result = {"name": action, **action_schema(action)}
            elif method == "timeline.show":
                current = compact_project(manager.require_document())
                result = {
                    "duration": current["project"]["duration"],
                    "tracks": current["tracks"],
                    "transitions": current["transitions"],
                    "markers": current["markers"],
                }
            elif method == "run":
                dry_run = bool(params.pop("dry_run", False))
                result = engine.run_batch(params, dry_run=dry_run)
            elif method == "exchange.export":
                from facut.exchange import export_fcpxml, export_otio

                output = Path(str(params["output"]))
                selected = str(params.get("format") or output.suffix.lstrip(".")).casefold()
                current = manager.require_document()
                if params.get("sequence"):
                    current = materialize_sequence(current, str(params["sequence"]))
                if selected == "otio":
                    exchange = export_otio(
                        current,
                        manager.project_dir,
                        output,
                        overwrite=bool(params.get("overwrite", False)),
                    )
                elif selected in {"fcpxml", "xml"}:
                    exchange = export_fcpxml(
                        current,
                        manager.project_dir,
                        output,
                        overwrite=bool(params.get("overwrite", False)),
                    )
                else:
                    raise ValueError("Exchange format must be otio or fcpxml.")
                result = {
                    "status": "success",
                    "command": method,
                    "data": exchange.as_dict(),
                    "warnings": list(exchange.warnings),
                    "errors": [],
                    "project_revision": current.revision,
                }
            elif method == "exchange.import":
                from facut.exchange import apply_import_plan, plan_fcpxml_import, plan_otio_import

                source = Path(str(params["source"]))
                selected = str(params.get("format") or source.suffix.lstrip(".")).casefold()
                if selected == "otio":
                    plan = plan_otio_import(source)
                elif selected in {"fcpxml", "xml"}:
                    plan = plan_fcpxml_import(source)
                else:
                    raise ValueError("Exchange format must be otio or fcpxml.")
                applied = bool(params.get("apply", False))
                current = (
                    apply_import_plan(manager, plan)
                    if applied
                    else manager.require_document()
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": {"plan": plan.as_dict(), "applied": applied},
                    "warnings": plan.warnings,
                    "errors": [],
                    "project_revision": current.revision,
                }
            elif method == "semantic.index":
                data = build_semantic_index(
                    manager.require_document(),
                    manager.project_dir,
                    observations_file=params.get("observations"),
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": data["limitations"],
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "semantic.search":
                data = search_semantic_index(
                    load_semantic_index(manager.project_dir),
                    str(params["query"]),
                    limit=int(params.get("limit", 10)),
                    minimum_score=float(params.get("minimum_score", 0.05)),
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": data["limitations"],
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "story.build":
                plan = build_story_plan(
                    manager.require_document(),
                    load_semantic_index(manager.project_dir),
                    style=str(params.get("style", "travel-documentary")),
                    target_duration=float(params.get("target_duration", 600)),
                )
                applied = bool(params.get("apply", False))
                current = (
                    apply_story_plan(
                        manager, plan, sequence=str(params.get("sequence", "ai-story"))
                    )
                    if applied
                    else manager.require_document()
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": {"plan": plan, "applied": applied},
                    "warnings": plan["warnings"],
                    "errors": [],
                    "project_revision": current.revision,
                }
            elif method == "broll.diagnose":
                try:
                    semantic = load_semantic_index(manager.project_dir)
                except FileNotFoundError:
                    semantic = None
                data = diagnose_broll(
                    manager.require_document(),
                    semantic,
                    maximum_aroll_seconds=float(params.get("maximum_aroll", 8)),
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": data["limitations"],
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "narration.suggest":
                data = build_narration_plan(
                    manager.require_document(),
                    load_semantic_index(manager.project_dir),
                    style=str(params.get("style", "natural-vlog")),
                    language=str(params.get("language", "zh-CN")),
                    max_lines=int(params.get("max_lines", 12)),
                    minimum_confidence=float(params.get("minimum_confidence", 0.55)),
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": [*data["warnings"], *data["limitations"]],
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "narration.synthesize":
                plan_path = Path(str(params["plan"])).expanduser().resolve()
                payload = json.loads(plan_path.read_text(encoding="utf-8"))
                lines = payload.get("lines") or payload.get("data", {}).get("lines")
                if not isinstance(lines, list) or not lines:
                    raise ValueError('Narration plan requires a non-empty "lines" list.')
                store = VoiceProfileStore()
                profile = store.get(str(params["voice_profile"]))
                data = synthesize_with_provider(
                    profile,
                    store.profile_directory(profile.id),
                    lines,
                    str(params["preview_dir"]),
                    provider=params.get("provider"),
                )
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": data.get("warnings", []),
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "voice.profile.create":
                profile = VoiceProfileStore().create(
                    str(params["name"]),
                    speaker_id=str(params["speaker"]),
                    language=str(params.get("language", "zh-CN")),
                    style=str(params.get("style", "natural-vlog")),
                    consent_relationship=str(params["consent"]),
                    consent_statement=str(params["consent_statement"]),
                )
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.list":
                profiles = [item.public_dict() for item in VoiceProfileStore().list()]
                result = {"status": "success", "command": method, "data": {"count": len(profiles), "profiles": profiles}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.import":
                profile = VoiceProfileStore().import_samples(
                    str(params["profile_id"]),
                    [str(item) for item in params["samples"]],
                    transcript=params.get("transcript"),
                )
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.validate":
                store = VoiceProfileStore()
                profile = store.get(str(params["profile_id"]))
                data = validate_voice_samples(
                    store.sample_paths(profile),
                    recommended_total_seconds=float(params.get("recommended_seconds", 600)),
                )
                profile = store.set_status(
                    profile.id,
                    {"pass": "ready", "warning": "warning", "fail": "invalid"}[data["status"]],
                )
                result = {"status": "success", "command": method, "data": {"profile_id": profile.id, "profile_status": profile.status, "report": data}, "warnings": [item["message"] for item in data["issues"]], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.delete":
                if params.get("confirm") is not True:
                    raise ValueError("Voice profile deletion requires confirm=true.")
                profile_id = str(params["profile_id"])
                destination = VoiceProfileStore().delete(profile_id)
                result = {"status": "success", "command": method, "data": {"profile_id": profile_id, "recoverable": True, "trash_name": destination.name}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.restore":
                profile = VoiceProfileStore().restore(str(params["trash_name"]))
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.record-plan":
                store = VoiceProfileStore()
                profile = store.get(str(params["profile_id"]))
                data = build_recording_plan(
                    profile,
                    target_minutes=int(params.get("target_minutes", 10)),
                    script=str(params.get("script", "mandarin-balanced-v1")),
                )
                result = {"status": "success", "command": method, "data": data, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.provider.status":
                data = provider_status(params.get("provider"))
                result = {"status": "success", "command": method, "data": data, "warnings": [] if data["available"] else ["No local voice synthesis provider is available."], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "map.inspect":
                from facut.travel import parse_gpx

                data = parse_gpx(str(params["source"]))
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": [],
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "map.animate":
                from facut.travel import parse_gpx, render_route_video

                route = parse_gpx(str(params["source"]))
                if bool(params.get("dry_run", False)):
                    data = {
                        "route": {key: value for key, value in route.items() if key != "points"},
                        "output": str(Path(str(params["output"])).resolve()),
                        "duration": float(params.get("duration", 8)),
                        "width": int(params.get("width", 1920)),
                        "height": int(params.get("height", 1080)),
                        "fps": int(params.get("fps", 30)),
                        "encoder": str(params.get("encoder", "libx264")),
                        "dry_run": True,
                    }
                else:
                    data = render_route_video(
                        route,
                        str(params["output"]),
                        ffmpeg=state.config.tools.ffmpeg,
                        width=int(params.get("width", 1920)),
                        height=int(params.get("height", 1080)),
                        fps=int(params.get("fps", 30)),
                        duration=float(params.get("duration", 8)),
                        encoder=str(params.get("encoder", "libx264")),
                        overwrite=bool(params.get("overwrite", False)),
                    )
                    data["dry_run"] = False
                result = {
                    "status": "success",
                    "command": method,
                    "data": data,
                    "warnings": data.get("warnings", []),
                    "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "reframe.plan":
                from facut.travel import apply_reframe_plan, build_reframe_plan

                plan = build_reframe_plan(
                    manager,
                    str(params["clip_id"]),
                    str(params["trajectory"]),
                    width=int(params.get("width", 1080)),
                    height=int(params.get("height", 1920)),
                    confidence_threshold=float(params.get("confidence", 0.4)),
                    smoothing=int(params.get("smoothing", 5)),
                )
                applied = bool(params.get("apply", False))
                current = apply_reframe_plan(manager, plan) if applied else manager.require_document()
                result = {
                    "status": "success",
                    "command": method,
                    "data": {"plan": plan, "applied": applied},
                    "warnings": plan["limitations"],
                    "errors": [],
                    "project_revision": current.revision,
                }
            else:
                dry_run = bool(params.pop("dry_run", False))
                result = engine.execute(method, params, dry_run=dry_run)
            if request_id is not None:
                _write(_success(request_id, result))
        except json.JSONDecodeError as error:
            _write(_error(None, -32700, "Parse error", {"detail": str(error)}))
        except Exception as error:
            public = public_error(error)
            _write(
                _error(
                    request_id,
                    -32602,
                    public.message,
                    {
                        "code": public.code,
                        "suggestion": public.suggestion,
                        "details": public.details,
                    },
                )
            )
