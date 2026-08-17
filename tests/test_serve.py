from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app
from facut.voice import VoiceProfileStore


def test_stdio_json_rpc_reuses_loaded_project(tmp_path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    assert runner.invoke(app, ["--json", "init", str(project)]).exit_code == 0
    requests = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            json.dumps(
                {"jsonrpc": "2.0", "id": 2, "method": "project.snapshot"}
            ),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "agent.capabilities"}),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "agent.schema",
                    "params": {"action": "exchange.import"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 5, "method": "shutdown"}),
        ]
    )
    result = runner.invoke(
        app,
        ["--project", str(project), "serve"],
        input=requests + "\n",
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["method"] == "facut.ready"
    assert lines[0]["params"]["protocol"] == "facut-agent/1.0"
    assert lines[1]["result"]["status"] == "ok"
    assert lines[2]["result"]["project"]["name"] == "project"
    assert lines[3]["result"]["protocol"] == "facut-agent"
    assert any(action["name"] == "exchange.import" for action in lines[3]["result"]["actions"])
    assert any(action["name"] == "narration.suggest" for action in lines[3]["result"]["actions"])
    assert any(action["name"] == "voice.profile.create" for action in lines[3]["result"]["actions"])
    assert any(action["name"] == "narration.synthesize" for action in lines[3]["result"]["actions"])
    assert any(action["name"] == "clip.speed_curve" for action in lines[3]["result"]["actions"])
    assert any(action["name"] == "proxy.scan" for action in lines[3]["result"]["actions"])
    assert lines[4]["result"]["plan_first"] is True
    assert lines[5]["result"]["status"] == "shutdown"


def test_agent_rpc_mutation_uses_experiment_branch(tmp_path) -> None:
    runner = CliRunner()
    project = tmp_path / "project"
    assert runner.invoke(app, ["--json", "init", str(project)]).exit_code == 0
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "timeline.track.add",
                    "params": {"type": "video", "name": "V1"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "history.status"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "shutdown"}),
        ]
    )
    result = runner.invoke(
        app, ["--project", str(project), "serve", "--no-handshake"], input=requests + "\n"
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[0]["result"]["project_revision"] == 1
    assert lines[1]["result"]["data"]["branch"].startswith("agent/")


def test_voice_say_rpc_forwards_device_and_rejects_unknown_params(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    profile = VoiceProfileStore().create(
        "Zion",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I authorize local test synthesis.",
    )
    runner = CliRunner()
    project = tmp_path / "project"
    assert runner.invoke(app, ["--json", "init", str(project)]).exit_code == 0
    captured = {}

    def fake_say(profile, profile_directory, text, output_directory, **kwargs):
        captured.update(kwargs)
        generated = tmp_path / "generated.wav"
        generated.write_bytes(b"RIFF-test")
        return {
            "status": "success",
            "outputs": [{"output": str(generated)}],
            "warnings": [],
        }

    monkeypatch.setattr("facut.cli.serve_commands.synthesize_voice_say", fake_say)
    output = tmp_path / "rpc.wav"
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "voice.say",
                    "params": {
                        "text": "你好",
                        "voice": profile.id,
                        "output": str(output),
                        "device": "cpu",
                        "use_service": False,
                    },
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "voice.say",
                    "params": {
                        "text": "你好",
                        "output": str(tmp_path / "bad.wav"),
                        "unknown": True,
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "shutdown"}),
        ]
    )
    result = runner.invoke(
        app,
        ["--project", str(project), "serve", "--no-handshake"],
        input=requests + "\n",
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert output.is_file()
    assert captured["use_service"] is False
    assert captured["service_options"] == {
        "device": "cpu",
        "require_cuda": False,
    }
    assert lines[1]["error"]["data"]["code"] == "INVALID_ARGUMENT"
    assert "unknown parameter" in lines[1]["error"]["message"]
