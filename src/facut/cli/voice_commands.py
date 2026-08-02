"""CLI for consent-gated local digital voice profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Annotated

import typer

from facut.cli.common import public_error
from facut.responses import success_response
from facut.voice import (
    VoiceProfileStore,
    RecordingStudioServer,
    build_recording_plan,
    configure_provider,
    provider_status,
    synthesize_with_provider,
    validate_voice_styles,
    validate_voice_samples,
    voice_style_catalog,
)


voice_app = typer.Typer(help="Manage authorized local digital voice profiles.")
profile_app = typer.Typer(help="Create, import, inspect, validate and remove voice profiles.")
provider_app = typer.Typer(help="Inspect the configured local voice synthesis provider.")
voice_app.add_typer(profile_app, name="profile")
voice_app.add_typer(provider_app, name="provider")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, *, warnings=None) -> None:
    from facut.cli.main import emit

    emit(
        _state(ctx),
        success_response(command, data, warnings=warnings or []),
        human=json.dumps(data, ensure_ascii=False, indent=2),
    )


def _fail(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def _write_json_atomic(path: Path, payload: dict) -> Path:
    destination = path.expanduser().resolve()
    if destination.exists():
        raise FileExistsError(f'Output "{destination}" already exists.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    return destination


@profile_app.command("create")
def profile_create(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument()],
    speaker: Annotated[str, typer.Option("--speaker")],
    consent_statement: Annotated[str, typer.Option("--consent-statement")],
    consent: Annotated[str, typer.Option("--consent")] = "self",
    language: Annotated[str, typer.Option("--language")] = "zh-CN",
    style: Annotated[str, typer.Option("--style")] = "natural-vlog",
) -> None:
    """Create one independently authorized local voice profile."""

    try:
        profile = VoiceProfileStore().create(
            name,
            speaker_id=speaker,
            language=language,
            style=style,
            consent_relationship=consent,
            consent_statement=consent_statement,
        )
        _emit(ctx, "voice.profile.create", profile.public_dict())
    except Exception as error:
        _fail(ctx, "voice.profile.create", error)


@profile_app.command("list")
def profile_list(ctx: typer.Context) -> None:
    """List profiles without exposing their absolute storage paths."""

    try:
        profiles = [item.public_dict() for item in VoiceProfileStore().list()]
        _emit(ctx, "voice.profile.list", {"count": len(profiles), "profiles": profiles})
    except Exception as error:
        _fail(ctx, "voice.profile.list", error)


@profile_app.command("show")
def profile_show(ctx: typer.Context, profile_id: Annotated[str, typer.Argument()]) -> None:
    try:
        _emit(ctx, "voice.profile.show", VoiceProfileStore().get(profile_id).public_dict())
    except Exception as error:
        _fail(ctx, "voice.profile.show", error)


@profile_app.command("import")
def profile_import(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    samples: Annotated[list[Path], typer.Argument()],
    transcript: Annotated[str | None, typer.Option("--transcript")] = None,
    category: Annotated[str | None, typer.Option("--category")] = None,
    delivery: Annotated[str | None, typer.Option("--delivery")] = None,
) -> None:
    """Copy PCM WAV samples into the selected local profile."""

    try:
        profile = VoiceProfileStore().import_samples(
            profile_id,
            samples,
            transcript=transcript,
            category=category,
            delivery=delivery,
        )
        _emit(ctx, "voice.profile.import", profile.public_dict())
    except Exception as error:
        _fail(ctx, "voice.profile.import", error)


@profile_app.command("validate")
def profile_validate(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    recommended_seconds: Annotated[
        float, typer.Option("--recommended-seconds", min=1)
    ] = 600,
) -> None:
    """Run bounded PCM quality checks for every stored voice sample."""

    try:
        store = VoiceProfileStore()
        profile = store.get(profile_id)
        report = validate_voice_samples(
            store.sample_paths(profile),
            recommended_total_seconds=recommended_seconds,
        )
        profile = store.set_status(
            profile.id,
            {"pass": "ready", "warning": "warning", "fail": "invalid"}[report["status"]],
        )
        _emit(
            ctx,
            "voice.profile.validate",
            {"profile_id": profile.id, "profile_status": profile.status, "report": report},
            warnings=[item["message"] for item in report["issues"]],
        )
    except Exception as error:
        _fail(ctx, "voice.profile.validate", error)


@profile_app.command("delete")
def profile_delete(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    confirm: Annotated[bool, typer.Option("--confirm")] = False,
) -> None:
    """Move a profile into FACUT's recoverable local trash."""

    try:
        if not confirm:
            raise ValueError("Voice profile deletion requires --confirm.")
        destination = VoiceProfileStore().delete(profile_id)
        _emit(
            ctx,
            "voice.profile.delete",
            {"profile_id": profile_id, "recoverable": True, "trash_name": destination.name},
        )
    except Exception as error:
        _fail(ctx, "voice.profile.delete", error)


@profile_app.command("restore")
def profile_restore(
    ctx: typer.Context, trash_name: Annotated[str, typer.Argument()]
) -> None:
    """Restore one profile previously moved to FACUT's local trash."""

    try:
        profile = VoiceProfileStore().restore(trash_name)
        _emit(ctx, "voice.profile.restore", profile.public_dict())
    except Exception as error:
        _fail(ctx, "voice.profile.restore", error)


@voice_app.command("record-plan")
def record_plan(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
    target_minutes: Annotated[int, typer.Option("--target-minutes", min=1, max=60)] = 10,
    script: Annotated[str, typer.Option("--script")] = "mandarin-balanced-v1",
) -> None:
    """Build a deterministic prompt plan for recording one voice profile."""

    try:
        profile = VoiceProfileStore().get(profile_id)
        plan = build_recording_plan(
            profile, target_minutes=target_minutes, script=script
        )
        if output is not None:
            plan["output"] = str(_write_json_atomic(output, plan))
        _emit(ctx, "voice.record-plan", plan)
    except Exception as error:
        _fail(ctx, "voice.record-plan", error)


@voice_app.command("record")
def record_voice(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    target_minutes: Annotated[int, typer.Option("--target-minutes", min=1, max=60)] = 10,
    script: Annotated[str, typer.Option("--script")] = "mandarin-balanced-v1",
    port: Annotated[int, typer.Option("--port", min=0, max=65535)] = 0,
    no_open: Annotated[
        bool, typer.Option("--no-open", help="Do not open the local recording page automatically.")
    ] = False,
) -> None:
    """Open FACUT's localhost-only microphone recording studio."""

    try:
        studio = RecordingStudioServer(
            profile_id,
            target_minutes=target_minutes,
            script=script,
            port=port,
        )
        state = _state(ctx)
        if not state.json_output and not state.quiet:
            typer.echo(f"FACUT voice studio: {studio.url}")
            typer.echo("Recording data stays on this computer. Press Ctrl+C to cancel.")
        result = studio.serve(open_browser=not no_open)
        _emit(ctx, "voice.record", result)
    except KeyboardInterrupt:
        _emit(
            ctx,
            "voice.record",
            {
                "profile_id": profile_id,
                "finished": False,
                "cancelled": True,
            },
            warnings=["The recording session was cancelled before completion."],
        )
    except Exception as error:
        _fail(ctx, "voice.record", error)


@provider_app.command("status")
def voice_provider_status(
    ctx: typer.Context,
    executable: Annotated[Path | None, typer.Option("--executable")] = None,
) -> None:
    try:
        data = provider_status(executable)
        warnings = [] if data["available"] else ["No local voice synthesis provider is available."]
        _emit(ctx, "voice.provider.status", data, warnings=warnings)
    except Exception as error:
        _fail(ctx, "voice.provider.status", error)


@provider_app.command("configure")
def voice_provider_configure(
    ctx: typer.Context,
    executable: Annotated[Path, typer.Argument()],
) -> None:
    """Persist the executable used for offline voice synthesis."""

    try:
        _emit(ctx, "voice.provider.configure", configure_provider(executable))
    except Exception as error:
        _fail(ctx, "voice.provider.configure", error)


@voice_app.command("synthesize")
def voice_synthesize(
    ctx: typer.Context,
    profile_id: Annotated[str, typer.Argument()],
    text: Annotated[str, typer.Argument()],
    output: Annotated[Path, typer.Option("--output", "-o")],
    provider: Annotated[Path | None, typer.Option("--provider")] = None,
    speed: Annotated[float, typer.Option("--speed", min=0.5, max=2.0)] = 1.0,
    delivery: Annotated[
        str,
        typer.Option(
            "--style",
            "--delivery",
            help="One style or a comma-separated list. Run `facut voice styles` to list them.",
        ),
    ] = "natural",
    instruction: Annotated[
        str | None,
        typer.Option("--instruction", help="Optional additional delivery guidance."),
    ] = None,
    takes: Annotated[int, typer.Option("--takes", min=1, max=3)] = 1,
    overwrite: Annotated[bool, typer.Option("--overwrite")] = False,
) -> None:
    """Synthesize one or more expressive takes with an authorized voice profile."""

    try:
        destination = output.expanduser().resolve()
        if destination.suffix.casefold() != ".wav":
            raise ValueError("Voice synthesis output must use the .wav extension.")
        styles = validate_voice_styles(delivery.split(","))
        line_specs = [
            (style, take_index)
            for style in styles
            for take_index in range(takes)
        ]
        multiple = len(line_specs) > 1
        destinations = []
        for style, take_index in line_specs:
            if not multiple:
                destinations.append(destination)
                continue
            take_suffix = f"-take-{take_index + 1}" if takes > 1 else ""
            destinations.append(
                destination.with_name(
                    f"{destination.stem}-{style}{take_suffix}{destination.suffix}"
                )
            )
        existing = [item for item in destinations if item.exists()]
        if existing and not overwrite:
            raise FileExistsError(f'Output "{existing[0]}" already exists; use --overwrite.')
        store = VoiceProfileStore()
        profile = store.get(profile_id)
        result = synthesize_with_provider(
            profile,
            store.profile_directory(profile_id),
            [
                {
                    "text": text,
                    "speed": speed,
                    "delivery": style,
                    "instruction": instruction,
                    "candidate_index": take_index,
                    "force": overwrite,
                }
                for style, take_index in line_specs
            ],
            destination.parent / ".facut-voice-cache",
            provider=provider,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        for item, final_path in zip(result["outputs"], destinations, strict=True):
            generated = Path(item["output"])
            with tempfile.NamedTemporaryFile(
                dir=destination.parent, delete=False, suffix=".wav"
            ) as stream:
                temporary = Path(stream.name)
            try:
                shutil.copy2(generated, temporary)
                os.replace(temporary, final_path)
            finally:
                temporary.unlink(missing_ok=True)
            item["output"] = str(final_path)
        result["takes"] = takes
        result["styles"] = styles
        _emit(ctx, "voice.synthesize", result, warnings=result.get("warnings"))
    except Exception as error:
        _fail(ctx, "voice.synthesize", error)


@voice_app.command("styles")
def voice_styles(ctx: typer.Context) -> None:
    """List stable voice styles for users and AI callers."""

    try:
        styles = voice_style_catalog()
        _emit(
            ctx,
            "voice.styles",
            {
                "count": len(styles),
                "styles": styles,
                "all_styles_argument": ",".join(item["id"] for item in styles),
                "style_recording_script": "vlog-style-capsules-v1",
            },
        )
    except Exception as error:
        _fail(ctx, "voice.styles", error)
