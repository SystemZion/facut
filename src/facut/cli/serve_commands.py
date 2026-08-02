"""Persistent newline-delimited JSON-RPC session for agents."""

from __future__ import annotations

import json
import sys
from typing import Annotated, Any

import typer

from facut import __version__
from facut.cli.common import compact_project, manager_for, public_error
from facut.core.command_engine import CommandEngine


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
            params = request.get("params") or {}
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
