"""Project, media import, inspection, history, and batch commands."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
import subprocess
from typing import Annotated

import typer

from facut.cli.common import compact_project, manager_for, public_error
from facut.core.command_engine import CommandEngine
from facut.core.project_manager import ProjectManager
from facut.media.probe import probe_media
from facut.media.proxy_manager import ProxyManager
from facut.responses import success_response


project_app = typer.Typer(help="Inspect, validate, snapshot, and manage projects.")
history_app = typer.Typer(help="Inspect project revision history.")
branch_app = typer.Typer(help="Create, switch, inspect, and accept CutGraph branches.")


def _state(ctx: typer.Context):
    from facut.cli.main import CliState

    return ctx.ensure_object(CliState)


def _emit(ctx: typer.Context, command: str, data, human: str = "", warnings=None, revision=None):
    from facut.cli.main import emit

    state = _state(ctx)
    emit(
        state,
        success_response(
            command, data, warnings=warnings or [], project_revision=revision
        ),
        human=human,
    )


def _abort(ctx: typer.Context, command: str, error: Exception) -> None:
    from facut.cli.main import fail

    fail(_state(ctx), command, public_error(error))


def init_command(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument(help="New project directory.")],
    width: Annotated[int, typer.Option("--width")] = 1920,
    height: Annotated[int, typer.Option("--height")] = 1080,
    fps: Annotated[float, typer.Option("--fps")] = 30.0,
    sample_rate: Annotated[int, typer.Option("--sample-rate")] = 48000,
    background: Annotated[str, typer.Option("--background")] = "#000000",
) -> None:
    """Create a validated non-destructive editing project."""

    try:
        manager = ProjectManager.create(
            path,
            width=width,
            height=height,
            fps=fps,
            sample_rate=sample_rate,
            background=background,
        )
        document = manager.require_document()
        _emit(
            ctx,
            "init",
            {"project": str(manager.project_file), "settings": document.project.model_dump(mode="json")},
            f"[green]Created project:[/green] {manager.project_file}",
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "init", error)


def import_command(
    ctx: typer.Context,
    paths: Annotated[list[Path], typer.Argument(help="Media file(s) or directories.")],
    recursive: Annotated[bool, typer.Option("--recursive", "-r")] = False,
    proxy: Annotated[
        str,
        typer.Option("--proxy", help="none or auto (detect and link existing LRF proxies)."),
    ] = "none",
    proxy_search: Annotated[
        list[Path] | None,
        typer.Option("--proxy-search", help="Additional directory to scan recursively."),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    porcelain: Annotated[
        bool,
        typer.Option(
            "--porcelain",
            help="Print only one stable media ID per line for shell scripts.",
        ),
    ] = False,
) -> None:
    """Import supported video, audio, image, and subtitle assets."""

    try:
        state = _state(ctx)
        manager = manager_for(state)
        proxy_mode = proxy.casefold()
        if proxy_mode not in {"none", "auto"}:
            raise ValueError("--proxy must be none or auto.")
        assets = manager.import_paths(
            paths,
            recursive=recursive,
            exclude_proxy_candidates=proxy_mode == "auto",
            dry_run=dry_run,
        )
        proxy_results: list[dict[str, object]] = []
        if proxy_mode == "auto" and not dry_run:
            service = ProxyManager(
                manager,
                ffmpeg=state.config.tools.ffmpeg,
                ffprobe=state.config.tools.ffprobe,
            )
            proxy_results, _ = service.scan(
                search_directories=proxy_search,
                link=True,
            )
        if porcelain:
            if state.json_output:
                raise ValueError("Use either --json or --porcelain, not both.")
            for asset in assets:
                typer.echo(asset.id)
            return
        revision = manager.require_document().revision + (1 if dry_run else 0)
        data = [asset.model_dump(mode="json") for asset in assets]
        _emit(
            ctx,
            "import",
            {"media": data, "proxies": proxy_results, "dry_run": dry_run},
            "\n".join(f"[green]{asset.id}[/green]  {asset.original_name}" for asset in assets),
            revision=revision,
        )
    except Exception as error:
        _abort(ctx, "import", error)


def inspect_command(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Media ID or file path.")],
    keyframes: Annotated[bool, typer.Option("--keyframes")] = False,
) -> None:
    """Read media metadata without decoding the complete file."""

    try:
        state = _state(ctx)
        try:
            manager = manager_for(state)
            asset = manager.resolve_media(target)
            path = manager.resolve_path(asset.path)
            identity = asset.model_dump(mode="json")
        except Exception:
            path = Path(target).expanduser().resolve()
            identity = {"path": str(path), "original_name": path.name}
        info = probe_media(path, ffprobe=state.config.tools.ffprobe, include_keyframes=keyframes)
        data = {**identity, "technical": info.model_dump(mode="json")}
        _emit(ctx, "inspect", data, json.dumps(data, ensure_ascii=False, indent=2))
    except Exception as error:
        _abort(ctx, "inspect", error)


@project_app.command("info")
def project_info(ctx: typer.Context) -> None:
    """Show project settings and object counts."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        data = {
            "file": str(manager.project_file),
            "revision": document.revision,
            "project": document.project.model_dump(mode="json"),
            "counts": {
                "media": len(document.media),
                "tracks": len(document.tracks),
                "clips": sum(len(track.clips) for track in document.tracks),
                "transitions": len(document.transitions),
            },
        }
        _emit(ctx, "project.info", data, json.dumps(data, ensure_ascii=False, indent=2), revision=document.revision)
    except Exception as error:
        _abort(ctx, "project.info", error)


@project_app.command("validate")
def project_validate(ctx: typer.Context) -> None:
    """Strictly validate project structure and media availability."""

    try:
        manager = manager_for(_state(ctx))
        warnings = manager.validate()
        document = manager.require_document()
        _emit(
            ctx,
            "project.validate",
            {"valid": True, "file": str(manager.project_file)},
            "[green]Project is valid.[/green]",
            warnings=warnings,
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "project.validate", error)


@project_app.command("snapshot")
def project_snapshot(
    ctx: typer.Context,
    output: Annotated[Path | None, typer.Option("--output", "-o")] = None,
) -> None:
    """Create an AI-readable complete state snapshot."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        warnings = manager.validate()
        data = compact_project(document)
        data["warnings"] = warnings
        if output:
            if output.exists():
                raise FileExistsError(f'Output "{output}" already exists.')
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _emit(
            ctx,
            "project.snapshot",
            {"snapshot": data, "output": str(output) if output else None},
            f"[green]Snapshot ready{f': {output}' if output else ''}[/green]",
            warnings=warnings,
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "project.snapshot", error)


@history_app.command("list")
def history_list(ctx: typer.Context) -> None:
    """List revision-producing operations."""

    try:
        document = manager_for(_state(ctx)).require_document()
        data = [entry.model_dump(mode="json") for entry in document.history]
        text = "\n".join(
            f"{entry.revision:>4}  {entry.action:<24} {entry.summary}"
            for entry in document.history
        ) or "No edits yet."
        _emit(ctx, "history.list", data, text, revision=document.revision)
    except Exception as error:
        _abort(ctx, "history.list", error)


@history_app.command("status")
def history_status(ctx: typer.Context) -> None:
    """Show current CutGraph branch, commit, and redo state."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        manager.cutgraph.initialize(document)
        data = manager.cutgraph.status()
        text = (
            f"Branch: {data['branch']}\n"
            f"HEAD: {data['head']}\n"
            f"Redo available: {'yes' if data['redo_available'] else 'no'}"
        )
        _emit(ctx, "history.status", data, text, revision=document.revision)
    except Exception as error:
        _abort(ctx, "history.status", error)


@history_app.command("log")
def history_log(
    ctx: typer.Context,
    start: Annotated[str, typer.Argument()] = "HEAD",
    graph: Annotated[bool, typer.Option("--graph")] = False,
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 50,
) -> None:
    """Show commit-addressed history from a branch or commit."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        manager.cutgraph.initialize(document)
        data = manager.cutgraph.log(start, limit=limit)
        prefix = "* " if graph else ""
        text = "\n".join(
            f"{prefix}{item['commit_id'][:12]}  r{item['revision']:<4} "
            f"{item['branch']:<18} {item['action']}  {item['summary']}"
            for item in data
        ) or "No commits."
        _emit(ctx, "history.log", data, text, revision=document.revision)
    except Exception as error:
        _abort(ctx, "history.log", error)


@history_app.command("diff")
def history_diff(
    ctx: typer.Context,
    range_spec: Annotated[str, typer.Argument(help="before..after")] = "main..HEAD",
) -> None:
    """Compare two revisions using clips, tracks, effects, audio, and proxy entities."""

    try:
        if ".." not in range_spec:
            raise ValueError("History diff requires before..after.")
        before, after = range_spec.split("..", 1)
        manager = manager_for(_state(ctx))
        data = manager.diff_history(before, after)
        summary = data["summary"]
        text = (
            f"Added: {summary['added']}  Removed: {summary['removed']}  "
            f"Modified: {summary['modified']}"
        )
        _emit(
            ctx,
            "history.diff",
            data,
            text,
            revision=manager.require_document().revision,
        )
    except Exception as error:
        _abort(ctx, "history.diff", error)


@history_app.command("undo")
def history_undo(ctx: typer.Context) -> None:
    """Move the current branch to its parent without deleting the future."""

    undo_command(ctx)


@history_app.command("redo")
def history_redo(ctx: typer.Context) -> None:
    """Move forward along the most recently undone CutGraph path."""

    redo_command(ctx)


@history_app.command("restore")
def history_restore(ctx: typer.Context, commit: str) -> None:
    """Restore an earlier state as a new commit on the current branch."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.restore_commit(commit)
        _emit(
            ctx,
            "history.restore",
            {"commit": commit, "revision": document.revision},
            f"[green]Restored {commit} as revision {document.revision}.[/green]",
            revision=document.revision,
        )
    except Exception as error:
        _abort(ctx, "history.restore", error)


@branch_app.command("create")
def branch_create(
    ctx: typer.Context,
    name: str,
    start: Annotated[str, typer.Option("--start")] = "HEAD",
    switch: Annotated[bool, typer.Option("--switch")] = False,
) -> None:
    """Create a branch from a commit and optionally switch to it."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        manager.cutgraph.initialize(document)
        data = manager.cutgraph.create_branch(name, start=start)
        if switch:
            document = manager.switch_branch(name)
            data["current"] = True
        _emit(ctx, "branch.create", data, f"[green]Created branch {name}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "branch.create", error)


@branch_app.command("switch")
def branch_switch(ctx: typer.Context, name: str) -> None:
    """Switch the working project to a saved branch tip."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.switch_branch(name)
        data = manager.cutgraph.status()
        _emit(ctx, "branch.switch", data, f"[green]Switched to {name}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "branch.switch", error)


@branch_app.command("list")
def branch_list(ctx: typer.Context) -> None:
    """List CutGraph branches and their tips."""

    try:
        manager = manager_for(_state(ctx))
        document = manager.require_document()
        manager.cutgraph.initialize(document)
        data = manager.cutgraph.list_branches()
        text = "\n".join(
            f"{'*' if item['current'] else ' '} {item['name']:<24} {item['commit_id'][:12]}"
            for item in data
        )
        _emit(ctx, "branch.list", data, text, revision=document.revision)
    except Exception as error:
        _abort(ctx, "branch.list", error)


@branch_app.command("accept")
def branch_accept(
    ctx: typer.Context,
    source: str,
    target: Annotated[str, typer.Option("--into")] = "main",
) -> None:
    """Fast-forward a target branch; divergent histories are never overwritten."""

    try:
        manager = manager_for(_state(ctx))
        data, document = manager.accept_branch(source, target)
        _emit(ctx, "branch.accept", data, f"[green]Accepted {source} into {target}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "branch.accept", error)


def undo_command(ctx: typer.Context) -> None:
    """Restore the immediately preceding project revision."""

    try:
        document = manager_for(_state(ctx)).undo()
        _emit(ctx, "undo", {"revision": document.revision}, f"[green]Restored revision {document.revision}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "undo", error)


def redo_command(ctx: typer.Context) -> None:
    """Reapply the most recently undone revision."""

    try:
        document = manager_for(_state(ctx)).redo()
        _emit(ctx, "redo", {"revision": document.revision}, f"[green]Restored revision {document.revision}.[/green]", revision=document.revision)
    except Exception as error:
        _abort(ctx, "redo", error)


def run_command(
    ctx: typer.Context,
    source: Annotated[str, typer.Argument(help="JSON command file or '-' for stdin.")],
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Run an atomic JSON command list."""

    try:
        raw = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8-sig")
        payload = json.loads(raw)
        manager = manager_for(_state(ctx))
        commands = payload.get("commands")
        if not isinstance(commands, list):
            raise ValueError('Batch payload requires a "commands" list.')
        project_actions = {
            "timeline.track.add", "timeline.add", "audio.add", "audio.volume",
            "audio.fade", "clip.move", "clip.duplicate", "clip.transform",
            "clip.freeze", "clip.composite", "effect.add", "effect.remove",
            "adjustment.add", "clip.split", "clip.trim", "clip.delete",
            "clip.motion", "clip.speed", "clip.speed_curve", "audio.process",
            "audio.crossfade", "audio.loudness", "transition.add",
            "transition.remove", "narration.apply",
        }
        has_project_edits = any(
            isinstance(command, dict) and command.get("action") in project_actions
            for command in commands
        )
        if not dry_run and has_project_edits and bool(payload.get("agent", True)):
            manager.ensure_experiment_branch("agent")
            payload.setdefault("actor", "agent")
            payload.setdefault("intent", "Apply Agent command batch")
        has_agent_actions = any(
            isinstance(command, dict) and command.get("action") not in project_actions
            for command in commands
        )
        result = (
            _run_agent_batch(manager, payload, dry_run=dry_run)
            if has_agent_actions
            else CommandEngine(manager).run_batch(payload, dry_run=dry_run)
        )
        document = manager.require_document()
        _emit(ctx, "run", result["data"], f"[green]Applied {len(payload.get('commands', []))} command(s).[/green]", revision=result["project_revision"])
    except Exception as error:
        _abort(ctx, "run", error)


def _run_agent_batch(manager, payload: dict[str, object], *, dry_run: bool) -> dict[str, object]:
    """Route non-timeline actions through the persistent JSON-RPC dispatcher.

    Cross-domain actions may create files or mutate the global voice store, so
    they require an explicit non-atomic batch. Timeline-only batches retain the
    CommandEngine single-transaction guarantee.
    """

    from facut.agent import action_schema
    from facut.core.command_engine import CommandEngineError

    commands = payload.get("commands")
    assert isinstance(commands, list)
    if dry_run:
        raise CommandEngineError(
            "Extended Agent actions do not support batch --dry-run; use each action's plan or dry-run command."
        )
    if bool(payload.get("atomic", True)):
        raise CommandEngineError(
            "Batches containing voice, narration-generation, or recipe actions must set atomic=false. "
            "Project-only edit batches remain fully atomic."
        )
    requests: list[str] = []
    for index, item in enumerate(commands, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("action"), str):
            raise CommandEngineError("Every command requires an action.")
        action = str(item["action"])
        schema = action_schema(action)
        if schema.get("rpc") is not True:
            raise CommandEngineError(f"Action {action} is not available through facut run.")
        params = {key: value for key, value in item.items() if key != "action"}
        requests.append(json.dumps(
            {"jsonrpc": "2.0", "id": index, "method": action, "params": params},
            ensure_ascii=True,
            separators=(",", ":"),
        ))
    requests.append('{"jsonrpc":"2.0","id":"shutdown","method":"shutdown","params":{}}')
    command_line = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "facut"]
    command_line.extend(
        ["--project", str(manager.project_file.resolve()), "serve", "--no-handshake"]
    )
    environment = os.environ.copy()
    if not getattr(sys, "frozen", False):
        source_paths = [item for item in sys.path if item and Path(item).exists()]
        environment["PYTHONPATH"] = os.pathsep.join(source_paths)
    completed = subprocess.run(
        command_line,
        input="\n".join(requests) + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environment,
        timeout=3600,
        check=False,
    )
    responses: list[object] = []
    for raw_line in completed.stdout.splitlines():
        try:
            response = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if response.get("id") == "shutdown":
            continue
        if "error" in response:
            rpc_error = response.get("error") or {}
            raise CommandEngineError(
                f"Agent action failed: {rpc_error.get('message', 'unknown JSON-RPC error')}"
            )
        responses.append(response.get("result"))
    if completed.returncode != 0:
        tail = " | ".join(completed.stderr.splitlines()[-8:])
        raise CommandEngineError(
            f"Agent batch process failed with exit code {completed.returncode}: {tail}"
        )
    if len(responses) != len(commands):
        raise CommandEngineError("Agent batch returned an incomplete response set.")
    document = manager.load()
    return {
        "status": "success",
        "command": "run",
        "data": {"results": responses, "atomic": False},
        "warnings": [],
        "errors": [],
        "project_revision": document.revision,
        "dry_run": False,
    }
