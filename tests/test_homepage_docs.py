"""Keep the public landing page short, navigable, and artifact-backed."""

import json
from pathlib import Path
import re
from urllib.parse import unquote

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def test_landing_pages_are_short_and_keep_language_navigation():
    for filename in ("README.md", "README.zh-CN.md"):
        text = (ROOT / filename).read_text(encoding="utf-8")
        assert len(text.splitlines()) < 160
        assert len(text.encode("utf-8")) < 12000
        assert "workflow-demo.gif" in text
        assert "Agent" in text
    assert "README.zh-CN.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "README.md" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")


def test_documentation_relative_links_resolve():
    documents = [
        ROOT / "README.md", ROOT / "README.zh-CN.md",
        *list((ROOT / "docs").glob("*.md")),
        ROOT / "examples" / "homepage-demo" / "README.md",
    ]
    for path in documents:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if target.startswith(("https:", "http:", "#", "mailto:")):
                continue
            relative = unquote(target.split("#", 1)[0])
            assert (path.parent / relative).exists(), f"{path.name}: {target}"


def test_demo_is_labeled_and_has_measured_artifact_identity():
    assets = ROOT / "docs" / "assets"
    manifest = json.loads((assets / "demo-verification.json").read_text(encoding="utf-8"))
    assert manifest["ui_screen_recording"] is False
    assert manifest["human_approval_claimed"] is False
    assert all(manifest["checks"].values())
    assert max(abs(d - 6) for d in manifest["durations_seconds"]) <= 1 / 30
    import hashlib
    assert hashlib.sha256((assets / "workflow-demo.gif").read_bytes()).hexdigest() == manifest["sha256"]["workflow-demo.gif"]
    with Image.open(assets / "workflow-demo.gif") as im:
        assert im.n_frames >= 24
        duration = 0
        for i in range(im.n_frames):
            im.seek(i)
            duration += im.info["duration"]
        assert 15000 <= duration <= 25000
    with Image.open(assets / "social-preview.png") as im:
        assert im.size == (1280, 640)
