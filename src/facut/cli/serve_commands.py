"""Persistent newline-delimited JSON-RPC session for agents."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
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
    generate_narration_plan,
    load_semantic_index,
    save_narration_plan,
    search_semantic_index,
)
from facut.recipe import RecipeEngine, load_recipe
from facut.voice import (
    VoiceProfileStore,
    build_recording_plan,
    provider_status,
    validate_voice_profile,
)
from facut.voice.say import synthesize_voice_say
from facut.voice.service import voice_service_status


def _write(payload: dict[str, Any]) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def _configure_stdio_utf8() -> None:
    """Keep the Agent JSONL transport UTF-8 on Windows pipes and consoles."""

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def _success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    if data is not None:
        payload["error"]["data"] = data
    return payload


def _validate_declared_params(method: str, params: dict[str, Any]) -> None:
    """Reject RPC parameters that are outside an action's published contract."""

    schema = action_schema(method)["parameters"]
    properties = set(schema.get("properties", {}))
    unknown = sorted(set(params) - properties)
    if unknown:
        raise ValueError(
            f"{method} received unknown parameter(s): {', '.join(unknown)}."
        )
    missing = sorted(set(schema.get("required", [])) - set(params))
    if missing:
        raise ValueError(
            f"{method} requires parameter(s): {', '.join(missing)}."
        )


def serve_command(
    ctx: typer.Context,
    handshake: Annotated[
        bool,
        typer.Option("--handshake/--no-handshake", help="Emit a ready event first."),
    ] = True,
) -> None:
    """Keep facut alive and process JSON-RPC requests over stdin/stdout."""

    from facut.cli.main import CliState

    _configure_stdio_utf8()
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
            if method in {
                "voice.say", "voice.sample.propose", "voice.sample.list",
                "voice.sample.show", "voice.sample.approve", "voice.sample.reject",
                "voice.profile.sample.remove",
                "narration.synthesize", "vlog.prepare",
                "vlog.inspect.next", "vlog.observe", "vlog.status", "vlog.plan",
                "vlog.atlas.build", "vlog.atlas.status", "vlog.inspect.batch",
                "vlog.observe.batch",
                "vlog.story.brief", "vlog.story.submit", "vlog.story.validate",
                "vlog.opening.plan", "vlog.ending.plan", "vlog.continuity.check",
                "vlog.review.create", "vlog.review.submit", "vlog.review.plan",
                "vlog.review.apply", "vlog.soundscape.analyze",
                "vlog.soundscape.plan", "vlog.soundscape.apply",
                "vlog.compare", "vlog.refine", "subtitle.transcribe", "subtitle.apply",
                "vlog.inbox.next", "vlog.inbox.list", "vlog.inbox.resolve",
                "vlog.bible.show", "vlog.bible.import",
                "subtitle.glossary.add", "typography.plan", "typography.apply",
                "font.scan", "font.register", "font.match", "font.audit",
                "vlog.preview", "vlog.build", "library.music.add", "library.sfx.add",
                "library.search", "library.audit", "style.list", "style.describe",
                "style.validate",
                "native.doctor", "analyze.batch",
                "runtime.status", "runtime.cleanram", "runtime.warmup",
                "runtime.autoload.configure",
            }:
                _validate_declared_params(method, params)
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
            elif method == "native.doctor":
                from facut.native import NativeClient, discover_native

                native_path = discover_native()
                data = NativeClient(native_path).doctor()
                data["executable"] = str(native_path) if native_path else None
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "runtime.status":
                from facut.runtime_control import autoload_status

                result = {
                    "status": "success", "command": method,
                    "data": autoload_status(), "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "runtime.cleanram":
                from facut.runtime_control import clean_services

                result = {
                    "status": "success", "command": method,
                    "data": clean_services(
                        params["services"], dry_run=bool(params.get("dry_run", False))
                    ),
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "runtime.warmup":
                from facut.runtime_control import warm_services

                result = {
                    "status": "success", "command": method,
                    "data": warm_services(params["services"]),
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "runtime.autoload.configure":
                from facut.runtime_control import clean_services, configure_autoload

                data = configure_autoload(
                    str(params["service"]), enabled=bool(params["enabled"])
                )
                if not params["enabled"] and bool(params.get("stop_now", False)):
                    data["stop"] = clean_services([str(params["service"])])
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "analyze.batch":
                from facut.analysis import analyze_quality
                from facut.native import (
                    NativeClient,
                    batch_failure_warnings,
                    collect_batch_inputs,
                    compact_batch_result,
                    discover_native,
                )

                engine_name = str(params.get("engine", "auto"))
                mode = str(params.get("mode", "fast"))
                folder = Path(str(params["folder"])).expanduser().resolve()
                output_directory = Path(str(
                    params.get("output_directory") or folder / ".facut-native"
                )).expanduser().resolve()
                inputs, collection = collect_batch_inputs(
                    folder,
                    output_directory=output_directory,
                    limit=int(params["limit"]) if params.get("limit") is not None else None,
                )
                files = [Path(str(item["original_path"])) for item in inputs]
                jobs = int(params.get("jobs", 3))
                asset_timeout = float(params.get("asset_timeout", 180.0))
                native_path = discover_native() if engine_name != "python" else None
                warnings = []
                if native_path:
                    try:
                        full_data = NativeClient(native_path).batch_scan(
                            inputs,
                            output_directory=output_directory,
                            mode=mode,
                            jobs=jobs,
                            asset_timeout_seconds=asset_timeout,
                        )
                        data = compact_batch_result(full_data)
                        data.update({
                            "engine": "native", "asset_count": len(files),
                            "collection": collection, "jobs": jobs,
                            "asset_timeout_seconds": asset_timeout,
                        })
                        warnings.extend(batch_failure_warnings(data))
                    except Exception as error:
                        if engine_name == "native":
                            raise
                        warnings.append(
                            "FACUT Native failed twice; Python/FFmpeg fallback was used: "
                            f"{error}"
                        )
                        native_path = None
                elif engine_name == "native":
                    NativeClient()
                if not native_path and engine_name != "native":
                    results = []
                    failures = []
                    for path in files:
                        try:
                            results.append(
                                analyze_quality(
                                    path,
                                    ffmpeg=state.config.tools.ffmpeg,
                                    ffprobe=state.config.tools.ffprobe,
                                )
                            )
                        except Exception as error:
                            failure = {
                                "source": str(path),
                                "status": "error",
                                "error": str(error),
                            }
                            results.append(failure)
                            failures.append(failure)
                    data = {
                        "engine": "python",
                        "asset_count": len(files),
                        "succeeded": len(files) - len(failures),
                        "failed": len(failures),
                        "failures": failures[:50],
                        "results": results,
                        "collection": collection,
                    }
                    warnings.extend(batch_failure_warnings(data))
                    if engine_name == "auto" and not warnings:
                        warnings.append("FACUT Native was unavailable; Python/FFmpeg fallback was used.")
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": warnings, "errors": [],
                    "project_revision": manager.require_document().revision,
                }
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
            elif method in {
                "timeline.track.add", "timeline.add", "audio.add", "audio.volume",
                "audio.fade", "clip.move", "clip.duplicate", "clip.transform",
                "clip.motion", "clip.freeze", "clip.composite", "clip.split",
                "clip.trim", "clip.delete", "clip.speed", "clip.speed_curve",
                "effect.add", "effect.remove", "adjustment.add", "audio.process",
                "audio.crossfade", "audio.loudness", "transition.add", "transition.remove",
            }:
                dry_run = bool(params.pop("dry_run", False))
                if not dry_run:
                    manager.ensure_experiment_branch("agent")
                    params.setdefault("_actor", "agent")
                    params.setdefault("_intent", f"Agent RPC {method}")
                result = engine.execute(method, params, dry_run=dry_run)
            elif method in {"proxy.scan", "proxy.link_auto"}:
                from facut.media.proxy_manager import ProxyManager

                service = ProxyManager(
                    manager,
                    ffmpeg=state.config.tools.ffmpeg,
                    ffprobe=state.config.tools.ffprobe,
                )
                if method == "proxy.scan":
                    data, current = service.scan(
                        params.get("media_id"),
                        search_directories=[Path(str(item)) for item in params.get("search", [])],
                        link=bool(params.get("link", False)),
                        dry_run=bool(params.get("dry_run", False)),
                    )
                    payload = {"results": data, "link": bool(params.get("link", False))}
                else:
                    asset, current, matched = service.relink(
                        str(params["media_id"]),
                        Path(str(params["search"])) if params.get("search") else None,
                        dry_run=bool(params.get("dry_run", False)),
                    )
                    payload = {
                        "media": asset.model_dump(mode="json"),
                        "matched": str(matched),
                    }
                result = {
                    "status": "success", "command": method, "data": payload,
                    "warnings": [], "errors": [], "project_revision": current.revision,
                }
            elif method == "history.status":
                current = manager.require_document()
                manager.cutgraph.initialize(current)
                result = {
                    "status": "success", "command": method,
                    "data": manager.cutgraph.status(), "warnings": [], "errors": [],
                    "project_revision": current.revision,
                }
            elif method == "history.diff":
                data = manager.diff_history(str(params["before"]), str(params["after"]))
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "history.restore":
                current = manager.restore_commit(str(params["commit"]))
                result = {
                    "status": "success", "command": method,
                    "data": {"commit": params["commit"], "revision": current.revision},
                    "warnings": [], "errors": [], "project_revision": current.revision,
                }
            elif method == "branch.create":
                current = manager.require_document()
                manager.cutgraph.initialize(current)
                data = manager.cutgraph.create_branch(
                    str(params["name"]), start=str(params.get("start", "HEAD"))
                )
                if bool(params.get("switch", False)):
                    current = manager.switch_branch(str(params["name"]))
                    data["current"] = True
                result = {"status": "success", "command": method, "data": data, "warnings": [], "errors": [], "project_revision": current.revision}
            elif method == "branch.switch":
                current = manager.switch_branch(str(params["name"]))
                result = {"status": "success", "command": method, "data": manager.cutgraph.status(), "warnings": [], "errors": [], "project_revision": current.revision}
            elif method == "branch.accept":
                data, current = manager.accept_branch(
                    str(params["source"]), str(params.get("target", "main"))
                )
                result = {"status": "success", "command": method, "data": data, "warnings": [], "errors": [], "project_revision": current.revision}
            elif method == "preview.compare":
                from facut.render.compare import render_compare_previews
                from facut.render.ffmpeg_backend import FFmpegBackend

                data = render_compare_previews(
                    manager,
                    FFmpegBackend(state.config.tools.ffmpeg),
                    before=str(params["before"]),
                    after=str(params["after"]),
                    output_dir=str(params["output_dir"]),
                    changed_only=bool(params.get("changed_only", True)),
                    padding=float(params.get("padding", 0.5)),
                    height=int(params.get("height", 360)),
                    fps=float(params.get("fps", 24)),
                    hardware=str(params.get("hardware", "auto")),
                    overwrite=bool(params.get("overwrite", False)),
                )
                result = {"status": "success", "command": method, "data": data, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "qc.run":
                from facut.qc.engine import QCEngine, resolve_qc_scope

                target = Path(str(params["target"]))
                scope, sources = resolve_qc_scope(target, project=state.project)
                qc_report = QCEngine(
                    ffmpeg=state.config.tools.ffmpeg,
                    ffprobe=state.config.tools.ffprobe,
                    expected_duration=params.get("expected_duration"),
                ).run(
                    scope,
                    sources,
                    contact_sheet=Path(str(params["contact_sheet"])) if params.get("contact_sheet") else None,
                    overwrite=bool(params.get("overwrite", False)),
                )
                result = {"status": "success", "command": method, "data": qc_report.model_dump(mode="json"), "warnings": qc_report.warnings, "errors": [], "project_revision": manager.require_document().revision}
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
                from facut.cli.intelligence_commands import synthesize_narration_previews

                data = synthesize_narration_previews(
                    str(params["plan"]),
                    voice=str(params["voice"]),
                    preview_dir=str(params["preview_dir"]),
                    style=str(params.get("style", "auto")),
                    takes=int(params.get("takes", 1)),
                    speed=float(params.get("speed", 1.0)),
                    intensity=float(params.get("intensity", 0.5)),
                    instruction=params.get("instruction"),
                    device=str(params.get("device", "auto")),
                    require_cuda=bool(params.get("require_cuda", False)),
                    use_service=bool(params.get("use_service", True)),
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
            elif method == "narration.generate":
                from facut.vlog import load_trip_bible, trip_bible_fact_policy

                output = Path(str(params["output"])).expanduser().resolve()
                if output.exists() and not bool(params.get("overwrite", False)):
                    raise FileExistsError(f'Output "{output}" already exists; use overwrite=true.')
                plan = generate_narration_plan(
                    manager.require_document(),
                    load_semantic_index(manager.project_dir),
                    style=str(params.get("style", "weekend-vlog")),
                    language=str(params.get("language", "zh-CN")),
                    provider=str(params.get("provider", "deterministic")),
                    max_lines=int(params.get("max_lines", 12)),
                    minimum_confidence=float(params.get("minimum_confidence", 0.55)),
                    fact_policy=trip_bible_fact_policy(
                        load_trip_bible(
                            manager.project_dir,
                            default_name=manager.require_document().project.name,
                        )
                    ),
                )
                save_narration_plan(plan, output)
                data = plan.model_dump(mode="json")
                data["output"] = str(output)
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [*plan.warnings, *plan.limitations], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method in {
                "vlog.prepare", "vlog.inspect.next", "vlog.observe", "vlog.status",
                "vlog.atlas.build", "vlog.atlas.status", "vlog.inspect.batch",
                "vlog.observe.batch",
                "vlog.plan", "vlog.compare", "vlog.refine",
                "vlog.story.brief", "vlog.story.submit", "vlog.story.validate",
                "vlog.opening.plan", "vlog.ending.plan", "vlog.continuity.check",
                "vlog.review.create", "vlog.review.submit", "vlog.review.plan",
                "vlog.review.apply", "vlog.soundscape.analyze",
                "vlog.soundscape.plan", "vlog.soundscape.apply",
                "vlog.inbox.next", "vlog.inbox.list", "vlog.inbox.resolve",
                "vlog.bible.show", "vlog.bible.import",
            }:
                from facut.media.proxy_manager import ProxyManager
                from facut.vlog import (
                    build_story_candidates,
                    compare_story_candidates,
                    director_status,
                    ingest_observations,
                    import_source_resumable,
                    load_trip_bible,
                    next_inspection_task,
                    next_inbox_items,
                    prepare_evidence_manifest,
                    rebuild_director_inbox,
                    refine_story_candidate,
                    resolve_inbox_item,
                    save_trip_bible,
                )
                from facut.vlog.labs import (
                    build_story_brief,
                    check_continuity,
                    plan_endings,
                    plan_openings,
                    submit_story_proposal,
                    validate_story_plan,
                )
                from facut.vlog.atlas import (
                    build_scene_atlas,
                    ingest_atlas_observations,
                    next_atlas_inspection_batch,
                    scene_atlas_status,
                )
                from facut.vlog.review import (
                    apply_review,
                    create_review,
                    plan_review,
                    submit_review,
                )
                from facut.vlog.soundscape import (
                    analyze_soundscape,
                    apply_soundscape_plan,
                    plan_soundscape,
                )

                if method == "vlog.prepare":
                    native_warnings = []
                    source_value = params.get("source")
                    if source_value:
                        source = Path(str(source_value)).expanduser().resolve()
                        import_source_resumable(manager, source)
                    else:
                        source = None
                    if params.get("proxy", "auto") == "auto":
                        ProxyManager(
                            manager,
                            ffmpeg=state.config.tools.ffmpeg,
                            ffprobe=state.config.tools.ffprobe,
                        ).scan(search_directories=[source] if source else None, link=True)
                    engine_name = str(params.get("engine", "auto"))
                    native_mode = str(params.get("native_mode", "fast"))
                    native_results = None
                    if engine_name != "python":
                        from facut.native import NativeClient, discover_native, scan_project_media

                        native_path = discover_native()
                        if native_path:
                            try:
                                native_payload = scan_project_media(
                                    manager, mode=native_mode, executable=native_path
                                )
                                native_results = {
                                    item["media_id"]: item
                                    for item in native_payload.get("results", [])
                                    if item.get("media_id")
                                }
                            except Exception as error:
                                if engine_name == "native":
                                    raise
                                native_warnings.append(
                                    "FACUT Native failed twice; vlog preparation used the "
                                    f"Python/FFmpeg path: {error}"
                                )
                        elif engine_name == "native":
                            NativeClient()
                    data = prepare_evidence_manifest(
                        manager,
                        ffmpeg=state.config.tools.ffmpeg,
                        generate_frames=bool(params.get("frames", True)),
                        batch_size=int(params.get("batch_size", 12)),
                        native_results=native_results,
                    )
                    data["scene_atlas"] = build_scene_atlas(
                        manager, batch_size=int(params.get("batch_size", 12))
                    )
                    data["analysis_engine"] = "native" if native_results is not None else "python"
                    if native_warnings:
                        data["warnings"] = [*data.get("warnings", []), *native_warnings]
                elif method == "vlog.inspect.next":
                    data = next_inspection_task(manager.project_dir)
                elif method == "vlog.atlas.build":
                    data = build_scene_atlas(
                        manager, batch_size=int(params.get("batch_size", 12))
                    )
                elif method == "vlog.atlas.status":
                    data = scene_atlas_status(manager.project_dir)
                elif method == "vlog.inspect.batch":
                    data = next_atlas_inspection_batch(
                        manager.project_dir, task_id=params.get("task_id")
                    )
                elif method == "vlog.observe.batch":
                    payload = {
                        "observations": params["observations"],
                        "asset_hashes": params.get("asset_hashes"),
                    }
                    data = ingest_atlas_observations(
                        manager.require_document(),
                        manager.project_dir,
                        payload,
                        task_id=params.get("task_id"),
                    )
                elif method == "vlog.inbox.next":
                    data = next_inbox_items(
                        manager.project_dir, limit=int(params.get("limit", 8))
                    )
                elif method == "vlog.inbox.list":
                    data = rebuild_director_inbox(manager.project_dir)
                elif method == "vlog.inbox.resolve":
                    data = resolve_inbox_item(
                        manager.project_dir, str(params["item_id"]),
                        resolution=str(params["resolution"]),
                    )
                elif method == "vlog.bible.show":
                    data = load_trip_bible(
                        manager.project_dir,
                        default_name=manager.require_document().project.name,
                    ).model_dump(mode="json")
                elif method == "vlog.bible.import":
                    data = save_trip_bible(manager.project_dir, dict(params["bible"]))
                    data["director_inbox"] = rebuild_director_inbox(manager.project_dir)
                elif method == "vlog.observe":
                    data = ingest_observations(
                        manager.require_document(),
                        manager.project_dir,
                        {"observations": params["observations"]},
                        task_id=params.get("task_id"),
                    )
                elif method == "vlog.status":
                    data = director_status(manager.project_dir, manager.require_document())
                elif method == "vlog.plan":
                    data = build_story_candidates(
                        manager.require_document(),
                        manager.project_dir,
                        style=str(params.get("style", "natural-vlog")),
                        target_duration=float(params.get("target_duration", 480)),
                    ).model_dump(mode="json")
                elif method == "vlog.story.brief":
                    data = build_story_brief(manager.project_dir)
                elif method == "vlog.story.submit":
                    data = submit_story_proposal(
                        manager.project_dir, dict(params["proposal"])
                    ).model_dump(mode="json")
                elif method == "vlog.story.validate":
                    data = validate_story_plan(manager.project_dir)
                elif method == "vlog.opening.plan":
                    data = plan_openings(manager.project_dir)
                elif method == "vlog.ending.plan":
                    data = plan_endings(manager.project_dir)
                elif method == "vlog.continuity.check":
                    data = check_continuity(
                        manager.project_dir, params.get("candidate_id")
                    )
                elif method == "vlog.review.create":
                    data = create_review(
                        manager, str(params.get("review_pass", "story"))
                    )
                elif method == "vlog.review.submit":
                    data = submit_review(manager, dict(params["submission"]))
                elif method == "vlog.review.plan":
                    data = plan_review(manager, dict(params["submission"]))
                elif method == "vlog.review.apply":
                    data = apply_review(
                        manager,
                        dict(params["plan"]),
                        approved_only=bool(params.get("approved_only", True)),
                    )
                elif method == "vlog.soundscape.analyze":
                    data = analyze_soundscape(manager)
                elif method == "vlog.soundscape.plan":
                    data = plan_soundscape(
                        manager,
                        style=str(params.get("style", "natural-vlog")),
                        target_lufs=float(params.get("target_lufs", -14.0)),
                        true_peak_db=float(params.get("true_peak_db", -1.0)),
                    )
                elif method == "vlog.soundscape.apply":
                    data = apply_soundscape_plan(
                        manager,
                        dict(params["plan"]),
                        approved_only=bool(params.get("approved_only", True)),
                    )
                elif method == "vlog.compare":
                    data = compare_story_candidates(manager.project_dir)
                else:
                    data = refine_story_candidate(
                        manager.project_dir, str(params["candidate_id"])
                    ).model_dump(mode="json")
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": data.get("warnings", []) if isinstance(data, dict) else [],
                    "errors": [], "project_revision": manager.require_document().revision,
                }
            elif method in {"vlog.apply", "vlog.preview", "vlog.build"}:
                from facut.cli.vlog_commands import _build_and_render
                from facut.render import FFmpegBackend
                from facut.vlog import candidate_document, compare_story_candidates

                if method == "vlog.apply":
                    applied = CommandEngine(manager).run_batch(
                        {
                            "version": "1.0",
                            "atomic": True,
                            "actor": "agent",
                            "intent": "Apply VLOG StoryGraph candidate",
                            "commands": [
                                {
                                    "action": "vlog.apply",
                                    "candidate_id": str(params["candidate_id"]),
                                    "preset": str(params.get("preset", "youtube-4k")),
                                }
                            ],
                        }
                    )
                    data = applied["data"]
                elif method == "vlog.preview":
                    comparison = compare_story_candidates(manager.project_dir)
                    candidate_ids = [item["id"] for item in comparison["candidates"]]
                    selected_ids = (
                        candidate_ids
                        if bool(params.get("all_candidates", False))
                        else [str(params.get("candidate_id") or candidate_ids[0])]
                    )
                    outputs = []
                    backend = FFmpegBackend(state.config.tools.ffmpeg)
                    output_dir = Path(str(params["output_dir"])).expanduser().resolve()
                    for candidate_id in selected_ids:
                        document, candidate = candidate_document(
                            manager.require_document(), manager.project_dir, candidate_id
                        )
                        rendered = backend.preview_range(
                            document,
                            manager.project_dir,
                            output_dir / f"{candidate_id}.mp4",
                            start=0,
                            end=document.project.duration,
                            height=540,
                            fps=24,
                            overwrite=bool(params.get("overwrite", False)),
                        )
                        outputs.append(
                            {"candidate_id": candidate.id, "output": str(rendered.output), "duration": rendered.duration}
                        )
                    data = {"outputs": outputs, "saved_timeline_changed": False}
                else:
                    data, rendered = _build_and_render(
                        manager,
                        candidate_id=str(params["candidate_id"]),
                        output=Path(str(params["output"])),
                        preset=str(params.get("preset", "youtube-4k")),
                        hardware=str(params.get("hardware", "auto")),
                        overwrite=bool(params.get("overwrite", False)),
                        ffmpeg=state.config.tools.ffmpeg,
                    )
                    data["warnings"] = rendered.warnings
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": data.pop("warnings", []), "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method in {"font.scan", "font.register", "font.match", "font.audit"}:
                from facut.fonts import FontCatalog

                catalog = FontCatalog()
                if method == "font.scan":
                    data = catalog.scan(refresh=bool(params.get("refresh", False)))
                elif method == "font.register":
                    data = catalog.register(
                        str(params["font_file"]), license_file=str(params["license_file"])
                    )
                elif method == "font.match":
                    data = catalog.match(
                        str(params["role"]), language=str(params.get("language", "zh-CN"))
                    )
                else:
                    data = catalog.audit_project(
                        manager.require_document(), language=str(params.get("language", "zh-CN"))
                    )
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method in {
                "library.music.add", "library.sfx.add", "library.search", "library.audit",
                "style.list", "style.describe", "style.validate",
            }:
                from facut.library import MediaLibrary
                from facut.styles import describe_style, list_styles, validate_style_usage

                if method in {"library.music.add", "library.sfx.add"}:
                    kind = "music" if method == "library.music.add" else "sfx"
                    data = MediaLibrary().add(
                        str(params["source"]),
                        kind=kind,
                        tags=list(map(str, params.get("tags", []))),
                        moods=list(map(str, params.get("moods", []))),
                        platforms=list(map(str, params.get("platforms", []))),
                        license_file=params.get("license_file"),
                        ffmpeg=state.config.tools.ffmpeg,
                        ffprobe=state.config.tools.ffprobe,
                        analyze=bool(params.get("analyze", kind == "music")),
                    )
                elif method == "library.search":
                    data = MediaLibrary().search(
                        kind=params.get("kind"), mood=params.get("mood"),
                        tag=params.get("tag"), platform=params.get("platform"),
                    )
                elif method == "library.audit":
                    data = MediaLibrary().audit(str(params["platform"]))
                elif method == "style.list":
                    data = list_styles()
                elif method == "style.describe":
                    data = describe_style(str(params["name"]))
                else:
                    data = validate_style_usage(manager.require_document(), str(params["name"]))
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method in {"typography.plan", "typography.apply"}:
                from facut.subtitles import apply_typography_plan, build_typography_plan

                if method == "typography.plan":
                    data = build_typography_plan(
                        manager.project_dir, language=str(params.get("language", "zh-CN"))
                    )
                else:
                    data = apply_typography_plan(manager, params.get("plan"))
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "subtitle.glossary.add":
                from facut.subtitles import add_glossary_entry

                data = add_glossary_entry(
                    manager.project_dir, str(params["term"]), str(params.get("type", "term"))
                )
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "subtitle.transcribe":
                from facut.analysis.engine import transcribe_local
                from facut.cli.subtitle_commands import _diarize
                from facut.subtitles import build_transcript_plan, save_transcript_plan

                destination = Path(str(params["output"])).expanduser().resolve()
                if destination.exists() and not bool(params.get("overwrite", False)):
                    raise FileExistsError(f'Output "{destination}" already exists; use overwrite.')
                document = manager.require_document()
                selected_ids = set(map(str, params.get("media", []))) or {
                    clip.media_id
                    for track in document.tracks
                    if track.type.value in {"video", "audio"}
                    for clip in track.clips
                    if clip.enabled
                }
                model_path = (
                    Path(str(params["model"]))
                    if params.get("model")
                    else state.config.models.resolve("srt_model")
                )
                transcripts = {}
                for media_id in sorted(selected_ids):
                    asset = document.find_media(media_id)
                    if asset is None:
                        raise ValueError(f'Unknown media "{media_id}".')
                    transcripts[media_id] = transcribe_local(
                        manager.resolve_path(asset.path),
                        model_path=model_path,
                        language=str(params.get("language", "zh")),
                        external_python=state.config.tools.analysis_python,
                        word_timestamps=bool(params.get("word_timestamps", True)),
                    )
                diarization = "disabled"
                if bool(params.get("speaker_diarization", False)):
                    transcripts = _diarize(transcripts)
                    diarization = "provider"
                plan = build_transcript_plan(
                    document,
                    manager.project_dir,
                    transcripts,
                    language=str(params.get("language", "zh")),
                    model=Path(model_path).name,
                    word_timestamps=bool(params.get("word_timestamps", True)),
                    speaker_diarization=diarization,
                )
                save_transcript_plan(plan, destination)
                data = plan.model_dump(mode="json")
                data["output"] = str(destination)
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": plan.warnings, "errors": [],
                    "project_revision": document.revision,
                }
            elif method == "subtitle.apply":
                from facut.subtitles import apply_transcript_plan, load_transcript_plan

                data = apply_transcript_plan(
                    manager,
                    load_transcript_plan(str(params["plan"])),
                    approved_only=bool(params.get("approved_only", True)),
                    track_id=str(params.get("track", "S_DIALOGUE")),
                )
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": [], "errors": [],
                    "project_revision": data["project_revision"],
                }
            elif method == "narration.apply":
                data = CommandEngine(manager).execute(
                    "narration.apply",
                    {
                        "plan_path": str(params["plan"]),
                        "approved_only": bool(params.get("approved_only", True)),
                        "duck_music": bool(params.get("duck_music", False)),
                        "preserve_original": bool(params.get("preserve_original", True)),
                        "track_id": str(params.get("track", "A_NARRATION")),
                        "allow_stale": bool(params.get("allow_stale", False)),
                    },
                    dry_run=bool(params.get("dry_run", False)),
                )
                result = data
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
            elif method == "voice.profile.rename":
                profile = VoiceProfileStore().rename(str(params["profile"]), str(params["name"]))
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.alias.set":
                profile = VoiceProfileStore().set_alias(str(params["profile"]), str(params["alias"]))
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.default.set":
                scope = str(params.get("scope", "project"))
                profile = VoiceProfileStore().set_default(
                    str(params["profile"]),
                    scope=scope,
                    project=manager.project_dir if scope == "project" else None,
                )
                result = {"status": "success", "command": method, "data": {"scope": scope, "profile": profile.public_dict()}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.serve.status":
                data = voice_service_status()
                result = {"status": "success", "command": method, "data": data, "warnings": [] if data.get("running") else ["The local voice service is not running."], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.say":
                store = VoiceProfileStore()
                profile = (
                    store.resolve(str(params["voice"]))
                    if params.get("voice")
                    else store.get_default(project=manager.project_dir)
                )
                if profile is None:
                    raise ValueError("voice.say requires voice or a configured project/global default.")
                destination = Path(str(params["output"])).expanduser().resolve()
                takes = int(params.get("takes", 1))
                outputs = [
                    destination if takes == 1 else destination.with_name(f"{destination.stem}-take-{index + 1}{destination.suffix}")
                    for index in range(takes)
                ]
                if any(path.exists() for path in outputs) and not bool(params.get("overwrite", False)):
                    raise FileExistsError("A voice.say output already exists; use overwrite=true.")
                data = synthesize_voice_say(
                    profile,
                    store.profile_directory(profile.id),
                    str(params["text"]),
                    destination.parent / ".facut-voice-cache",
                    style=str(params.get("style", "auto")),
                    takes=takes,
                    speed=float(params.get("speed", 1.0)),
                    intensity=float(params.get("intensity", 0.5)),
                    instruction=params.get("instruction"),
                    purpose=params.get("purpose"),
                    provider=params.get("provider"),
                    use_service=bool(params.get("use_service", True)),
                    service_options={
                        "device": str(params.get("device", "auto")),
                        "require_cuda": bool(params.get("require_cuda", False)),
                    },
                )
                if bool(params.get("verify", False)):
                    from facut.analysis.engine import transcribe_local
                    from facut.voice.verification import verify_outputs

                    model_path = state.config.models.resolve("srt_model")
                    if not model_path.is_dir():
                        raise FileNotFoundError(
                            "TTS verification requires srt_model. Run `facut download srt_model`."
                        )
                    reports = verify_outputs(
                        [item["output"] for item in data["outputs"]],
                        str(params["text"]),
                        required_entities=[str(item) for item in params.get("verify_entity", [])],
                        minimum_similarity=float(params.get("verify_min_similarity", 0.70)),
                        transcribe=lambda path: transcribe_local(
                            path,
                            model_path=model_path,
                            language="zh",
                            external_python=state.config.tools.analysis_python,
                        ),
                    )
                    data["verification"] = reports
                    if any(not item["passed"] for item in reports):
                        raise ValueError(
                            "TTS_ASR_MISMATCH: synthesized narration did not match this request; "
                            "no final output was written."
                        )
                destination.parent.mkdir(parents=True, exist_ok=True)
                for item, final_path in zip(data["outputs"], outputs, strict=True):
                    generated = Path(str(item["output"])).resolve()
                    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix=".wav") as stream:
                        temporary = Path(stream.name)
                    try:
                        shutil.copy2(generated, temporary)
                        os.replace(temporary, final_path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    item["output"] = str(final_path)
                result = {"status": "success", "command": method, "data": data, "warnings": data.get("warnings", []), "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.sample.propose":
                store = VoiceProfileStore()
                candidate = store.propose_candidate(
                    str(params["profile"]),
                    str(params["source"]),
                    transcript=params.get("transcript"),
                    category=params.get("category"),
                    delivery=params.get("delivery"),
                    source_media_id=params.get("source_media_id"),
                    source_start=params.get("source_start"),
                    source_end=params.get("source_end"),
                    identity_basis=str(params.get("identity_basis", "similarity")),
                )
                result = {"status": "success", "command": method, "data": {**candidate.public_dict(), "synthesis_eligible": False}, "warnings": ["Candidate audio is quarantined until the speaker is manually confirmed."], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.sample.remove":
                if not bool(params.get("confirm", False)):
                    raise ValueError("Voice sample removal requires confirm=true.")
                sample, trash_name = VoiceProfileStore().remove_sample(
                    str(params["profile"]), str(params["sample_id"])
                )
                result = {
                    "status": "success", "command": method,
                    "data": {"sample_id": sample.id, "sha256": sample.sha256,
                             "recoverable": True, "trash_name": trash_name,
                             "derived_cache_cleared": True},
                    "warnings": [], "errors": [],
                    "project_revision": manager.require_document().revision,
                }
            elif method == "voice.sample.list":
                store = VoiceProfileStore()
                candidates = store.list_candidates(params.get("profile"))
                if params.get("status") is not None:
                    candidates = [item for item in candidates if item.status == params["status"]]
                result = {"status": "success", "command": method, "data": {"count": len(candidates), "candidates": [item.public_dict() for item in candidates]}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.sample.show":
                candidate = VoiceProfileStore().get_candidate(str(params["candidate_id"]))
                result = {"status": "success", "command": method, "data": candidate.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.sample.approve":
                candidate, profile = VoiceProfileStore().approve_candidate(
                    str(params["candidate_id"]),
                    speaker_confirmed=bool(params["speaker_confirmed"]),
                    confirmation_statement=str(params["confirmation_statement"]),
                )
                result = {"status": "success", "command": method, "data": {"candidate": candidate.public_dict(), "profile_id": profile.id, "sample_count": len(profile.samples), "synthesis_eligible": True}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.sample.reject":
                candidate = VoiceProfileStore().reject_candidate(
                    str(params["candidate_id"]), reason=str(params["reason"])
                )
                result = {"status": "success", "command": method, "data": candidate.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.import":
                store = VoiceProfileStore()
                profile = store.import_samples(
                    store.resolve(str(params["profile_id"])).id,
                    [str(item) for item in params["samples"]],
                    transcript=params.get("transcript"),
                    speaker_similarity=(
                        float(params["speaker_similarity"])
                        if params.get("speaker_similarity") is not None
                        else None
                    ),
                    speaker_confirmed=bool(params.get("speaker_confirmed", False)),
                    confirmation_statement=params.get("confirmation_statement"),
                )
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.validate":
                store = VoiceProfileStore()
                profile = store.resolve(str(params["profile_id"]))
                data = validate_voice_profile(
                    profile,
                    store.sample_paths(profile),
                    recommended_total_seconds=float(params.get("recommended_seconds", 120)),
                )
                profile = store.set_status(
                    profile.id,
                    (
                        "invalid"
                        if data["status"] == "fail"
                        else "ready"
                        if data["profile_assessment"]["synthesis_usable"]
                        else "warning"
                    ),
                )
                result = {"status": "success", "command": method, "data": {"profile_id": profile.id, "profile_status": profile.status, "report": data}, "warnings": [item["message"] for item in data["issues"]], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.delete":
                if params.get("confirm") is not True:
                    raise ValueError("Voice profile deletion requires confirm=true.")
                store = VoiceProfileStore()
                profile_id = store.resolve(str(params["profile_id"])).id
                destination = store.delete(profile_id)
                result = {"status": "success", "command": method, "data": {"profile_id": profile_id, "recoverable": True, "trash_name": destination.name}, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.profile.restore":
                profile = VoiceProfileStore().restore(str(params["trash_name"]))
                result = {"status": "success", "command": method, "data": profile.public_dict(), "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.record-plan":
                store = VoiceProfileStore()
                profile = store.resolve(str(params["profile_id"]))
                data = build_recording_plan(
                    profile,
                    target_minutes=int(params.get("target_minutes", 10)),
                    script=str(params.get("script", "mandarin-balanced-v1")),
                )
                result = {"status": "success", "command": method, "data": data, "warnings": [], "errors": [], "project_revision": manager.require_document().revision}
            elif method == "voice.provider.status":
                data = provider_status(params.get("provider"))
                result = {"status": "success", "command": method, "data": data, "warnings": [] if data["available"] else ["No local voice synthesis provider is available."], "errors": [], "project_revision": manager.require_document().revision}
            elif method in {"recipe.validate", "recipe.plan", "recipe.build"}:
                source = Path(str(params["source"])).expanduser().resolve()
                recipe = load_recipe(source)
                recipe_engine = RecipeEngine(manager)
                if method == "recipe.validate":
                    data = recipe_engine.validate(recipe, recipe_path=source)
                elif method == "recipe.plan":
                    from facut.cli.recipe_commands import _write_json_atomic

                    plan = recipe_engine.plan(recipe, recipe_path=source)
                    data = plan.as_dict()
                    destination = _write_json_atomic(
                        Path(str(params["output"])), data, overwrite=bool(params.get("overwrite", False))
                    )
                    data["output"] = str(destination)
                else:
                    data = recipe_engine.build(
                        recipe,
                        recipe_path=source,
                        output=params.get("output"),
                        dry_run=bool(params.get("dry_run", False)),
                    )
                result = {
                    "status": "success", "command": method, "data": data,
                    "warnings": data.get("warnings", []), "errors": [],
                    "project_revision": manager.require_document().revision,
                }
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
