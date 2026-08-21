"""Quality-first, external-vision VLOG director commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from facut.cli.common import manager_for, public_error
from facut.core.project_manager import ProjectManager
from facut.media.proxy_manager import ProxyManager
from facut.render import FFmpegBackend
from facut.render.presets import resolve_render_preset
from facut.responses import success_response
from facut.exceptions import ReviewRequiredError
from facut.fonts import FontCatalog
from facut.qc.engine import QCEngine, QCSource
from facut.analysis.engine import transcribe_local
from facut.subtitles.director import (
    apply_transcript_plan,
    apply_typography_plan,
    build_transcript_plan,
    build_typography_plan,
    load_transcript_plan,
    save_transcript_plan,
)
from facut.vlog import (
    build_story_candidates,
    candidate_document,
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


vlog_app = typer.Typer(help="Quality-first travel VLOG director workflow.")
inspect_app = typer.Typer(help="Read resumable visual-inspection tasks for an external AI.")
inbox_app = typer.Typer(help="Prioritize ambiguous, high-value and continuity review tasks.")
bible_app = typer.Typer(help="Manage confirmed travel people, places, terms and facts.")
vlog_app.add_typer(inspect_app, name="inspect")
vlog_app.add_typer(inbox_app, name="inbox")
vlog_app.add_typer(bible_app, name="bible")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, *, warnings=None, revision=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, warnings=warnings or [], project_revision=revision),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _manager(ctx: typer.Context, explicit_project: Path | None = None) -> ProjectManager:
    if explicit_project is None:
        return manager_for(_state(ctx))
    manager = ProjectManager(explicit_project)
    manager.load()
    return manager


def _apply_candidate(
    manager: ProjectManager,
    *,
    candidate_id: str,
    preset: str,
) -> tuple[object, object, object]:
    plan = refine_story_candidate(manager.project_dir, candidate_id)
    if plan.status != "ready":
        raise ReviewRequiredError(
            "Candidate is not ready; resolve evidence and continuity gaps first.",
            details={"candidate_id": candidate_id, "warnings": plan.warnings},
        )
    candidate_state, candidate = candidate_document(
        manager.require_document(), manager.project_dir, candidate_id
    )
    current = manager.require_document()
    applied = current.settings.get("vlog_director", {})
    if (
        applied.get("candidate_id") == candidate.id
        and applied.get("evidence_sha256") == plan.evidence_sha256
    ):
        return current, candidate, plan
    manager.ensure_experiment_branch("vlog")

    def operation(document):
        document.tracks = candidate_state.tracks
        document.transitions = candidate_state.transitions
        document.markers = candidate_state.markers
        document.recompute_duration()
        document.settings["vlog_director"] = {
            "candidate_id": candidate.id,
            "strategy": candidate.strategy,
            "style": plan.style,
            "evidence_sha256": plan.evidence_sha256,
        }
        return document.settings["vlog_director"]

    _, saved = manager.mutate(
        "vlog.build",
        f"Applied VLOG candidate {candidate_id}",
        operation,
        command={"candidate_id": candidate_id, "preset": preset},
    )
    return saved, candidate, plan


def _render_active(
    manager: ProjectManager,
    *,
    candidate_id: str,
    output: Path,
    preset: str,
    hardware: str,
    overwrite: bool,
    ffmpeg: str | Path | None,
) -> tuple[dict, object]:
    saved = manager.require_document()
    font_audit = FontCatalog().audit_project(saved)
    if font_audit["status"] != "pass":
        raise ReviewRequiredError(
            "Font audit failed; final delivery was blocked before rendering.",
            suggestion="Run `facut font audit`, repair missing fonts or glyphs, then retry.",
            details=font_audit,
        )
    settings = resolve_render_preset(preset, source_fps=saved.project.fps)
    result = FFmpegBackend(ffmpeg).render(
        saved,
        manager.project_dir,
        output,
        width=settings["width"],
        height=settings["height"],
        fps=settings["fps"],
        bitrate=settings["bitrate"],
        audio_bitrate=settings["audio_bitrate"],
        audio_sample_rate=settings["audio_sample_rate"],
        color_space=settings["color_space"],
        hardware=hardware,
        overwrite=overwrite,
        loudness_target=-14,
        true_peak=-1,
    )
    qc_report = QCEngine(
        ffmpeg=ffmpeg,
        expected_duration=saved.project.duration,
        duration_tolerance=1 / saved.project.fps,
    ).run(
        {"type": "delivery", "path": str(result.output)},
        [QCSource(Path(result.output))],
    )
    qc_path = manager.project_dir / "renders" / f"{Path(result.output).stem}.qc.json"
    qc_path.parent.mkdir(parents=True, exist_ok=True)
    qc_path.write_text(
        json.dumps(qc_report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    delivery = {
        "status": "pass" if qc_report.status.value == "pass" else "review_required",
        "output": str(Path(result.output).resolve()),
        "preset": preset,
        "hardware": result.hardware,
        "encoder": result.encoder,
        "duration": result.duration,
        "qc_report": str(qc_path.resolve()),
        "completed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }

    def record_delivery(document):
        director = document.settings.setdefault("vlog_director", {})
        director["delivery"] = delivery
        return delivery

    manager.mutate(
        "vlog.delivery.record",
        f"Recorded VLOG delivery {Path(result.output).name}",
        record_delivery,
        command={"candidate_id": candidate_id, "output": str(result.output), "preset": preset},
    )
    return {
        "candidate_id": candidate_id,
        "output": str(result.output),
        "duration": result.duration,
        "encoder": result.encoder,
        "hardware": result.hardware,
        "loudness": result.loudness,
        "font_audit": font_audit,
        "qc": {"status": qc_report.status.value, "report": str(qc_path.resolve())},
        "delivery": delivery,
    }, result


def _build_and_render(
    manager: ProjectManager,
    *,
    candidate_id: str,
    output: Path,
    preset: str,
    hardware: str,
    overwrite: bool,
    ffmpeg: str | Path | None,
) -> tuple[dict, object]:
    _apply_candidate(manager, candidate_id=candidate_id, preset=preset)
    return _render_active(
        manager,
        candidate_id=candidate_id,
        output=output,
        preset=preset,
        hardware=hardware,
        overwrite=overwrite,
        ffmpeg=ffmpeg,
    )


def _auto_caption_and_typography(manager: ProjectManager, state, *, subtitle: str, typography: str) -> dict:
    """Prepare review-first captions and apply only evidence-safe typography."""

    data: dict[str, object] = {"subtitle": subtitle, "typography": typography}
    if subtitle not in {"auto", "none"}:
        raise ValueError("--subtitle must be auto or none.")
    if typography not in {"auto", "none"}:
        raise ValueError("--typography must be auto or none.")
    if subtitle == "auto":
        plan_path = manager.project_dir / "cache" / "subtitles" / "transcript.plan.json"
        if plan_path.is_file():
            plan = load_transcript_plan(plan_path)
            if plan.project_revision != manager.require_document().revision:
                plan_path.unlink()
                plan = None
        else:
            plan = None
        if plan is None:
            model_path = state.config.models.resolve("srt_model")
            document = manager.require_document()
            media_ids = {
                clip.media_id
                for track in document.tracks
                if track.type.value in {"video", "audio"}
                for clip in track.clips
                if clip.enabled
            }
            transcripts = {}
            for media_id in sorted(media_ids):
                asset = document.find_media(media_id)
                if asset is None:
                    continue
                transcripts[media_id] = transcribe_local(
                    manager.resolve_path(asset.path),
                    model_path=model_path,
                    language="zh",
                    external_python=state.config.tools.analysis_python,
                    word_timestamps=True,
                )
            plan = build_transcript_plan(
                document,
                manager.project_dir,
                transcripts,
                language="zh",
                model=Path(model_path).name,
                word_timestamps=True,
            )
            save_transcript_plan(plan, plan_path)
        drafts = [cue.id for cue in plan.cues if cue.status == "draft"]
        if drafts:
            raise ReviewRequiredError(
                "ASR produced captions that require human review before final delivery.",
                suggestion=f"Review `{plan_path}`, approve or correct uncertain cues, then rerun vlog build.",
                details={"plan": str(plan_path), "draft_count": len(drafts), "draft_cues": drafts[:50]},
            )
        if plan.cues:
            data["captions"] = apply_transcript_plan(manager, plan, approved_only=True)
        else:
            data["captions"] = {"created_cues": [], "reason": "No speech was detected."}
    if typography == "auto":
        data["typography_plan"] = build_typography_plan(manager.project_dir)
        data["typography_apply"] = apply_typography_plan(manager)
    return data


@inbox_app.command("next")
def vlog_inbox_next(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 8,
) -> None:
    """Return the highest-priority evidence tasks; never reduce baseline coverage."""

    command = "vlog.inbox.next"
    try:
        manager = manager_for(_state(ctx))
        _emit(
            ctx, command, next_inbox_items(manager.project_dir, limit=limit),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@inbox_app.command("list")
def vlog_inbox_list(ctx: typer.Context) -> None:
    """List the complete stable Director Inbox queue."""

    command = "vlog.inbox.list"
    try:
        manager = manager_for(_state(ctx))
        _emit(
            ctx, command, rebuild_director_inbox(manager.project_dir),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@inbox_app.command("resolve")
def vlog_inbox_resolve(
    ctx: typer.Context,
    item_id: Annotated[str, typer.Argument()],
    resolution: Annotated[str, typer.Option("--resolution")],
) -> None:
    """Resolve one review item with an auditable explanation."""

    command = "vlog.inbox.resolve"
    try:
        manager = manager_for(_state(ctx))
        _emit(
            ctx, command,
            resolve_inbox_item(manager.project_dir, item_id, resolution=resolution),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@bible_app.command("show")
def vlog_bible_show(ctx: typer.Context) -> None:
    """Show the facts allowed to constrain story, titles and narration."""

    command = "vlog.bible.show"
    try:
        manager = manager_for(_state(ctx))
        bible = load_trip_bible(
            manager.project_dir, default_name=manager.require_document().project.name
        )
        _emit(
            ctx, command, bible.model_dump(mode="json"),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@bible_app.command("import")
def vlog_bible_import(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
) -> None:
    """Validate and atomically replace the Trip Bible from reviewed JSON."""

    command = "vlog.bible.import"
    try:
        manager = manager_for(_state(ctx))
        payload = json.loads(source.expanduser().resolve().read_text("utf-8-sig"))
        data = save_trip_bible(manager.project_dir, payload)
        data["director_inbox"] = rebuild_director_inbox(manager.project_dir)
        _emit(ctx, command, data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("prepare")
def vlog_prepare(
    ctx: typer.Context,
    source: Annotated[Path | None, typer.Argument(help="Optional media root to import recursively.")] = None,
    project: Annotated[Path | None, typer.Option("--project", "-p", help="Create or open this VLOG project.")] = None,
    trip: Annotated[str | None, typer.Option("--trip")] = None,
    proxy: Annotated[str, typer.Option("--proxy", help="none or auto-link existing LRF/proxies.")] = "auto",
    batch_size: Annotated[int, typer.Option("--batch-size", min=1, max=100)] = 12,
    frames: Annotated[bool, typer.Option("--frames/--metadata-only")] = True,
    engine: Annotated[str, typer.Option("--engine", help="auto, native, or python.")] = "auto",
    native_mode: Annotated[str, typer.Option("--native-mode", help="fast or deep.")] = "fast",
) -> None:
    """Import media, link existing proxies, and create complete baseline inspection tasks."""

    command = "vlog.prepare"
    try:
        state = _state(ctx)
        if proxy not in {"none", "auto"}:
            raise ValueError("--proxy must be none or auto.")
        if engine not in {"auto", "native", "python"}:
            raise ValueError("--engine must be auto, native, or python.")
        if native_mode not in {"fast", "deep"}:
            raise ValueError("--native-mode must be fast or deep.")
        if project is not None:
            destination = project.expanduser().resolve()
            if (destination / "facut.json").is_file() or destination.name == "facut.json":
                manager = ProjectManager(destination)
                manager.load()
            else:
                manager = ProjectManager.create(
                    destination,
                    name=trip or (source.name if source else destination.name),
                    width=3840,
                    height=2160,
                    fps=30,
                    sample_rate=48000,
                )
        else:
            manager = manager_for(state)
        imported = []
        if source is not None:
            source = source.expanduser().resolve()
            if not source.is_dir():
                raise FileNotFoundError(f'VLOG media root "{source}" was not found.')
            imported = import_source_resumable(manager, source)
        proxy_results = []
        if proxy == "auto":
            proxy_results, _ = ProxyManager(
                manager,
                ffmpeg=state.config.tools.ffmpeg,
                ffprobe=state.config.tools.ffprobe,
            ).scan(search_directories=[source] if source else None, link=True)
        native_results = None
        native_status: dict[str, Any] = {"requested": engine, "used": "python"}
        native_warning: str | None = None
        if engine != "python":
            from facut.native import NativeClient, discover_native, scan_project_media

            native_path = discover_native()
            if native_path is not None:
                try:
                    native_payload = scan_project_media(manager, mode=native_mode, executable=native_path)
                    native_results = {
                        item["media_id"]: item
                        for item in native_payload.get("results", [])
                        if item.get("media_id")
                    }
                    native_status = {
                        "requested": engine,
                        "used": "native",
                        "mode": native_mode,
                        "executable": str(native_path),
                        "task_id": native_payload.get("task_id"),
                        "elapsed_seconds": native_payload.get("elapsed_seconds"),
                    }
                except Exception as error:
                    if engine == "native":
                        raise
                    native_warning = (
                        "FACUT Native failed twice; vlog preparation continued with the "
                        f"Python/FFmpeg path: {error}"
                    )
                    native_status = {
                        "requested": engine,
                        "used": "python",
                        "fallback_reason": str(error),
                    }
            elif engine == "native":
                NativeClient()
        data = prepare_evidence_manifest(
            manager,
            ffmpeg=state.config.tools.ffmpeg,
            generate_frames=frames,
            batch_size=batch_size,
            native_results=native_results,
        )
        data.update(
            {
                "project": str(manager.project_file.resolve()),
                "imported_media": imported.get("newly_imported", 0) if imported else 0,
                "import_failures": imported.get("failures", []) if imported else [],
                "proxy_results": proxy_results,
                "analysis_engine": native_status,
                "next_command": "facut vlog inspect next",
            }
        )
        _emit(
            ctx,
            command,
            data,
            revision=manager.require_document().revision,
            warnings=[native_warning] if native_warning else None,
        )
    except Exception as error:
        _fail(ctx, command, error)


@inspect_app.command("next")
def vlog_inspect_next(ctx: typer.Context) -> None:
    """Return the next bounded visual-inspection task and exact output schema."""

    command = "vlog.inspect.next"
    try:
        manager = manager_for(_state(ctx))
        data = next_inspection_task(manager.project_dir)
        _emit(ctx, command, data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("observe")
def vlog_observe(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="Evidence v2 JSON returned by the external visual AI.")],
    task: Annotated[str | None, typer.Option("--task")] = None,
) -> None:
    """Validate and idempotently store evidence without claiming local vision inference."""

    command = "vlog.observe"
    try:
        manager = manager_for(_state(ctx))
        payload = json.loads(source.expanduser().resolve().read_text(encoding="utf-8-sig"))
        data = ingest_observations(
            manager.require_document(), manager.project_dir, payload, task_id=task
        )
        data["next_command"] = (
            "facut vlog inspect next" if data["remaining_tasks"] else "facut vlog plan --style natural-vlog"
        )
        _emit(ctx, command, data, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("status")
def vlog_status(ctx: typer.Context) -> None:
    """Report the current director stage, coverage, gaps, and exact next command."""

    command = "vlog.status"
    try:
        manager = manager_for(_state(ctx))
        _emit(
            ctx,
            command,
            director_status(manager.project_dir, manager.require_document()),
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("plan")
def vlog_plan(
    ctx: typer.Context,
    style: Annotated[str, typer.Option("--style")] = "natural-vlog",
    target_duration: Annotated[float, typer.Option("--target-duration", min=1)] = 480,
    candidates: Annotated[int, typer.Option("--candidates", min=3, max=3)] = 3,
) -> None:
    """Build three evidence-grounded StoryGraph candidates."""

    del candidates
    command = "vlog.plan"
    try:
        manager = manager_for(_state(ctx))
        plan = build_story_candidates(
            manager.require_document(),
            manager.project_dir,
            style=style,
            target_duration=target_duration,
        )
        _emit(
            ctx,
            command,
            plan.model_dump(mode="json"),
            warnings=plan.warnings,
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("compare")
def vlog_compare(ctx: typer.Context) -> None:
    """Compare candidate shot choices, durations, scores, and unresolved gaps."""

    command = "vlog.compare"
    try:
        manager = manager_for(_state(ctx))
        _emit(ctx, command, compare_story_candidates(manager.project_dir), revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("refine")
def vlog_refine(
    ctx: typer.Context,
    candidate_id: Annotated[str, typer.Argument()],
    automatic: Annotated[bool, typer.Option("--auto/--manual")] = False,
) -> None:
    """Run deterministic continuity gates and select one reviewable candidate."""

    command = "vlog.refine"
    try:
        manager = manager_for(_state(ctx))
        plan = refine_story_candidate(manager.project_dir, candidate_id)
        data = plan.model_dump(mode="json")
        data["automatic"] = automatic
        data["next_command"] = (
            f"facut vlog build {candidate_id}" if plan.status == "ready" else "facut vlog inspect next"
        )
        _emit(ctx, command, data, warnings=plan.warnings, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("preview")
def vlog_preview(
    ctx: typer.Context,
    candidate_id: Annotated[str | None, typer.Argument()] = None,
    all_candidates: Annotated[bool, typer.Option("--all-candidates")] = False,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Render real 540p candidate previews without changing the saved timeline."""

    command = "vlog.preview"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        comparison = compare_story_candidates(manager.project_dir)
        ids = [item["id"] for item in comparison["candidates"]]
        selected = ids if all_candidates else [candidate_id or ids[0]]
        root = (output_dir or manager.project_dir / "previews" / "vlog").resolve()
        outputs = []
        backend = FFmpegBackend(state.config.tools.ffmpeg)
        for item_id in selected:
            document, candidate = candidate_document(manager.require_document(), manager.project_dir, item_id)
            destination = root / f"{item_id}.mp4"
            result = backend.preview_range(
                document,
                manager.project_dir,
                destination,
                start=0,
                end=document.project.duration,
                height=540,
                fps=24,
                overwrite=overwrite,
            )
            outputs.append({"candidate_id": candidate.id, "output": str(result.output), "duration": result.duration})
        _emit(ctx, command, {"outputs": outputs, "saved_timeline_changed": False}, revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("build")
def vlog_build(
    ctx: typer.Context,
    candidate_id: Annotated[str, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    preset: Annotated[str, typer.Option("--preset")] = "youtube-4k",
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Commit the selected ready candidate once, then render the original-media 4K delivery."""

    command = "vlog.build"
    try:
        state = _state(ctx)
        manager = manager_for(state)
        data, result = _build_and_render(
            manager,
            candidate_id=candidate_id,
            output=output,
            preset=preset,
            hardware=hardware,
            overwrite=overwrite,
            ffmpeg=state.config.tools.ffmpeg,
        )
        _emit(
            ctx,
            command,
            data,
            warnings=result.warnings,
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)


@vlog_app.command("run")
def vlog_run(
    ctx: typer.Context,
    source: Annotated[Path, typer.Argument(help="Travel media root directory.")],
    output: Annotated[Path, typer.Option("--output", "-o")],
    project: Annotated[Path | None, typer.Option("--project", "-p")] = None,
    style: Annotated[str, typer.Option("--style")] = "natural-vlog",
    target_duration: Annotated[float, typer.Option("--target-duration", min=1)] = 480,
    preset: Annotated[str, typer.Option("--preset")] = "youtube-4k",
    subtitle: Annotated[str, typer.Option("--subtitle")] = "auto",
    typography: Annotated[str, typer.Option("--typography")] = "auto",
    preserve_original_audio: Annotated[bool, typer.Option("--preserve-original-audio/--replace-original-audio")] = True,
    automatic: Annotated[bool, typer.Option("--auto/--review")] = False,
    hardware: Annotated[str, typer.Option("--hardware")] = "auto",
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Resume the complete workflow; stop honestly when external visual evidence is required."""

    command = "vlog.run"
    try:
        if not automatic:
            raise ReviewRequiredError(
                "VLOG run defaults to review mode. Use `--auto` only when automated candidate selection is intended.",
                suggestion="Run `facut vlog prepare`, inspect candidates, or repeat with --auto.",
            )
        state = _state(ctx)
        source = source.expanduser().resolve()
        if not source.is_dir():
            raise FileNotFoundError(f'VLOG media root "{source}" was not found.')
        if state.project is not None:
            manager = manager_for(state)
        else:
            project_dir = (
                project.expanduser().resolve()
                if project is not None
                else output.expanduser().resolve().parent / f"{source.name}-facut-project"
            )
            if (project_dir / "facut.json").is_file():
                manager = ProjectManager(project_dir)
                manager.load()
            else:
                manager = ProjectManager.create(
                    project_dir,
                    name=source.name,
                    width=3840,
                    height=2160,
                    fps=30,
                    sample_rate=48000,
                )
        import_result = import_source_resumable(manager, source)
        ProxyManager(
            manager,
            ffmpeg=state.config.tools.ffmpeg,
            ffprobe=state.config.tools.ffprobe,
        ).scan(search_directories=[source], link=True)
        prepared = prepare_evidence_manifest(
            manager, ffmpeg=state.config.tools.ffmpeg, generate_frames=True
        )
        if prepared["pending_tasks"]:
            task = next_inspection_task(manager.project_dir)
            raise ReviewRequiredError(
                "External visual inspection is required before quality-first story planning.",
                suggestion="Inspect the returned frame batch, submit evidence with `facut vlog observe`, then rerun this command.",
                details={"stage": "inspection", "task": task},
            )
        plan = build_story_candidates(
            manager.require_document(),
            manager.project_dir,
            style=style,
            target_duration=target_duration,
        )
        selected = max(plan.candidates, key=lambda item: item.score)
        refine_story_candidate(manager.project_dir, selected.id)
        _apply_candidate(manager, candidate_id=selected.id, preset=preset)
        packaging = _auto_caption_and_typography(
            manager, state, subtitle=subtitle, typography=typography
        )
        data, result = _render_active(
            manager,
            candidate_id=selected.id,
            output=output,
            preset=preset,
            hardware=hardware,
            overwrite=overwrite,
            ffmpeg=state.config.tools.ffmpeg,
        )
        data.update(
            {
                "style": style,
                "automatic": True,
                "quality_policy": "quality-first",
                "preserve_original_audio": preserve_original_audio,
                "packaging": packaging,
                "import": import_result,
            }
        )
        _emit(
            ctx,
            command,
            data,
            warnings=result.warnings,
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, command, error)
