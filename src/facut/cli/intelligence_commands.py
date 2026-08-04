"""Agent-friendly semantic search, story planning, and coverage diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from facut.cli.common import manager_for, public_error
from facut.intelligence import (
    apply_story_plan,
    build_narration_plan,
    build_semantic_index,
    build_story_plan,
    diagnose_broll,
    generate_narration_plan,
    load_narration_plan,
    load_semantic_index,
    prepare_narration_apply,
    save_narration_plan,
    search_semantic_index,
)
from facut.responses import success_response
from facut.media.importer import hash_file
from facut.voice import VoiceProfileStore, synthesize_with_provider
from facut.voice.say import build_say_lines
from facut.voice.service import synthesize_with_voice_service


semantic_app = typer.Typer(help="Build and query evidence-backed semantic observations.")
story_app = typer.Typer(help="Generate inspectable travel-story candidates.")
broll_app = typer.Typer(help="Diagnose B-roll coverage and continuity gaps.")
narration_app = typer.Typer(help="Suggest evidence-grounded VLOG narration for the timeline.")


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


@semantic_app.command("index")
def semantic_index(
    ctx: typer.Context,
    observations: Annotated[Path | None, typer.Option("--observations")] = None,
) -> None:
    """Index project metadata plus optional AI/vision observations JSON."""

    try:
        manager = manager_for(_state(ctx))
        data = build_semantic_index(
            manager.require_document(), manager.project_dir, observations_file=observations
        )
        _emit(ctx, "semantic.index", data, warnings=data["limitations"], revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "semantic.index", error)


@semantic_app.command("search")
def semantic_search(
    ctx: typer.Context,
    query: Annotated[str, typer.Argument()],
    limit: Annotated[int, typer.Option("--limit", min=1, max=100)] = 10,
    minimum_score: Annotated[float, typer.Option("--minimum-score", min=0)] = 0.05,
) -> None:
    """Return stable media IDs, exact ranges, scores, confidence and evidence."""

    try:
        manager = manager_for(_state(ctx))
        data = search_semantic_index(
            load_semantic_index(manager.project_dir), query, limit=limit, minimum_score=minimum_score
        )
        _emit(ctx, "semantic.search", data, warnings=data["limitations"], revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "semantic.search", error)


@story_app.command("build")
def story_build(
    ctx: typer.Context,
    style: Annotated[str, typer.Option("--style")] = "travel-documentary",
    target_duration: Annotated[float, typer.Option("--target-duration", min=1)] = 600,
    sequence: Annotated[str, typer.Option("--sequence")] = "ai-story",
    apply: Annotated[bool, typer.Option("--apply")] = False,
) -> None:
    """Create a reviewable story plan; apply only when explicitly requested."""

    try:
        manager = manager_for(_state(ctx))
        plan = build_story_plan(
            manager.require_document(),
            load_semantic_index(manager.project_dir),
            style=style,
            target_duration=target_duration,
        )
        document = apply_story_plan(manager, plan, sequence=sequence) if apply else manager.require_document()
        data = {"plan": plan, "applied": apply, "sequence": sequence if apply else None}
        _emit(ctx, "story.build", data, warnings=plan["warnings"], revision=document.revision)
    except Exception as error:
        _fail(ctx, "story.build", error)


@broll_app.command("diagnose")
def broll_diagnose(
    ctx: typer.Context,
    maximum_aroll: Annotated[float, typer.Option("--maximum-aroll", min=1)] = 8,
) -> None:
    """Inspect current sequence without modifying it."""

    try:
        manager = manager_for(_state(ctx))
        try:
            index = load_semantic_index(manager.project_dir)
        except FileNotFoundError:
            index = None
        data = diagnose_broll(
            manager.require_document(), index, maximum_aroll_seconds=maximum_aroll
        )
        _emit(ctx, "broll.diagnose", data, warnings=data["limitations"], revision=manager.require_document().revision)
    except Exception as error:
        _fail(ctx, "broll.diagnose", error)


@narration_app.command("suggest")
def narration_suggest(
    ctx: typer.Context,
    style: Annotated[str, typer.Option("--style")] = "natural-vlog",
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
    max_lines: Annotated[int, typer.Option("--max-lines", min=1, max=100)] = 12,
    minimum_confidence: Annotated[
        float, typer.Option("--minimum-confidence", min=0, max=1)
    ] = 0.55,
) -> None:
    """Generate review-first suggestions and draft lines from visual evidence."""

    try:
        manager = manager_for(_state(ctx))
        data = build_narration_plan(
            manager.require_document(),
            load_semantic_index(manager.project_dir),
            style=style,
            language=language,
            max_lines=max_lines,
            minimum_confidence=minimum_confidence,
        )
        _emit(
            ctx,
            "narration.suggest",
            data,
            warnings=[*data["warnings"], *data["limitations"]],
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, "narration.suggest", error)


@narration_app.command("generate")
def narration_generate(
    ctx: typer.Context,
    output: Annotated[Path, typer.Option("--output", "-o")],
    style: Annotated[str, typer.Option("--style")] = "weekend-vlog",
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
    provider: Annotated[str, typer.Option("--provider")] = "deterministic",
    max_lines: Annotated[int, typer.Option("--max-lines", min=1, max=100)] = 12,
    minimum_confidence: Annotated[
        float, typer.Option("--minimum-confidence", min=0, max=1)
    ] = 0.55,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Create an evidence-grounded, review-first narration plan."""

    try:
        destination = output.expanduser().resolve()
        if destination.exists() and not overwrite:
            raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
        manager = manager_for(_state(ctx))
        plan = generate_narration_plan(
            manager.require_document(),
            load_semantic_index(manager.project_dir),
            style=style,
            language=language,
            provider=provider,
            max_lines=max_lines,
            minimum_confidence=minimum_confidence,
        )
        saved = save_narration_plan(plan, destination)
        data = plan.model_dump(mode="json")
        data["output"] = str(saved)
        _emit(
            ctx,
            "narration.generate",
            data,
            warnings=[*plan.warnings, *plan.limitations],
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _fail(ctx, "narration.generate", error)


@narration_app.command("review")
def narration_review(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    lines: Annotated[list[str] | None, typer.Option("--line")] = None,
    status: Annotated[str, typer.Option("--status")] = "approved",
    preview: Annotated[str | None, typer.Option("--preview")] = None,
    all_lines: Annotated[bool, typer.Option("--all")] = False,
) -> None:
    """Approve or reject explicit lines after auditioning their previews."""

    try:
        from facut.intelligence.narration_plan import NarrationLineStatus

        if status not in {"approved", "rejected", "draft"}:
            raise ValueError("Narration review status must be draft, approved, or rejected.")
        if not all_lines and not lines:
            raise ValueError("Select at least one --line, or use --all explicitly.")
        source = plan_file.expanduser().resolve()
        plan = load_narration_plan(source)
        selected = set(line.id for line in plan.lines) if all_lines else set(lines or [])
        unknown = selected - {line.id for line in plan.lines}
        if unknown:
            raise ValueError("Unknown narration line(s): " + ", ".join(sorted(unknown)))
        for line in plan.lines:
            if line.id not in selected:
                continue
            if preview is not None:
                if preview not in {item.id for item in line.previews}:
                    raise ValueError(f'Preview "{preview}" does not belong to line "{line.id}".')
                line.selected_preview_id = preview
            if status == "approved" and line.selected_preview is None:
                raise ValueError(f'Line "{line.id}" must have a synthesized preview before approval.')
            if status == "approved" and line.selected_preview_id is None:
                line.selected_preview_id = line.previews[0].id
            line.status = NarrationLineStatus(status)
        plan.status = (
            "ready"
            if any(line.status == NarrationLineStatus.APPROVED for line in plan.lines)
            else "review_required"
        )
        save_narration_plan(plan, source)
        _emit(
            ctx,
            "narration.review",
            {"plan": str(source), "status": plan.status, "updated_lines": sorted(selected)},
        )
    except Exception as error:
        _fail(ctx, "narration.review", error)


def synthesize_narration_previews(
    plan_file: str | Path,
    *,
    voice: str,
    preview_dir: str | Path,
    style: str = "auto",
    takes: int = 1,
    speed: float = 1.0,
    intensity: float = 0.5,
    instruction: str | None = None,
    require_cuda: bool = False,
    use_service: bool = True,
    provider: str | Path | None = None,
) -> dict:
    """Reusable CLI/RPC implementation for audition-first plan synthesis."""

    from facut.intelligence.narration_plan import NarrationPreview

    source = Path(plan_file).expanduser().resolve()
    output_root = Path(preview_dir).expanduser().resolve()
    plan = load_narration_plan(source)
    eligible = [line for line in plan.lines if line.status.value != "rejected"]
    if not eligible:
        raise ValueError("Narration plan has no draft or approved lines to synthesize.")
    store = VoiceProfileStore()
    profile = store.resolve(voice)
    provider_lines: list[dict] = []
    assignments: list[tuple[object, str, int]] = []
    style_decisions: dict[str, dict] = {}
    for line in eligible:
        built, selection = build_say_lines(
            line.selected_text,
            style=style if style != "auto" else line.style,
            takes=takes,
            speed=speed,
            intensity=intensity,
            instruction=instruction,
            purpose=line.suggestion,
        )
        style_decisions[line.id] = selection
        for take_index, item in enumerate(built, start=1):
            item["id"] = f"{line.id}_take_{take_index}"
            item["timeline_range"] = line.timeline_range.model_dump(mode="json")
            provider_lines.append(item)
            assignments.append((line, str(selection["selected_style"]), take_index))
    synthesize = synthesize_with_voice_service if use_service else synthesize_with_provider
    options = {"require_cuda": require_cuda} if use_service else {}
    if provider is not None:
        options["provider"] = provider
    data = synthesize(
        profile,
        store.profile_directory(profile.id),
        provider_lines,
        output_root,
        **options,
    )
    if len(data["outputs"]) != len(assignments):
        raise RuntimeError("Voice provider returned an unexpected number of narration previews.")
    for generated, (line, selected_style, take_index) in zip(
        data["outputs"], assignments, strict=True
    ):
        path = Path(str(generated["output"])).expanduser().resolve()
        try:
            stored_path = str(path.relative_to(source.parent))
        except ValueError:
            stored_path = str(path)
        preview_id = f"{line.id}_{selected_style}_take_{take_index}"
        preview = NarrationPreview(
            id=preview_id,
            path=stored_path,
            style=selected_style,
            take=take_index,
            sha256=hash_file(path),
        )
        line.previews = [item for item in line.previews if item.id != preview_id]
        line.previews.append(preview)
        line.voice = profile.id
        line.style = selected_style
        if line.selected_preview_id is None:
            line.selected_preview_id = preview_id
    save_narration_plan(plan, source)
    data["plan"] = str(source)
    data["preview_directory"] = str(output_root)
    data["style_decisions"] = style_decisions
    data["audition_required"] = True
    return data


@narration_app.command("synthesize")
def narration_synthesize(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    voice_profile: Annotated[
        str,
        typer.Option("--voice", "--voice-profile", help="Voice ID, alias, or unique display name."),
    ],
    preview_dir: Annotated[Path, typer.Option("--preview-dir")],
    style: Annotated[str, typer.Option("--style")] = "auto",
    takes: Annotated[int, typer.Option("--takes", min=1, max=10)] = 1,
    speed: Annotated[float, typer.Option("--speed", min=0.5, max=2.0)] = 1.0,
    intensity: Annotated[float, typer.Option("--intensity", min=0.0, max=1.0)] = 0.5,
    instruction: Annotated[str | None, typer.Option("--instruction")] = None,
    require_cuda: Annotated[bool, typer.Option("--require-cuda")] = False,
    use_service: Annotated[bool, typer.Option("--service/--no-service")] = True,
    provider: Annotated[Path | None, typer.Option("--provider")] = None,
) -> None:
    """Synthesize audition candidates and atomically attach them to the plan."""

    try:
        data = synthesize_narration_previews(
            plan_file,
            voice=voice_profile,
            preview_dir=preview_dir,
            style=style,
            takes=takes,
            speed=speed,
            intensity=intensity,
            instruction=instruction,
            require_cuda=require_cuda,
            use_service=use_service,
            provider=provider,
        )
        _emit(
            ctx,
            "narration.synthesize",
            data,
            warnings=data.get("warnings", []),
        )
    except Exception as error:
        _fail(ctx, "narration.synthesize", error)


@narration_app.command("apply")
def narration_apply(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    approved_only: Annotated[bool, typer.Option("--approved-only/--include-drafts")] = True,
    duck_music: Annotated[bool, typer.Option("--duck-music/--no-duck-music")] = False,
    preserve_original: Annotated[
        bool, typer.Option("--preserve-original/--replace-original")
    ] = True,
    track: Annotated[str, typer.Option("--track")] = "A_NARRATION",
    allow_stale: Annotated[bool, typer.Option("--allow-stale")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Apply selected narration audio in one undoable project revision."""

    try:
        from facut.core.command_engine import CommandEngine

        manager = manager_for(_state(ctx))
        result = CommandEngine(manager).execute(
            "narration.apply",
            {
                "plan_path": str(plan_file.expanduser().resolve()),
                "approved_only": approved_only,
                "duck_music": duck_music,
                "preserve_original": preserve_original,
                "track_id": track,
                "allow_stale": allow_stale,
            },
            dry_run=dry_run,
        )
        warnings = list(result.get("data", {}).get("warnings") or [])
        _emit(
            ctx,
            "narration.apply",
            {**result["data"], "dry_run": dry_run},
            warnings=warnings,
            revision=result["project_revision"],
        )
    except Exception as error:
        _fail(ctx, "narration.apply", error)
