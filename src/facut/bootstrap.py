"""Small console bootstrap that keeps light lifecycle commands responsive."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from facut import __version__


def _command_name(arguments: list[str]) -> str | None:
    consumes_value = {"--project", "-p"}
    skip_next = False
    for item in arguments:
        if skip_next:
            skip_next = False
            continue
        if item in consumes_value:
            skip_next = True
            continue
        if item.startswith("-"):
            continue
        return item
    return None


def _emit(command: str, data: dict[str, Any], *, json_output: bool, quiet: bool) -> None:
    if json_output:
        print(json.dumps({
            "status": "success", "command": command, "data": data,
            "warnings": [], "errors": [], "project_revision": None,
        }, ensure_ascii=True, separators=(",", ":")))
    elif not quiet:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _emit_error(command: str, error: Exception, *, json_output: bool) -> int:
    if json_output:
        print(json.dumps({
            "status": "error", "command": command, "data": None, "warnings": [],
            "errors": [{"code": "INVALID_ARGUMENT", "message": str(error),
                        "suggestion": None, "details": {}}],
            "project_revision": None,
        }, ensure_ascii=True, separators=(",", ":")))
    else:
        print(f"Error: {error}", file=sys.stderr)
    return 2


def _lifecycle_command(arguments: list[str]) -> int | None:
    command = _command_name(arguments)
    if command not in {"cleanram", "warmup", "autoload"}:
        return None
    json_output = "--json" in arguments
    quiet = "--quiet" in arguments or "-q" in arguments
    filtered = [item for item in arguments if item not in {"--json", "--quiet", "-q"}]
    try:
        if command in {"cleanram", "warmup"}:
            parser = argparse.ArgumentParser(prog=f"facut {command}")
            parser.add_argument("command")
            parser.add_argument("--service", action="append", required=True)
            if command == "cleanram":
                parser.add_argument("--dry-run", action="store_true")
            parsed = parser.parse_args(filtered)
            if command == "cleanram":
                from facut.runtime_control import clean_services

                data = clean_services(parsed.service, dry_run=parsed.dry_run)
                response_command = "runtime.cleanram"
            else:
                from facut.runtime_control import warm_services

                data = warm_services(parsed.service)
                response_command = "runtime.warmup"
            _emit(response_command, data, json_output=json_output, quiet=quiet)
            return 0
        parser = argparse.ArgumentParser(prog="facut autoload")
        parser.add_argument("command")
        parser.add_argument("action", choices=("status", "enable", "disable"))
        parser.add_argument("service", nargs="?")
        parser.add_argument("--stop-now", action="store_true")
        parsed = parser.parse_args(filtered)
        from facut.runtime_control import (
            autoload_status,
            clean_services,
            configure_autoload,
        )

        if parsed.action == "status":
            if parsed.service is not None:
                raise ValueError("autoload status does not take a service name.")
            data = autoload_status()
        else:
            if parsed.service is None:
                raise ValueError(f"autoload {parsed.action} requires a service name or all.")
            data = configure_autoload(parsed.service, enabled=parsed.action == "enable")
            if parsed.action == "disable" and parsed.stop_now:
                data["stop"] = clean_services([parsed.service])
        _emit(
            f"runtime.autoload.{parsed.action}", data,
            json_output=json_output, quiet=quiet,
        )
        return 0
    except SystemExit as error:
        return int(error.code or 0)
    except Exception as error:
        return _emit_error(f"runtime.{command}", error, json_output=json_output)


def main() -> None:
    arguments = sys.argv[1:]
    if len(arguments) == 1 and arguments[0] == "--version":
        print(f"facut {__version__}")
        return
    if arguments and arguments[0] == "__voice_service_daemon__":
        from facut.voice.service import _daemon_entry

        _daemon_entry(arguments[1:])
        return
    if arguments and arguments[0] == "__runtime_warmup__":
        from facut.runtime_control import warmup_child

        raise SystemExit(warmup_child(arguments[1:]))
    lifecycle_result = _lifecycle_command(arguments)
    if lifecycle_result is not None:
        raise SystemExit(lifecycle_result)
    command = _command_name(arguments)
    if command not in {None, "help", "doctor"} and "--help" not in arguments and "-h" not in arguments:
        try:
            from facut.runtime_control import schedule_default_warmup

            schedule_default_warmup()
        except Exception:
            # Background warmup is a latency optimization.  It must never make
            # the requested deterministic edit command unavailable.
            pass
    from facut.cli.main import main as cli_main

    cli_main()
