from __future__ import annotations

import json

import pytest

from facut.core.models import Marker
from facut.core.project_manager import ProjectManager, ProjectNotFoundError, find_project


def test_create_layout_and_atomic_round_trip(tmp_path) -> None:
    manager = ProjectManager.create(
        tmp_path / "demo", width=1280, height=720, fps=24, name="Movie"
    )
    assert manager.project_file.is_file()
    assert all((manager.project_dir / name).is_dir() for name in manager.SUBDIRECTORIES)
    document = manager.load()
    assert document.project.name == "Movie"
    assert document.project.width == 1280
    assert json.loads(manager.project_file.read_text(encoding="utf-8"))["version"] == "1.0"
    assert not list(manager.project_dir.glob("*.tmp"))


def test_mutation_revision_undo_and_redo(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "demo")

    def add_marker(document):
        marker = Marker(at=1.25, label="Beat")
        document.markers.append(marker)
        return marker

    marker, state = manager.mutate("marker.add", "Added marker", add_marker)
    assert state.revision == 1
    assert state.markers[0].id == marker.id
    assert manager.undo().revision == 0
    assert manager.document is not None and manager.document.markers == []
    assert manager.redo().revision == 1
    assert manager.document is not None and manager.document.markers[0].label == "Beat"


def test_dry_run_does_not_write(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "demo")
    original = manager.project_file.read_bytes()
    _, candidate = manager.mutate(
        "marker.add",
        "Preview marker",
        lambda document: document.markers.append(Marker(at=2)),
        dry_run=True,
    )
    assert candidate.revision == 1
    assert manager.project_file.read_bytes() == original
    assert manager.require_document().revision == 0


def test_find_project_walks_parents(tmp_path) -> None:
    manager = ProjectManager.create(tmp_path / "demo")
    nested = manager.project_dir / "media" / "nested"
    nested.mkdir()
    assert find_project(nested) == manager.project_file


def test_missing_project_has_domain_error(tmp_path) -> None:
    with pytest.raises(ProjectNotFoundError):
        ProjectManager(tmp_path / "none").load()
