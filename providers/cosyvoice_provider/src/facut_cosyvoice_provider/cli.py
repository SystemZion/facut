"""Implementation of the facut-voice-provider/1.0 process protocol."""

from __future__ import annotations

from contextlib import redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sys
import time
import traceback
from typing import Any

# Respect an explicit CPU service before importing torch.  The normal default
# remains CUDA when available.
if os.environ.get("FACUT_VOICE_DEVICE", "auto").casefold() == "cpu":
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    # A CUDA-enabled PyTorch build can crash during import when a Windows eGPU
    # is physically disconnected.  Keep the normal CUDA runtime untouched and
    # allow the installer to provide a small, explicit CPU wheel overlay for
    # this mode instead of replacing the user's GPU packages.
    if cpu_overlay := os.environ.get("FACUT_CPU_TORCH_OVERLAY"):
        sys.path.insert(0, str(Path(cpu_overlay).expanduser().resolve()))

import numpy as np
import soundfile
from scipy.signal import butter, sosfilt
import torch
import torchaudio


PROTOCOL = "facut-voice-provider/1.0"
MODEL_VERSION = "Fun-CosyVoice3-0.5B-2512"
REFERENCE_PROCESSING_VERSION = "v4-daily-chat"
STREAM_PROTOCOL = "facut-voice-provider-jsonl/1.0"
_MODEL_CONTEXT: dict[str, Any] | None = None

DELIVERY_INSTRUCTIONS = {
    "natural": (
        "请用自然、松弛的日常VLOG口吻表达，像在和熟悉的人分享眼前发生的事情。"
        "不要播音腔，不要每个字同样用力；语速中等，句尾自然收住。"
    ),
    "broadcast": (
        "请用清晰、稳定、有信息感的口吻表达，吐字准确，但避免新闻联播式的过度字正腔圆。"
        "层次分明，重点词适度强调。"
    ),
    "chat": (
        "请像面对熟悉的朋友聊天一样表达，口语化、松弛，允许轻微思考和自然呼吸。"
        "不要朗读腔，句子之间有真实交流感。"
    ),
    "daily-chat": (
        "请用旅行现场随口交流的方式表达，像刚看到眼前事物后自然说出来。"
        "保留真实的轻重音、短暂停顿和少量口语感；不要朗读腔，不要把每句话组织得过分完整。"
    ),
    "comedy": (
        "请用轻松俏皮、带一点真实笑意和反差感的口吻表达。"
        "笑点前稍作停顿，但不要使用夸张的卡通腔或刻意大笑。"
    ),
    "excited": (
        "请用真实兴奋、略带惊喜的旅行VLOG口吻表达，节奏稍快、能量更高。"
        "不要喊叫，也不要从头到尾保持同一强度。"
    ),
    "natural-vlog": (
        "请用自然、松弛的日常VLOG口吻表达，像在和熟悉的朋友聊天。"
        "不要播音腔，不要每个字同样用力；语速中等，句尾自然收住，允许轻微呼吸和思考停顿。"
    ),
    "warm": "请用温暖、亲近但不过分表演的口吻表达，语气柔和，重点自然。",
    "reflective": "请用安静、真诚、略带感慨的口吻表达，语速稍慢，保留自然停顿。",
    "energetic": "请用轻快、有活力但不夸张的旅行VLOG口吻表达，节奏自然，有真实的兴奋感。",
    "documentary": "请用克制、清晰、有叙事感但不播音腔的纪录片口吻表达。",
}

REFERENCE_HINTS = {
    "natural": ("delivery:natural", "prompt_002", "prompt_007", "刚走", "如果"),
    "broadcast": ("delivery:broadcast", "prompt_003", "prompt_008", "现在是", "接下来"),
    "chat": ("delivery:chat", "prompt_002", "prompt_006", "我跟你说", "你看", "你觉得"),
    "daily-chat": (
        "delivery:daily-chat", "category:conversation", "chat0", "反正", "你看人家",
        "亲自来", "看运气", "one way",
    ),
    "comedy": ("delivery:comedy", "拿着三部手机", "不同的方向"),
    "excited": ("delivery:excited", "prompt_010", "快看", "真的到了", "开心"),
    "natural-vlog": ("prompt_002", "prompt_007", "刚走", "你觉得", "如果", "时候"),
    "warm": (
        "prompt_004",
        "prompt_009",
        "prompt_010",
        "刚刚好",
        "有些",
        "孩子",
        "开心",
    ),
    "reflective": ("prompt_007", "prompt_009", "愿意", "瞬间", "真实"),
    "energetic": ("prompt_001", "prompt_010", "出发", "开心"),
    "documentary": ("prompt_003", "prompt_008", "现在是", "画面", "变化"),
}


def _configuration() -> dict[str, str]:
    candidates = []
    if configured := os.environ.get("FACUT_COSYVOICE_CONFIG"):
        candidates.append(Path(configured))
    candidates.append(Path(sys.prefix).resolve().parent / "facut-cosyvoice.json")
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        return {str(key): str(value) for key, value in payload.items()}
    return {}


def _paths() -> tuple[Path, Path, Path | None]:
    config = _configuration()
    runtime = Path(
        os.environ.get("FACUT_COSYVOICE_RUNTIME") or config.get("runtime") or ""
    ).expanduser()
    model = Path(
        os.environ.get("FACUT_COSYVOICE_MODEL") or config.get("model") or ""
    ).expanduser()
    wetext_value = os.environ.get("FACUT_COSYVOICE_WETEXT") or config.get("wetext")
    wetext = Path(wetext_value).expanduser() if wetext_value else None
    if not runtime.is_dir() or not (runtime / "cosyvoice").is_dir():
        raise FileNotFoundError("CosyVoice runtime directory is missing.")
    if not model.is_dir() or not (model / "cosyvoice3.yaml").is_file():
        raise FileNotFoundError("CosyVoice3 model directory is missing.")
    return runtime.resolve(), model.resolve(), wetext.resolve() if wetext else None


def _soundfile_load_wav(path: str, target_sr: int, min_sr: int = 16000) -> torch.Tensor:
    samples, sample_rate = soundfile.read(path, dtype="float32", always_2d=True)
    if sample_rate < min_sr:
        raise ValueError(f"WAV sample rate {sample_rate} is below {min_sr}.")
    speech = torch.from_numpy(samples.T).mean(dim=0, keepdim=True)
    if sample_rate != target_sr:
        speech = torchaudio.transforms.Resample(sample_rate, target_sr)(speech)
    return speech


def _safe_relative(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("Voice sample path escapes its profile directory.")
    return candidate


def _select_reference(
    profile: dict[str, Any], profile_directory: Path, delivery: str
) -> tuple[Path, str, str]:
    candidates: list[tuple[float, Path, str, str]] = []
    for sample in profile.get("samples") or []:
        transcript = str(sample.get("transcript") or "").strip()
        if not transcript:
            continue
        path = _safe_relative(profile_directory, str(sample.get("stored_path") or ""))
        if not path.is_file():
            continue
        data, sample_rate = soundfile.read(path, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        duration = mono.size / sample_rate
        if duration < 3 or duration > 20:
            continue
        rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
        peak = float(np.max(np.abs(mono)))
        score = 20 * np.log10(max(rms, 1e-9)) - abs(duration - 7) * 0.15
        searchable = (
            f"delivery:{sample.get('delivery') or ''} "
            f"category:{sample.get('category') or ''} "
            f"{sample.get('original_name') or ''} {transcript}"
        )
        score += sum(
            3.0 for hint in REFERENCE_HINTS.get(delivery, ()) if hint in searchable
        )
        if peak >= 0.999:
            score -= 20
        candidates.append((float(score), path, transcript, str(sample.get("id") or "")))
    if not candidates:
        raise ValueError(
            "No 3-20 second voice sample with an exact transcript is available."
        )
    _, source, transcript, sample_id = max(candidates, key=lambda item: item[0])
    derived = profile_directory / "derived" / "cosyvoice3"
    derived.mkdir(parents=True, exist_ok=True)
    identity = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    reference = derived / f"reference-{REFERENCE_PROCESSING_VERSION}-{identity}.wav"
    if not reference.is_file():
        data, sample_rate = soundfile.read(source, dtype="float32", always_2d=True)
        mono = data.mean(axis=1)
        mono = sosfilt(
            butter(2, 70, btype="highpass", fs=sample_rate, output="sos"), mono
        )
        # Preserve breaths while removing only long, near-silent edges.
        peak = float(np.max(np.abs(mono)))
        activity_threshold = max(10 ** (-50 / 20), peak * 0.008)
        active = np.flatnonzero(np.abs(mono) >= activity_threshold)
        if active.size:
            padding = round(sample_rate * 0.12)
            start = max(0, int(active[0]) - padding)
            end = min(mono.size, int(active[-1]) + padding + 1)
            mono = mono[start:end]
        rms = float(np.sqrt(np.mean(np.square(mono), dtype=np.float64)))
        peak = float(np.max(np.abs(mono)))
        if peak <= 1e-7 or rms <= 1e-9:
            raise ValueError("Selected voice sample contains no usable signal.")
        gain = min((10 ** (-23 / 20)) / rms, (10 ** (-3 / 20)) / peak)
        mono = np.asarray(mono * gain, dtype=np.float32)
        temporary = reference.with_suffix(".wav.tmp")
        soundfile.write(temporary, mono, sample_rate, subtype="PCM_16", format="WAV")
        os.replace(temporary, reference)
    return reference, transcript, sample_id


def _line_text(line: dict[str, Any]) -> str:
    value = line.get("text") or line.get("draft_text")
    text = str(value or "").strip()
    if not text:
        raise ValueError("Every synthesis line requires text or draft_text.")
    return text


def _delivery(line: dict[str, Any], profile: dict[str, Any]) -> str:
    value = str(line.get("delivery") or profile.get("style") or "natural-vlog").strip()
    if value == "reference":
        return value
    if value not in DELIVERY_INSTRUCTIONS:
        choices = ", ".join([*DELIVERY_INSTRUCTIONS, "reference"])
        raise ValueError(f'Unsupported delivery "{value}". Choose one of: {choices}.')
    return value


def _instruction(line: dict[str, Any], delivery: str) -> str:
    custom = str(line.get("instruction") or "").strip()
    if len(custom) > 300:
        raise ValueError("Voice instruction must be 300 characters or fewer.")
    guidance = " ".join(
        part for part in (DELIVERY_INSTRUCTIONS.get(delivery, ""), custom) if part
    )
    return f"You are a helpful assistant. {guidance}<|endofprompt|>"


def _split_for_prosody(text: str, max_chars: int = 38) -> list[str]:
    sentences = [
        item.strip() for item in re.split(r"(?<=[。！？!?；;])", text) if item.strip()
    ]
    chunks: list[str] = []
    for sentence in sentences or [text]:
        if len(sentence) <= max_chars:
            chunks.append(sentence)
            continue
        pieces = [item for item in re.split(r"(?<=[，,、：:])", sentence) if item]
        current = ""
        for piece in pieces:
            if current and len(current) + len(piece) > max_chars:
                chunks.append(current.strip())
                current = piece
            else:
                current += piece
        if current.strip():
            chunks.append(current.strip())
    return chunks


def _pause_seconds(chunk: str) -> float:
    if chunk.endswith(("。", "！", "？", "!", "?")):
        return 0.24
    if chunk.endswith(("；", ";", "：", ":")):
        return 0.18
    return 0.12


def _output_path(
    directory: Path, profile_id: str, index: int, text: str, line: dict[str, Any]
) -> Path:
    identity = {
        "profile": profile_id,
        "text": text,
        "delivery": line.get("delivery"),
        "instruction": line.get("instruction"),
        "speed": line.get("speed", 1.0),
        "candidate": line.get("candidate_index", 0),
        "reference_processing": REFERENCE_PROCESSING_VERSION,
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]
    return directory / f"line-{index + 1:03d}-{digest}.wav"


def _generation_seed(profile_id: str, text: str, line: dict[str, Any]) -> int:
    """Return a stable, candidate-specific seed for reproducible audition takes."""

    identity = {
        "model": MODEL_VERSION,
        "profile": profile_id,
        "text": text,
        "delivery": line.get("delivery"),
        "instruction": line.get("instruction"),
        "speed": line.get("speed", 1.0),
        "candidate": line.get("candidate_index", 0),
    }
    digest = hashlib.sha256(
        json.dumps(identity, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:4], "big") & 0x7FFFFFFF


def _load_model_context() -> dict[str, Any]:
    """Load CosyVoice once per provider process and expose honest device state."""

    global _MODEL_CONTEXT
    if _MODEL_CONTEXT is not None:
        return _MODEL_CONTEXT
    started = time.perf_counter()
    runtime, model_path, wetext_path = _paths()
    requested_device = os.environ.get("FACUT_VOICE_DEVICE", "auto").casefold()
    if requested_device not in {"auto", "cuda", "cpu"}:
        raise ValueError("FACUT_VOICE_DEVICE must be auto, cuda, or cpu.")
    cuda_available = torch.cuda.is_available()
    require_cuda = os.environ.get("FACUT_VOICE_REQUIRE_CUDA") == "1"
    if require_cuda and not cuda_available:
        raise RuntimeError(
            "CUDA was required, but PyTorch cannot access a CUDA device."
        )
    if requested_device == "cuda" and not cuda_available:
        raise RuntimeError(
            "CUDA was requested, but PyTorch cannot access a CUDA device."
        )
    device = "cuda" if cuda_available and requested_device != "cpu" else "cpu"
    matcha = runtime / "third_party" / "Matcha-TTS"
    sys.path.insert(0, str(runtime))
    sys.path.insert(0, str(matcha))
    if wetext_path is not None:
        import wetext.wetext as wetext_module

        wetext_module.snapshot_download = lambda *args, **kwargs: str(wetext_path)

    import cosyvoice.cli.frontend as frontend_module
    from cosyvoice.cli.cosyvoice import CosyVoice3

    frontend_module.load_wav = _soundfile_load_wav
    with redirect_stdout(sys.stderr):
        model = CosyVoice3(str(model_path), fp16=device == "cuda")
    gpu = torch.cuda.get_device_name(0) if device == "cuda" else None
    vram = None
    if device == "cuda":
        vram = {
            "allocated_mib": round(torch.cuda.memory_allocated(0) / 1024 / 1024, 1),
            "reserved_mib": round(torch.cuda.memory_reserved(0) / 1024 / 1024, 1),
        }
    _MODEL_CONTEXT = {
        "model": model,
        "model_path": model_path,
        "device": device,
        "compute_type": "float16" if device == "cuda" else "float32",
        "cuda": device == "cuda",
        "gpu": gpu,
        "vram": vram,
        "load_seconds": round(time.perf_counter() - started, 3),
    }
    return _MODEL_CONTEXT


def synthesize(request: dict[str, Any]) -> dict[str, Any]:
    if request.get("protocol") != PROTOCOL or request.get("action") != "synthesize":
        raise ValueError("Unsupported FACUT voice provider request.")
    profile = request.get("profile") or {}
    consent = profile.get("consent") or {}
    if consent.get("relationship") not in {"self", "authorized"}:
        raise PermissionError("Voice profile lacks explicit self/authorized consent.")
    if profile.get("status") == "invalid":
        raise ValueError("Invalid voice profiles cannot be synthesized.")
    lines = request.get("lines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("At least one narration line is required.")

    context = _load_model_context()
    model = context["model"]
    profile_directory = Path(str(request["profile_directory"])).resolve()
    output_directory = Path(str(request["output_directory"])).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    outputs: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise ValueError("Narration lines must be JSON objects.")
        text = _line_text(line)
        delivery = _delivery(line, profile)
        reference, transcript, reference_sample_id = _select_reference(
            profile, profile_directory, delivery
        )
        prompt = f"You are a helpful assistant.<|endofprompt|>{transcript}"
        instruction = _instruction(line, delivery)
        segments = _split_for_prosody(text)
        destination = _output_path(
            output_directory, str(profile.get("id")), index, text, line
        )
        if (
            not line.get("force")
            and destination.is_file()
            and destination.stat().st_size > 44
        ):
            info = soundfile.info(destination)
            outputs.append(
                {
                    "output": str(destination),
                    "line_index": index,
                    "text": text,
                    "duration": info.frames / info.samplerate,
                    "sample_rate": info.samplerate,
                    "cached": True,
                    "delivery": delivery,
                    "segments": segments,
                    "reference_sample_id": reference_sample_id,
                    "candidate_index": int(line.get("candidate_index", 0)),
                    "timeline_range": line.get("timeline_range"),
                    "request_id": request.get("request_id"),
                }
            )
            continue
        started = time.perf_counter()
        seed = _generation_seed(str(profile.get("id")), text, line)
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        if context["cuda"]:
            torch.cuda.manual_seed_all(seed)
        with redirect_stdout(sys.stderr):
            parts: list[torch.Tensor] = []
            for segment_index, segment in enumerate(segments):
                if delivery == "reference":
                    generated = model.inference_zero_shot(
                        segment,
                        prompt,
                        str(reference),
                        stream=False,
                        speed=float(line.get("speed", 1.0)),
                    )
                else:
                    generated = model.inference_instruct2(
                        segment,
                        instruction,
                        str(reference),
                        stream=False,
                        speed=float(line.get("speed", 1.0)),
                    )
                parts.extend(item["tts_speech"].detach().cpu() for item in generated)
                if segment_index < len(segments) - 1:
                    pause = round(model.sample_rate * _pause_seconds(segment))
                    parts.append(torch.zeros((1, pause), dtype=torch.float32))
        if not parts:
            raise RuntimeError("CosyVoice3 returned no audio.")
        speech = torch.cat(parts, dim=1).squeeze(0).numpy()
        temporary = destination.with_suffix(".wav.tmp")
        soundfile.write(
            temporary, speech, model.sample_rate, subtype="PCM_16", format="WAV"
        )
        os.replace(temporary, destination)
        duration = speech.shape[0] / model.sample_rate
        outputs.append(
            {
                "output": str(destination),
                "line_index": index,
                "text": text,
                "duration": duration,
                "sample_rate": model.sample_rate,
                "cached": False,
                "synthesis_seconds": round(time.perf_counter() - started, 3),
                "delivery": delivery,
                "segments": segments,
                "reference_sample_id": reference_sample_id,
                "candidate_index": int(line.get("candidate_index", 0)),
                "generation_seed": seed,
                "timeline_range": line.get("timeline_range"),
                "request_id": request.get("request_id"),
            }
        )
    return {
        "status": "success",
        "request_id": request.get("request_id"),
        "provider": "facut-cosyvoice3-local",
        "model_version": MODEL_VERSION,
        "device": context["device"],
        "compute_type": context["compute_type"],
        "outputs": outputs,
        "warnings": [],
    }


def _stream_ready() -> dict[str, Any]:
    context = _load_model_context()
    return {
        "status": "ready",
        "protocol": STREAM_PROTOCOL,
        "provider": "facut-cosyvoice3-local",
        "model_version": MODEL_VERSION,
        "model_path": str(context["model_path"]),
        "device": context["device"],
        "compute_type": context["compute_type"],
        "cuda": context["cuda"],
        "gpu": context["gpu"],
        "vram": context["vram"],
        "model_load_seconds": context["load_seconds"],
        "persistent": True,
    }


def _run_jsonl() -> None:
    print(
        json.dumps(_stream_ready(), ensure_ascii=True, separators=(",", ":")),
        flush=True,
    )
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
            if request.get("action") == "shutdown":
                return
            response = synthesize(request)
        except Exception as error:
            print(
                f"{error.__class__.__name__}: {error}\n"
                + traceback.format_exc()
                .encode("ascii", "backslashreplace")
                .decode("ascii"),
                file=sys.stderr,
            )
            response = {
                "status": "error",
                "error": {"code": "VOICE_PROVIDER_FAILED", "message": str(error)},
            }
        print(
            json.dumps(response, ensure_ascii=True, separators=(",", ":")), flush=True
        )


def main() -> None:
    if "--facut-voice-jsonl" in sys.argv:
        try:
            _run_jsonl()
        except Exception as error:
            print(f"{error.__class__.__name__}: {error}", file=sys.stderr)
            print(
                traceback.format_exc()
                .encode("ascii", "backslashreplace")
                .decode("ascii"),
                file=sys.stderr,
            )
            raise SystemExit(1) from error
        return
    if "--facut-voice-json" not in sys.argv:
        print("facut-cosyvoice-provider requires --facut-voice-json", file=sys.stderr)
        raise SystemExit(2)
    try:
        request = json.load(sys.stdin)
        response = synthesize(request)
    except Exception as error:
        print(f"{error.__class__.__name__}: {error}", file=sys.stderr)
        print(
            traceback.format_exc().encode("ascii", "backslashreplace").decode("ascii"),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
    print(json.dumps(response, ensure_ascii=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
