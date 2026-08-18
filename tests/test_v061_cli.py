from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from facut.cli.main import app
from facut.cli.intelligence_commands import synthesize_narration_previews
from facut.core.project_manager import ProjectManager
from facut.intelligence.narration_plan import (
    NarrationCandidate,
    NarrationLine,
    NarrationPlan,
    NarrationPreview,
    NarrationTimeRange,
    load_narration_plan,
    save_narration_plan,
)
from facut.voice import VoiceProfileStore


def _profile(store: VoiceProfileStore):
    return store.create(
        "Zion",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    )


def test_custom_voice_alias_default_and_selector_cli(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    project = tmp_path / "project"
    ProjectManager.create(project)
    profile = _profile(VoiceProfileStore())
    runner = CliRunner()
    alias = runner.invoke(app, ["--json", "voice", "alias", "set", profile.id, "zion"])
    assert alias.exit_code == 0, alias.output
    renamed = runner.invoke(app, ["--json", "voice", "profile", "rename", "zion", "Zion VLOG"])
    assert renamed.exit_code == 0, renamed.output
    default = runner.invoke(
        app,
        ["--json", "--project", str(project), "voice", "default", "set", "zion", "--scope", "project"],
    )
    assert default.exit_code == 0, default.output
    shown = runner.invoke(app, ["--json", "voice", "profile", "show", "zion"])
    assert json.loads(shown.stdout)["data"]["display_name"] == "Zion VLOG"


def test_voice_say_materializes_named_audition_outputs(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    profile = _profile(VoiceProfileStore())

    def fake_synthesize(profile, profile_directory, text, output_directory, **kwargs):
        output_root = Path(output_directory)
        output_root.mkdir(parents=True, exist_ok=True)
        outputs = []
        for index in range(kwargs["takes"]):
            generated = output_root / f"generated-{index}.wav"
            generated.write_bytes(b"RIFF-test")
            outputs.append({"output": str(generated), "reference_sample_id": "sample_1"})
        return {
            "status": "success",
            "outputs": outputs,
            "warnings": [],
            "selection": {"selected_style": "natural", "confidence": 0.58},
            "audition_required": True,
            "timeline_modified": False,
        }

    monkeypatch.setattr("facut.cli.voice_commands.synthesize_voice_say", fake_synthesize)
    output = tmp_path / "narration.wav"
    result = CliRunner().invoke(
        app,
        ["--json", "voice", "say", "今天出去走走。", "--voice", profile.id, "--takes", "2", "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["audition_required"] is True
    assert (tmp_path / "narration-take-1.wav").is_file()
    assert (tmp_path / "narration-take-2.wav").is_file()


def test_narration_review_requires_preview_and_updates_plan(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    preview = tmp_path / "preview.wav"
    preview.write_bytes(b"RIFF-test")
    line = NarrationLine(
        id="line_1",
        clip_id="clip_1",
        media_id="media_1",
        timeline_range=NarrationTimeRange(start=0, end=2),
        visual_summary="一家人在海边看日落",
        fact_confidence=0.9,
        evidence={"provider": "test"},
        candidates=[NarrationCandidate(id="candidate_1", text="这一刻大家都安静了。")],
        previews=[NarrationPreview(id="preview_1", path=str(preview))],
    )
    plan_path = save_narration_plan(
        NarrationPlan(
            project_id=manager.require_document().project.id,
            project_revision=0,
            lines=[line],
        ),
        tmp_path / "narration.plan.json",
    )
    result = CliRunner().invoke(
        app,
        ["--json", "narration", "review", str(plan_path), "--line", "line_1", "--status", "approved"],
    )
    assert result.exit_code == 0, result.output
    updated = load_narration_plan(plan_path)
    assert updated.status == "ready"
    assert updated.lines[0].status.value == "approved"
    assert updated.lines[0].selected_preview_id == "preview_1"


def test_narration_no_service_preserves_device_and_cuda_options(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    profile = _profile(VoiceProfileStore())
    manager = ProjectManager.create(tmp_path / "project")
    line = NarrationLine(
        id="line_1",
        clip_id="clip_1",
        media_id="media_1",
        timeline_range=NarrationTimeRange(start=0, end=2),
        visual_summary="一家人在海边散步",
        fact_confidence=0.9,
        evidence={"provider": "test"},
        candidates=[NarrationCandidate(id="candidate_1", text="今天慢慢走一走。")],
    )
    plan_path = save_narration_plan(
        NarrationPlan(
            project_id=manager.require_document().project.id,
            project_revision=0,
            lines=[line],
        ),
        tmp_path / "narration.plan.json",
    )
    captured = {}

    def fake_provider(profile, profile_directory, lines, output_directory, **kwargs):
        captured.update(kwargs)
        output_root = Path(output_directory)
        output_root.mkdir(parents=True, exist_ok=True)
        outputs = []
        for index, _line in enumerate(lines):
            generated = output_root / f"generated-{index}.wav"
            generated.write_bytes(b"RIFF-test")
            outputs.append({"output": str(generated), "reference_sample_id": "sample_1"})
        return {"status": "success", "outputs": outputs, "warnings": []}

    monkeypatch.setattr(
        "facut.cli.intelligence_commands.synthesize_with_provider", fake_provider
    )
    synthesize_narration_previews(
        plan_path,
        voice=profile.id,
        preview_dir=tmp_path / "previews",
        device="cuda",
        require_cuda=True,
        use_service=False,
    )

    assert captured == {"device": "cuda", "require_cuda": True}


def test_new_agent_schemas_are_discoverable() -> None:
    runner = CliRunner()
    for action in (
        "voice.say",
        "voice.serve.status",
        "narration.generate",
        "narration.synthesize",
        "narration.apply",
        "recipe.validate",
        "recipe.plan",
        "recipe.build",
        "clip.motion",
        "clip.speed",
        "clip.speed_curve",
        "audio.process",
        "audio.crossfade",
        "proxy.scan",
        "proxy.link_auto",
        "history.status",
        "history.diff",
        "history.restore",
        "branch.create",
        "branch.switch",
        "branch.accept",
        "preview.compare",
        "qc.run",
    ):
        result = runner.invoke(app, ["--json", "schema", "action", action])
        assert result.exit_code == 0, (action, result.output)

    voice_schema = json.loads(
        runner.invoke(app, ["--json", "schema", "action", "voice.say"]).stdout
    )["data"]["parameters"]["properties"]
    assert voice_schema["device"]["enum"] == ["auto", "cuda", "cpu"]
    assert "use_service" in voice_schema


def test_run_exposes_agent_rpc_actions_in_explicit_non_atomic_batch(tmp_path) -> None:
    project = tmp_path / "project"
    ProjectManager.create(project)
    source = tmp_path / "agent-batch.json"
    source.write_text(
        json.dumps({
            "version": "1.0",
            "atomic": False,
            "commands": [{"action": "voice.serve.status"}],
        }),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["--json", "--project", str(project), "run", str(source)]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["data"]["atomic"] is False
    assert payload["data"]["results"][0]["command"] == "voice.serve.status"


def test_run_rejects_false_atomicity_for_external_agent_actions(tmp_path) -> None:
    project = tmp_path / "project"
    ProjectManager.create(project)
    source = tmp_path / "unsafe-batch.json"
    source.write_text(
        json.dumps({"atomic": True, "commands": [{"action": "voice.serve.status"}]}),
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        app, ["--json", "--project", str(project), "run", str(source)]
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["errors"][0]["code"] == "INVALID_COMMAND"
