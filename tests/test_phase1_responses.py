from __future__ import annotations

import json

from facut.exceptions import ResourceNotFoundError
from facut.responses import error_response, success_response


def test_success_response_contract() -> None:
    payload = json.loads(success_response("test", {"value": 1}, project_revision=3).as_json())
    assert payload == {
        "status": "success",
        "command": "test",
        "data": {"value": 1},
        "warnings": [],
        "errors": [],
        "project_revision": 3,
    }


def test_error_response_contract() -> None:
    error = ResourceNotFoundError(
        "Missing.",
        suggestion="Import it first.",
        details={"media_id": "media_01"},
    )
    payload = json.loads(error_response("inspect", error, project_revision=2).as_json())
    assert payload["status"] == "error"
    assert payload["errors"][0]["code"] == "FILE_NOT_FOUND"
    assert payload["errors"][0]["suggestion"] == "Import it first."
