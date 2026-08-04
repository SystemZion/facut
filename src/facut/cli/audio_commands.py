"""Independent audio-track editing commands."""

from __future__ import annotations

import json
from typing import Annotated, Any, Callable

import typer

from facut.cli.common import manager_for, public_error
from facut.cli.timeline_commands import _abort, _execute, _state
from facut.core.models import ProjectDocument
from facut.core.timeline_engine import TimelineEngine
from facut.responses import success_response


audio_app = typer.Typer(
    help="Add and shape independent music or sound-effect tracks."
)


def _serialize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    return value


def _mutate(
    ctx: typer.Context,
    action: str,
    summary: str,
    operation: Callable[[TimelineEngine], Any],
    dry_run: bool,
) -> None:
    """Run audio-only mutations with the standard revision/JSON contract."""

    from facut.cli.main import emit

    try:
        manager = manager_for(_state(ctx))

        def apply(document: ProjectDocument) -> Any:
            return operation(TimelineEngine(document))

        result, document = manager.mutate(
            action,
            summary,
            apply,
            command={"action": action},
            dry_run=dry_run,
        )
        data = _serialize(result)
        emit(
            _state(ctx),
            success_response(
                action,
                {"result": data, "dry_run": dry_run},
                project_revision=document.revision,
            ),
            human=f"[green]{action} completed.[/green]\n"
            f"{json.dumps(data, ensure_ascii=False, indent=2)}",
        )
    except Exception as error:
        _abort(ctx, action, public_error(error))


@audio_app.command("add")
def audio_add(
    ctx: typer.Context,
    media_id: Annotated[str, typer.Argument()],
    track: Annotated[str, typer.Option("--track")],
    at: Annotated[str, typer.Option("--at")] = "0",
    source_in: Annotated[str, typer.Option("--in")] = "0",
    source_out: Annotated[str | None, typer.Option("--out")] = None,
    volume_db: Annotated[float, typer.Option("--volume-db")] = 0.0,
    muted: Annotated[bool, typer.Option("--muted")] = False,
    fade_in: Annotated[str, typer.Option("--fade-in")] = "0",
    fade_out: Annotated[str, typer.Option("--fade-out")] = "0",
    highpass_hz: Annotated[float | None, typer.Option("--highpass-hz")] = None,
    denoise_strength: Annotated[
        float | None, typer.Option("--denoise-strength")
    ] = None,
    compressor: Annotated[bool, typer.Option("--compressor")] = False,
    limiter_db: Annotated[float | None, typer.Option("--limiter-db")] = None,
    loudnorm_lufs: Annotated[float | None, typer.Option("--loudnorm-lufs")] = None,
    channel_mode: Annotated[str, typer.Option("--channel-mode")] = "original",
    pan: Annotated[float | None, typer.Option("--pan")] = None,
    loop: Annotated[
        bool,
        typer.Option(
            "--loop",
            help="Repeat the selected source range; defaults to covering the video timeline.",
        ),
    ] = False,
    duration: Annotated[
        str | None,
        typer.Option("--duration", help="Explicit looped timeline duration."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Place music or a sound effect on an audio track."""

    params = {
        "media_id": media_id,
        "track": track,
        "at": at,
        "in": source_in,
        "volume_db": volume_db,
        "muted": muted,
        "fade_in": fade_in,
        "fade_out": fade_out,
        "highpass_hz": highpass_hz,
        "denoise_strength": denoise_strength,
        "compressor": compressor,
        "limiter_db": limiter_db,
        "loudnorm_lufs": loudnorm_lufs,
        "channel_mode": channel_mode,
        "pan": pan,
        "loop": loop,
    }
    params = {key: value for key, value in params.items() if value is not None}
    if source_out is not None:
        params["out"] = source_out
    if duration is not None:
        params["duration"] = duration
    _execute(ctx, "audio.add", params, dry_run)


@audio_app.command("volume")
def audio_volume(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    db: Annotated[float, typer.Option("--db")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Set gain for native video audio or an independent audio clip."""

    _execute(ctx, "audio.volume", {"clip_id": clip_id, "db": db}, dry_run)


@audio_app.command("fade")
def audio_fade(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    fade_in: Annotated[str | None, typer.Option("--in")] = None,
    fade_out: Annotated[str | None, typer.Option("--out")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Apply a fade-in, fade-out, or both."""

    params = {"clip_id": clip_id}
    if fade_in is not None:
        params["fade_in"] = fade_in
    if fade_out is not None:
        params["fade_out"] = fade_out
    _execute(ctx, "audio.fade", params, dry_run)


@audio_app.command("mute")
def audio_mute(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    unmute: Annotated[bool, typer.Option("--unmute")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Mute or restore native video audio or an independent audio clip."""

    _mutate(
        ctx,
        "audio.mute",
        f"{'Unmuted' if unmute else 'Muted'} {clip_id}",
        lambda timeline: timeline.set_audio_mute(clip_id, not unmute),
        dry_run,
    )


@audio_app.command("process")
def audio_process(
    ctx: typer.Context,
    clip_id: Annotated[str, typer.Argument()],
    highpass_hz: Annotated[float | None, typer.Option("--highpass", "--highpass-hz")] = None,
    denoise_strength: Annotated[
        float | None, typer.Option("--denoise", "--denoise-strength")
    ] = None,
    compress: Annotated[
        str | None,
        typer.Option("--compress", help="Compressor preset: vlog, dialogue, or gentle."),
    ] = None,
    compressor: Annotated[bool, typer.Option("--compressor")] = False,
    disable_compressor: Annotated[
        bool, typer.Option("--disable-compressor")
    ] = False,
    compressor_threshold_db: Annotated[
        float | None, typer.Option("--compressor-threshold-db")
    ] = None,
    compressor_ratio: Annotated[
        float | None, typer.Option("--compressor-ratio")
    ] = None,
    limiter_db: Annotated[float | None, typer.Option("--limiter", "--limiter-db")] = None,
    loudnorm_lufs: Annotated[float | None, typer.Option("--loudnorm-lufs")] = None,
    channel_mode: Annotated[str | None, typer.Option("--channel-mode")] = None,
    pan: Annotated[float | None, typer.Option("--pan")] = None,
    clear_pan: Annotated[bool, typer.Option("--clear-pan")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Configure cleanup, dynamics, loudness, and channel correction."""

    if compressor and disable_compressor:
        raise typer.BadParameter("Choose --compressor or --disable-compressor, not both.")
    compressor_value = False if disable_compressor else (True if compressor else None)
    _mutate(
        ctx,
        "audio.process",
        f"Updated audio processing for {clip_id}",
        lambda timeline: timeline.configure_audio(
            clip_id,
            highpass_hz=highpass_hz,
            denoise_strength=denoise_strength,
            compressor=compressor_value,
            compressor_preset=compress,
            compressor_threshold_db=compressor_threshold_db,
            compressor_ratio=compressor_ratio,
            limiter_db=limiter_db,
            loudnorm_lufs=loudnorm_lufs,
            channel_mode=channel_mode,
            pan=pan,
            clear_pan=clear_pan,
        ),
        dry_run,
    )


@audio_app.command("loudness")
def audio_loudness(
    ctx: typer.Context,
    target: Annotated[float, typer.Option("--target")] = -14.0,
    true_peak: Annotated[float, typer.Option("--true-peak")] = -1.0,
    loudness_range: Annotated[float, typer.Option("--lra")] = 11.0,
    two_pass: Annotated[bool, typer.Option("--two-pass/--single-pass")] = True,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Set project-wide loudness defaults; rendering uses two-pass by default."""

    _execute(
        ctx,
        "audio.loudness",
        {
            "target": target,
            "true_peak": true_peak,
            "loudness_range": loudness_range,
            "two_pass": two_pass,
        },
        dry_run,
    )


@audio_app.command("crossfade")
def audio_crossfade(
    ctx: typer.Context,
    from_clip: Annotated[str, typer.Option("--from")],
    to_clip: Annotated[str, typer.Option("--to")],
    duration: Annotated[str, typer.Option("--duration")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Crossfade overlapping independent audio-track clips."""

    _mutate(
        ctx,
        "audio.crossfade",
        f"Crossfaded {from_clip} to {to_clip}",
        lambda timeline: timeline.crossfade_audio(from_clip, to_clip, duration),
        dry_run,
    )
