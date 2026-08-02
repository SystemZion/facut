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
    load_semantic_index,
    search_semantic_index,
)
from facut.responses import success_response
from facut.voice import VoiceProfileStore, synthesize_with_provider


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


@narration_app.command("synthesize")
def narration_synthesize(
    ctx: typer.Context,
    plan_file: Annotated[Path, typer.Argument()],
    voice_profile: Annotated[str, typer.Option("--voice-profile")],
    preview_dir: Annotated[Path, typer.Option("--preview-dir")],
    provider: Annotated[Path | None, typer.Option("--provider")] = None,
) -> None:
    """Synthesize reviewed lines through an explicitly configured local provider."""

    try:
        payload = json.loads(plan_file.expanduser().resolve().read_text(encoding="utf-8"))
        lines = payload.get("lines") or payload.get("data", {}).get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError('Narration plan requires a non-empty "lines" list.')
        store = VoiceProfileStore()
        profile = store.get(voice_profile)
        data = synthesize_with_provider(
            profile,
            store.profile_directory(profile.id),
            lines,
            preview_dir,
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
