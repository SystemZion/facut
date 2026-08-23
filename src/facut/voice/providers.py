"""Local voice synthesis provider protocol; no silent online fallback."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from uuid import uuid4
from typing import Any

from facut.exceptions import InvalidArgumentError, NotImplementedFacutError

from .models import VoiceProfile
from .store import default_voice_home


def _provider_config_path() -> Path:
    return default_voice_home() / "provider.json"


def _configured_provider() -> str:
    path = _provider_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ""
    return str(payload.get("executable") or "")


def configure_provider(executable: str | Path) -> dict[str, Any]:
    """Persist one local provider executable using an atomic config write."""

    candidate = Path(executable).expanduser().resolve()
    if not candidate.is_file():
        raise FileNotFoundError(f'Voice provider "{candidate}" was not found.')
    destination = _provider_config_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {"protocol": "facut-voice-provider/1.0", "executable": str(candidate)}
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, destination)
    return provider_status()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def build_provider_request(
    profile: VoiceProfile,
    profile_directory: str | Path,
    lines: list[dict[str, Any]],
    output_directory: str | Path,
) -> tuple[dict[str, Any], Path]:
    """Build the stable local-provider request without starting a process."""

    output_root = Path(output_directory).expanduser().resolve()
    request_id = f"voice_request_{uuid4().hex}"
    # Every request owns a unique directory. A timed-out provider can never
    # overwrite a later request's outputs, even if it attempts a late write.
    destination = output_root / ".facut-requests" / request_id
    destination.mkdir(parents=True, exist_ok=True)
    request = {
        "protocol": "facut-voice-provider/1.0",
        "action": "synthesize",
        "request_id": request_id,
        "profile": profile.public_dict(),
        "profile_directory": str(Path(profile_directory).resolve()),
        "output_directory": str(destination),
        "lines": lines,
    }
    return request, destination


def validate_provider_response(
    payload: dict[str, Any],
    destination: str | Path,
    profile_id: str,
    *,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate provider output paths and attach content hashes.

    Both the one-shot provider adapter and the persistent loopback service use
    this boundary, so neither path can trust files outside the requested output
    directory.
    """

    output_root = Path(destination).expanduser().resolve()
    if payload.get("status") != "success" or not isinstance(payload.get("outputs"), list):
        raise RuntimeError("Local voice provider did not return a successful outputs list.")
    request_id = str((request or {}).get("request_id") or "")
    response_request_id = payload.get("request_id")
    if response_request_id is not None and str(response_request_id) != request_id:
        raise RuntimeError("Local voice provider returned a response for another request.")
    expected_lines = list((request or {}).get("lines") or [])
    if expected_lines and len(payload["outputs"]) != len(expected_lines):
        raise RuntimeError(
            "Local voice provider output count does not match the current request."
        )
    outputs = []
    for index, item in enumerate(payload["outputs"]):
        path = Path(str(item["output"])).expanduser().resolve()
        if output_root != path.parent and output_root not in path.parents:
            raise RuntimeError("Local voice provider returned an output outside the requested directory.")
        if path.suffix.casefold() != ".wav" or not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Local voice provider returned a missing or invalid WAV output.")
        expected_text = (
            str(
                expected_lines[index].get("text")
                or expected_lines[index].get("draft_text")
                or ""
            )
            if expected_lines
            else ""
        )
        outputs.append(
            {
                **item,
                "output": str(path),
                "sha256": _hash_file(path),
                "request_id": request_id or None,
                "text_sha256": hashlib.sha256(expected_text.encode("utf-8")).hexdigest(),
            }
        )
    return {
        "status": "success",
        "provider": payload.get("provider"),
        "model_version": payload.get("model_version"),
        "profile_id": profile_id,
        "request_id": request_id or None,
        "outputs": outputs,
        "warnings": payload.get("warnings", []),
    }


def invoke_provider_request(
    executable: str | Path,
    request: dict[str, Any],
    *,
    timeout: float = 300,
    device: str = "auto",
    require_cuda: bool = False,
) -> dict[str, Any]:
    """Invoke a facut-voice-provider/1.0 executable once."""

    environment = os.environ.copy()
    requested_device = resolve_voice_device(
        device,
        require_cuda=require_cuda,
        environment=environment,
    )
    environment["FACUT_VOICE_DEVICE"] = requested_device
    environment["FACUT_VOICE_REQUIRE_CUDA"] = "1" if require_cuda else "0"

    completed = subprocess.run(
        [str(executable), "--facut-voice-json"],
        input=json.dumps(request, ensure_ascii=True),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        env=environment,
    )
    if completed.returncode:
        detail = completed.stderr[-2000:].strip()
        if not detail:
            detail = (
                f"provider exited with code {completed.returncode} while using "
                f"device={requested_device}; the inference runtime may have crashed"
            )
        raise RuntimeError(f"Local voice provider failed: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Local voice provider returned invalid JSON.") from error


def resolve_voice_device(
    device: str,
    *,
    require_cuda: bool = False,
    environment: dict[str, str] | None = None,
) -> str:
    """Resolve a safe provider device consistently for one-shot and service paths."""

    requested_device = str(device).casefold()
    if requested_device not in {"auto", "cuda", "cpu"}:
        raise InvalidArgumentError("Voice device must be auto, cuda, or cpu.")
    if requested_device == "cpu" and require_cuda:
        raise InvalidArgumentError(
            "Voice device cpu cannot be combined with --require-cuda.",
            suggestion="Use --device cuda, or remove --require-cuda.",
        )
    current_environment = environment if environment is not None else os.environ
    # A configured CPU overlay exists specifically to avoid importing a CUDA
    # PyTorch build when an eGPU is disconnected. Prefer that safe runtime in
    # auto mode, but never override an explicit CUDA requirement.
    if (
        requested_device == "auto"
        and not require_cuda
        and current_environment.get("FACUT_CPU_TORCH_OVERLAY")
    ):
        requested_device = "cpu"
    return requested_device


def provider_status(explicit: str | Path | None = None) -> dict[str, Any]:
    source = "none"
    requested = ""
    if explicit:
        requested, source = str(explicit), "argument"
    elif os.environ.get("FACUT_VOICE_PROVIDER"):
        requested, source = str(os.environ["FACUT_VOICE_PROVIDER"]), "environment"
    elif persisted := _configured_provider():
        requested, source = persisted, "configuration"
    executable = None
    if requested:
        candidate = Path(requested).expanduser()
        executable = str(candidate.resolve()) if candidate.is_file() else shutil.which(requested)
    return {
        "protocol": "facut-voice-provider/1.0",
        "configured": bool(requested),
        "available": bool(executable),
        "executable": executable,
        "source": source,
        "offline_only": True,
    }


def synthesize_with_provider(
    profile: VoiceProfile,
    profile_directory: str | Path,
    lines: list[dict[str, Any]],
    output_directory: str | Path,
    *,
    provider: str | Path | None = None,
    timeout: float = 300,
    device: str = "auto",
    require_cuda: bool = False,
) -> dict[str, Any]:
    status = provider_status(provider)
    if not status["available"]:
        raise NotImplementedFacutError(
            "No local digital-voice synthesis provider is configured.",
            suggestion=(
                "Set FACUT_VOICE_PROVIDER to an executable implementing "
                "facut-voice-provider/1.0. FACUT will not upload voice samples automatically."
            ),
            details=status,
        )
    request, destination = build_provider_request(
        profile, profile_directory, lines, output_directory
    )
    payload = invoke_provider_request(
        status["executable"],
        request,
        timeout=timeout,
        device=device,
        require_cuda=require_cuda,
    )
    return validate_provider_response(
        payload, destination, profile.id, request=request
    )
