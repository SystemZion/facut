"""Patch application must not report failure after committing domain objects."""

import json

from typer.testing import CliRunner

from facut.cli.main import app
from facut.core.models import Clip, MediaAsset, MediaTechnicalInfo, Track, TrackType
from facut.core.project_manager import ProjectManager
from facut.director.review_room import create_patch


def test_effect_patch_apply_serializes_domain_result_in_json_and_human_modes(tmp_path):
    runner = CliRunner()
    for json_mode in (False, True):
        manager = ProjectManager.create(tmp_path / str(json_mode))

        def seed(document):
            document.media.append(MediaAsset(
                id="media_test", kind="video", path="synthetic.mp4",
                original_name="synthetic.mp4", size=1, sha256="a" * 64,
                technical=MediaTechnicalInfo(duration=6, video_codec="h264"),
            ))
            document.tracks.append(Track(
                id="V1", type=TrackType.VIDEO, name="V1",
                clips=[Clip(id="clip_test", media_id="media_test", track_id="V1", source_out=6)],
            ))

        manager.mutate("test.seed", "generated fixture", seed)
        patch = create_patch(manager, {
            "request": "Apply the reviewed monochrome effect.",
            "operations": [{
                "action": "effect.add", "approved": True,
                "parameters": {"clip_id": "clip_test", "effect_type": "black-white"},
                "reason": "Reviewed synthetic test", "evidence": ["clip_test"],
                "confidence": 1.0,
            }],
        })
        args = ["--json"] if json_mode else []
        result = runner.invoke(app, [
            *args, "-p", str(manager.project_dir), "review", "patch",
            "apply", patch["path"], "--approved-only",
        ])
        assert result.exit_code == 0, result.output
        if json_mode:
            envelope = json.loads(result.stdout)
            assert envelope["status"] == "success"
            assert envelope["data"]["results"][0]["type"] == "black-white"
        else:
            assert "black-white" in result.output
        assert manager.load().find_clip("clip_test").effects[0].type == "black-white"
