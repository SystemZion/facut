from __future__ import annotations

from pathlib import Path

import pytest

from facut.core.cutgraph import CutGraphConflictError
from facut.core.project_manager import ProjectManager


def _edit(manager: ProjectManager, value: str) -> None:
    manager.mutate(
        "test.edit",
        f"Set note to {value}",
        lambda document: document.settings.update({"note": value}),
        command={"value": value},
    )


def test_branch_diff_and_fast_forward_accept(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    _edit(manager, "main")
    manager.cutgraph.create_branch("experiment")
    manager.switch_branch("experiment")
    _edit(manager, "alternate")
    diff = manager.diff_history("main", "experiment")
    # Settings are intentionally excluded from entity noise, while branch tips remain distinct.
    assert diff["before"] != diff["after"]
    result, _ = manager.accept_branch("experiment", "main")
    assert result["fast_forward"] is True
    restored = manager.switch_branch("main")
    assert restored.settings["note"] == "alternate"


def test_undo_then_edit_preserves_abandoned_future_on_recovery_branch(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    _edit(manager, "one")
    _edit(manager, "two")
    future = manager.cutgraph.head()
    manager.undo()
    _edit(manager, "three")
    branches = manager.cutgraph.list_branches()
    recovery = [item for item in branches if item["name"].startswith("recovery/")]
    assert len(recovery) == 1
    assert recovery[0]["commit_id"] == future
    assert manager.require_document().settings["note"] == "three"


def test_restore_creates_new_commit_without_rewriting_history(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    _edit(manager, "one")
    first = manager.cutgraph.head()
    _edit(manager, "two")
    latest = manager.cutgraph.head()
    restored = manager.restore_commit(first)
    assert restored.settings["note"] == "one"
    assert manager.cutgraph.head() not in {first, latest}
    assert manager.cutgraph.read_commit(latest)["document"]["settings"]["note"] == "two"


def test_accept_rejects_diverged_target(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    _edit(manager, "base")
    manager.cutgraph.create_branch("experiment")
    manager.switch_branch("experiment")
    _edit(manager, "experiment")
    manager.switch_branch("main")
    _edit(manager, "main-moved")
    with pytest.raises(CutGraphConflictError):
        manager.accept_branch("experiment", "main")


def test_cutgraph_commit_records_actor_and_intent(tmp_path: Path) -> None:
    manager = ProjectManager.create(tmp_path / "project")
    manager.mutate(
        "agent.edit",
        "AI shortened the opening",
        lambda document: document.settings.update({"opening": "short"}),
        command={"_actor": "agent", "_intent": "Faster first thirty seconds"},
    )
    commit = manager.cutgraph.read_commit("HEAD")
    assert commit["actor"] == "agent"
    assert commit["intent"] == "Faster first thirty seconds"
    assert manager.require_document().history[-1].commit_id == commit["commit_id"]
