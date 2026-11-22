"""Build a truthful homepage GIF from actual FACUT CLI renders.

This is a workflow visualization, not a screen recording or an AI session.
All source footage is generated. Work artifacts remain in an external directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    """Hash this small generated artifact."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, data: object) -> None:
    """Write generated build artifacts without overwriting earlier results."""
    with path.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run(argv: list[str], *, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Execute safely with a bounded timeout and hidden Windows child windows."""
    result = subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=240,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed ({result.returncode}):\n{result.stderr[-4000:]}\n{result.stdout[-4000:]}")
    return result


def font(size: int, *, mono: bool = False) -> ImageFont.ImageFont:
    """Use installed fonts; do not copy fonts into the repository."""
    candidates = (
        ["consola.ttf", "DejaVuSansMono.ttf"]
        if mono else ["segoeui.ttf", "DejaVuSans.ttf"]
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--assets-dir", type=Path, default=REPO / "docs" / "assets")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args()
    work = args.work_dir.expanduser().resolve()
    assets = args.assets_dir.expanduser().resolve()
    if work.exists():
        raise SystemExit("Choose a new work directory; previous artifacts are preserved.")
    if work == REPO or REPO in work.parents:
        raise SystemExit("Work artifacts must be outside this repository.")
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("workflow-demo.gif", "demo-verification.json", "social-preview.png"):
        if (assets / name).exists():
            raise SystemExit(f"Refusing to overwrite {name}. Choose a new assets directory.")
    work.mkdir(parents=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONUTF8"] = "1"
    env["FACUT_CONFIG"] = str(work / "config.toml")
    env["FACUT_VOICE_HOME"] = str(work / "unused-voice-home")
    # Use the configured maintainer toolkit on Windows, with a portable fallback.
    sys.path.insert(0, str(REPO / "src"))
    from facut.media.tools import find_executable
    if not args.ffmpeg and os.name == "nt":
        try:
            import mediakit as mk
            args.ffmpeg = str(mk.FFMPEG)
        except ImportError:
            pass
    ffmpeg = find_executable("ffmpeg", args.ffmpeg)
    ffprobe = find_executable("ffprobe", args.ffprobe or os.environ.get("FACUT_FFPROBE"))
    env["FACUT_FFMPEG"] = ffmpeg
    env["FACUT_FFPROBE"] = ffprobe
    (work / "config.toml").write_text("[runtime]\nautoload = false\n", encoding="utf-8")
    transcript: list[dict] = []

    def cli(*arguments: str) -> dict:
        started = time.perf_counter()
        result = run([sys.executable, "-m", "facut", "--json", *arguments], env=env, cwd=work)
        payload = json.loads(result.stdout)
        elapsed = time.perf_counter() - started
        transcript.append({"arguments": list(arguments), "seconds": round(elapsed, 3), "stdout": payload, "stderr": result.stderr})
        save_json(work / f"command-{len(transcript):02d}.json", transcript[-1])
        if payload.get("status") != "success":
            raise RuntimeError(f"FACUT did not succeed: {payload}")
        print(f"{payload['command']}: success ({elapsed:.2f}s)", flush=True)
        return payload

    source = work / "source.mp4"
    run([
        ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-n",
        "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=30:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=6",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "24",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-af", "volume=0.04",
        "-shortest", str(source),
    ], env=env, cwd=work)
    source_hash = digest(source)
    project = work / "project"
    cli("init", str(project), "--width", "960", "--height", "540", "--fps", "30")
    prefix = ("-p", str(project))
    imported = cli(*prefix, "import", str(source))
    media_id = imported["data"]["media"][0]["id"]
    cli(*prefix, "timeline", "track", "add", "--type", "video", "--name", "V1")
    added = cli(*prefix, "timeline", "add", media_id, "--track", "V1", "--at", "0", "--in", "0", "--out", "6s")
    clip_id = added["data"]["result"]["id"]
    before = project / "previews" / "before.mp4"
    after = project / "previews" / "after.mp4"
    cli(*prefix, "render", "--width", "960", "--height", "540", "--fps", "30", "--hardware", "none", "--fast-path", "off", "--no-incremental", "--output", str(before))
    cli(*prefix, "edit", "Make this generated shot black and white.")
    incoming = work / "agent.patch.json"
    save_json(incoming, {
        "request": "Make this generated shot black and white.",
        "operations": [{
            "action": "effect.add",
            "parameters": {"clip_id": clip_id, "effect_type": "black-white", "parameters": {}},
            "reason": "Predefined monochrome test of the generated color chart.",
            "evidence": [clip_id], "confidence": 1.0, "approved": False,
        }],
    })
    planned = work / "planned.patch.json"
    cli(*prefix, "review", "patch", "plan", str(incoming), "--output", str(planned))
    before_preview_hash = digest(project / "facut.json")
    cli(*prefix, "review", "patch", "preview", str(planned))
    assert digest(project / "facut.json") == before_preview_hash, "Preview modified the project"
    # Demonstrate that a draft is rejected, rather than assuming the gate works.
    rejection = subprocess.run(
        [sys.executable, "-m", "facut", "--json", *prefix, "review", "patch", "apply", str(planned), "--approved-only", "--dry-run"],
        env=env, cwd=work, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    rejection_data = json.loads(rejection.stdout)
    assert rejection.returncode != 0 and rejection_data["status"] == "error"
    assert digest(project / "facut.json") == before_preview_hash
    save_json(work / "draft-rejection.json", rejection_data)
    approved = json.loads(planned.read_text(encoding="utf-8"))
    approved["status"] = "approved"
    approved["operations"][0]["approved"] = True
    approved_path = work / "approved.patch.json"
    save_json(approved_path, approved)
    cli(*prefix, "review", "patch", "apply", str(approved_path), "--approved-only", "--dry-run")
    assert digest(project / "facut.json") == before_preview_hash
    cli(*prefix, "review", "patch", "apply", str(approved_path), "--approved-only")
    cli(*prefix, "render", "--width", "960", "--height", "540", "--fps", "30", "--hardware", "none", "--fast-path", "off", "--no-incremental", "--output", str(after))
    assert digest(source) == source_hash, "Source media changed"
    durations = []
    for movie in (before, after):
        result = run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(movie)], env=env, cwd=work)
        durations.append(float(json.loads(result.stdout)["format"]["duration"]))
        run([ffmpeg, "-v", "error", "-nostdin", "-i", str(movie), "-f", "null", "-"], env=env, cwd=work)
    assert max(abs(value - 6) for value in durations) <= 1 / 30
    shots: dict[str, list[Image.Image]] = {}
    for label, movie in (("before", before), ("after", after)):
        run([ffmpeg, "-v", "error", "-nostdin", "-n", "-i", str(movie), "-vf", "fps=2,scale=456:256", str(work / f"{label}-%02d.png")], env=env, cwd=work)
        shots[label] = [Image.open(path).convert("RGB") for path in sorted(work.glob(f"{label}-*.png"))]
    def chroma(im: Image.Image) -> float:
        pixels = list(im.resize((64, 36)).getdata())
        return sum(max(p) - min(p) for p in pixels) / len(pixels)
    assert chroma(shots["before"][0]) > 30
    assert chroma(shots["after"][0]) < 5, "Monochrome effect did not reach the render"
    steps = [
        ("01 / IMPORT", "$ facut import source.mp4", "Generated input; original hash recorded."),
        ("02 / REQUEST", '$ facut edit "Make this shot black and white."', "Feedback captured. No autonomous LLM interpretation."),
        ("03 / PLAN", "$ facut review patch plan agent.patch.json", "A structured effect operation, tied to real evidence."),
        ("04 / REVIEW", "$ facut review patch preview planned.patch.json", "Semantic diff verified; project hash unchanged."),
        ("05 / APPROVE", "$ facut review patch apply approved.patch.json", "Unapproved operation rejected; test approval is explicit."),
        ("06 / RENDER", "$ facut render --hardware none", "Real rendered result. Source untouched; same duration."),
    ]
    frames = []
    for step_index, (title, command, detail) in enumerate(steps):
        for tick in range(8):
            canvas = Image.new("RGB", (1000, 620), "#0b1220")
            draw = ImageDraw.Draw(canvas)
            draw.text((32, 22), "FACUT", font=font(34), fill="#eef6ff")
            draw.text((180, 32), "Agent decisions. Deterministic edits.", font=font(20), fill="#9eb3c9")
            draw.text((32, 83), title, font=font(24), fill="#68dbc8")
            draw.rounded_rectangle((26, 134, 974, 236), radius=14, fill="#111e31")
            draw.text((42, 153), command, font=font(18, mono=True), fill="#eef6ff")
            draw.text((42, 191), detail, font=font(17), fill="#9eb3c9")
            draw.text((32, 257), "BEFORE / actual FACUT render", font=font(17), fill="#9eb3c9")
            draw.text((512, 257), "AFTER / approved test patch", font=font(17), fill="#68dbc8")
            canvas.paste(shots["before"][tick % len(shots["before"])], (32, 289))
            if step_index >= 4:
                canvas.paste(shots["after"][tick % len(shots["after"])], (512, 289))
            else:
                draw.rounded_rectangle((512, 289, 968, 545), radius=12, fill="#111e31")
                draw.text((560, 396), "No timeline change yet.", font=font(24), fill="#9eb3c9")
            draw.text((32, 574), "Executed workflow visualization | synthetic footage | not a UI recording", font=font(17), fill="#9eb3c9")
            frames.append(canvas.quantize(colors=96))
    gif = assets / "workflow-demo.gif"
    frames[0].save(gif, save_all=True, append_images=frames[1:], duration=400, loop=0, optimize=True)
    cover = Image.new("RGB", (1280, 640), "#0b1220")
    draw = ImageDraw.Draw(cover)
    draw.rounded_rectangle((48, 48, 1232, 592), radius=28, fill="#111e31", outline="#26415b", width=2)
    draw.text((96, 90), "FACUT", font=font(80), fill="#eef6ff")
    draw.text((100, 200), "FAST AI CUT", font=font(25, mono=True), fill="#68dbc8")
    draw.text((100, 292), "Video editing built for AI agents.", font=font(42), fill="#eef6ff")
    draw.text((100, 350), "With human approval.", font=font(42), fill="#eef6ff")
    draw.text((100, 465), "PROMPT > PATCH > REVIEW > APPROVE > RENDER", font=font(27, mono=True), fill="#68dbc8")
    draw.text((100, 540), "Deterministic / Non-destructive / FFmpeg-powered", font=font(22), fill="#9eb3c9")
    cover.save(assets / "social-preview.png")
    save_json(assets / "demo-verification.json", {
        "demo_type": "executed-workflow-visualization",
        "ui_screen_recording": False, "external_ai_used": False,
        "human_approval_claimed": False, "approval": "predefined synthetic test only",
        "facut_version": __import__("facut").__version__,
        "source_note": "Source checkout with Review CLI serialization fix; not the unmodified v1.0.0 portable release",
        "source": "FFmpeg-generated testsrc2 and sine; no user media",
        "checks": {
            "source_hash_unchanged": True, "preview_project_hash_unchanged": True,
            "draft_apply_rejected": True, "approved_dry_run_non_mutating": True,
            "full_decode_before_after": True, "monochrome_effect_measured": True,
        },
        "durations_seconds": durations,
        "gif_seconds": len(frames) * 0.4,
        "sha256": {"workflow-demo.gif": digest(gif)},
        "benchmark_note": "Not a creative quality or performance benchmark.",
    })
    print(f"PASS: {gif.name}; {len(frames) * 0.4:.1f}s; {gif.stat().st_size} bytes", flush=True)


if __name__ == "__main__":
    main()
