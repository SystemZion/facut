from __future__ import annotations

from facut.vlog.context import audit_trip_bible, rename_trip_entity, save_trip_bible


def test_trip_bible_rename_keeps_old_name_as_alias_and_changes_policy(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    save_trip_bible(
        project,
        {
            "trip_name": "Test",
            "people": [{"id": "person_1", "display_name": "旧名字"}],
        },
    )
    before = audit_trip_bible(project)["policy_sha256"]
    result = rename_trip_entity(project, "person_1", "Roger", kind="person")
    after = audit_trip_bible(project)
    assert result["old_name"] == "旧名字"
    assert result["aliases"] == ["旧名字"]
    assert after["policy_sha256"] != before
    assert after["valid"] is True


def test_trip_bible_audit_blocks_ambiguous_names(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    save_trip_bible(
        project,
        {
            "trip_name": "Test",
            "people": [{"id": "p1", "display_name": "小王"}],
            "places": [{"id": "place1", "display_name": "景点", "aliases": ["小王"]}],
        },
    )
    audit = audit_trip_bible(project)
    assert audit["valid"] is False
    assert audit["blocking_count"] == 1
