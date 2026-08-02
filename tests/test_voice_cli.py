from __future__ import annotations

import json

from typer.testing import CliRunner

from facut.cli.main import app


def test_voice_profile_cli_and_record_plan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "--json",
            "voice",
            "profile",
            "create",
            "我的自然口播",
            "--speaker",
            "self",
            "--consent",
            "self",
            "--consent-statement",
            "I confirm this is my own voice and authorize local synthesis.",
        ],
    )
    assert created.exit_code == 0, created.output
    profile_id = json.loads(created.stdout)["data"]["id"]
    listed = runner.invoke(app, ["--json", "voice", "profile", "list"])
    assert json.loads(listed.stdout)["data"]["count"] == 1
    plan = runner.invoke(
        app,
        ["--json", "voice", "record-plan", profile_id, "--target-minutes", "2"],
    )
    payload = json.loads(plan.stdout)
    assert plan.exit_code == 0, plan.output
    assert payload["data"]["profile_id"] == profile_id
    assert len(payload["data"]["prompts"]) == 20
    assert len(payload["data"]["coverage"]["categories"]) == 20
    assert payload["data"]["coverage"]["delivery_modes"] == [
        "neutral",
        "conversational",
        "informative",
        "warm",
        "restrained",
    ]


def test_voice_provider_status_is_honest_when_unconfigured(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("FACUT_VOICE_PROVIDER", raising=False)
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    result = CliRunner().invoke(app, ["--json", "voice", "provider", "status"])
    payload = json.loads(result.stdout)
    assert payload["data"]["available"] is False
    assert payload["warnings"]


def test_voice_styles_are_stable_and_agent_discoverable() -> None:
    result = CliRunner().invoke(app, ["--json", "voice", "styles"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)["data"]
    assert [item["id"] for item in data["styles"]] == [
        "natural",
        "broadcast",
        "chat",
        "comedy",
        "excited",
    ]
    assert data["style_recording_script"] == "vlog-style-capsules-v1"


def test_style_capsule_recording_plan_is_short_and_varied(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    runner = CliRunner()
    created = runner.invoke(
        app,
        [
            "--json", "voice", "profile", "create", "Capsules", "--speaker", "self",
            "--consent", "self", "--consent-statement",
            "I confirm this is my own voice and authorize local synthesis.",
        ],
    )
    profile_id = json.loads(created.stdout)["data"]["id"]
    result = runner.invoke(
        app,
        [
            "--json", "voice", "record-plan", profile_id,
            "--script", "vlog-style-capsules-v1",
        ],
    )
    assert result.exit_code == 0, result.output
    plan = json.loads(result.stdout)["data"]
    assert plan["target_minutes"] == 2
    assert len(plan["prompts"]) == 5
    assert [item["delivery"] for item in plan["prompts"]] == [
        "natural", "broadcast", "chat", "comedy", "excited"
    ]


def test_voice_profile_actions_work_over_persistent_agent_session(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FACUT_VOICE_HOME", str(tmp_path / "voices"))
    runner = CliRunner()
    project = tmp_path / "project"
    assert runner.invoke(app, ["--json", "init", str(project)]).exit_code == 0
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "voice.profile.create",
                    "params": {
                        "name": "Agent voice",
                        "speaker": "self",
                        "consent": "self",
                        "consent_statement": "I confirm this is my own voice and authorize local synthesis.",
                    },
                }
            ),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "voice.profile.list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "shutdown"}),
        ]
    )
    result = runner.invoke(
        app, ["--project", str(project), "serve"], input=requests + "\n"
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert lines[1]["result"]["data"]["id"].startswith("voice_")
    assert lines[2]["result"]["data"]["count"] == 1
