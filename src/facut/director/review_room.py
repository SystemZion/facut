"""Token-authenticated local Review Room and deterministic Timeline Patch service."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import parse_qs, quote, urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from facut.core.command_engine import CommandEngine
from facut.core.cutgraph import semantic_diff
from facut.core.models import ProjectDocument
from facut.core.project_manager import ProjectManager
from facut.exceptions import InvalidArgumentError, ResourceNotFoundError, ReviewRequiredError


PATCH_ACTIONS = {
    "timeline.add", "clip.move", "clip.trim", "clip.delete", "clip.replace",
    "clip.transform", "clip.motion", "clip.freeze", "clip.speed", "clip.speed_curve",
    "audio.volume", "audio.fade", "audio.process", "audio.crossfade", "audio.loudness",
    "effect.add", "effect.remove", "transition.add", "transition.remove",
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PatchOperation(_Strict):
    id: str = Field(default_factory=lambda: f"patchop_{uuid4().hex[:16].upper()}")
    action: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    replacement: dict[str, Any] | None = None
    reason: str = Field(min_length=1)
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    approved: bool = False


class TimelinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"] = "1.0"
    id: str = Field(default_factory=lambda: f"timeline-patch-{uuid4().hex[:12]}")
    request: str
    project_id: str
    project_revision: int
    project_sha256: str
    evidence_sha256: str
    status: Literal["draft", "approved", "applied"] = "draft"
    operations: list[PatchOperation] = Field(default_factory=list)


def _root(project_dir: str | Path) -> Path:
    return Path(project_dir) / "cache" / "director-studio"


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _document_hash(document: ProjectDocument) -> str:
    return hashlib.sha256(_canonical(document.model_dump(mode="json"))).hexdigest()


def _evidence_hash(project_dir: str | Path) -> str:
    path = Path(project_dir) / "cache" / "vlog" / "observations.json"
    return hashlib.sha256(path.read_bytes() if path.is_file() else b"").hexdigest()


def _atomic(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n"); temporary = Path(stream.name)
    os.replace(temporary, path)
    return path


def _load_json(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    payload = source if isinstance(source, dict) else json.loads(Path(source).read_text(encoding="utf-8-sig"))
    return {key: value for key, value in payload.items() if key not in {"path", "output"}}


def _project_snapshot(manager: ProjectManager) -> dict[str, Any]:
    document = manager.require_document()
    videos = []
    for folder in (manager.project_dir / "previews", manager.project_dir / "renders"):
        if folder.is_dir():
            videos.extend(str(path.relative_to(manager.project_dir).as_posix()) for path in sorted(folder.rglob("*.mp4")) if path.stat().st_size > 0)
    evidence_path = manager.project_dir / "cache" / "vlog" / "observations.json"
    story_path = manager.project_dir / "cache" / "vlog" / "story.plan.json"
    waveform_files = sorted((manager.project_dir / "cache").rglob("*waveform*.json"))
    music_plan_files = sorted((manager.project_dir / "cache").rglob("*music*.plan.json"))
    return {
        "project": document.project.model_dump(mode="json"),
        "revision": document.revision,
        "branch": manager.cutgraph.current_branch(),
        "tracks": [item.model_dump(mode="json") for item in document.tracks],
        "subtitles": [item.model_dump(mode="json") for item in document.subtitle_cues],
        "videos": videos,
        "evidence": json.loads(evidence_path.read_text(encoding="utf-8")) if evidence_path.is_file() else {"observations": []},
        "story": json.loads(story_path.read_text(encoding="utf-8")) if story_path.is_file() else None,
        "waveforms": [item for path in waveform_files[:32] if (item := _safe_json(path)) is not None],
        "music_plans": [item for path in music_plan_files[:32] if (item := _safe_json(path)) is not None],
    }


def _safe_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None


def create_feedback(manager: ProjectManager, payload: dict[str, Any]) -> dict[str, Any]:
    allowed = {"keep", "delete", "shorten", "extend", "replace", "preserve-original", "music", "subtitle", "effect", "comment"}
    action = str(payload.get("action", "comment"))
    if action not in allowed:
        raise InvalidArgumentError(f'Unknown review feedback action "{action}".')
    start = float(payload.get("start", 0))
    end = float(payload.get("end", start))
    if start < 0 or end < start:
        raise InvalidArgumentError("Review feedback range is invalid.")
    feedback = {
        "version": "review_feedback.v1", "id": f"feedback_{uuid4().hex[:16].upper()}",
        "project_id": manager.require_document().project.id,
        "project_revision": manager.require_document().revision,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start": start, "end": end, "action": action,
        "comment": str(payload.get("comment", "")).strip(),
        "target": payload.get("target"), "remember_requested": False,
    }
    path = _atomic(_root(manager.project_dir) / "feedback" / f"{feedback['id']}.json", feedback)
    return {**feedback, "path": str(path.resolve())}


def capture_request(manager: ProjectManager, request: str) -> dict[str, Any]:
    if not request.strip():
        raise InvalidArgumentError("Natural-language edit request cannot be empty.")
    item = create_feedback(manager, {"action": "comment", "comment": request, "start": 0, "end": manager.require_document().project.duration})
    return {
        "status": "review_required", "feedback_id": item["id"], "request": request,
        "reason": "FACUT deliberately has no embedded language model.",
        "next_command": f"facut --project \"{manager.project_dir}\" review patch plan <timeline.patch.json>",
        "required_schema": "timeline_patch.v1", "project_revision": manager.require_document().revision,
    }


def create_patch(manager: ProjectManager, payload: dict[str, Any]) -> dict[str, Any]:
    document = manager.require_document()
    incoming = dict(payload)
    incoming.setdefault("project_id", document.project.id)
    incoming.setdefault("project_revision", document.revision)
    incoming.setdefault("project_sha256", _document_hash(document))
    incoming.setdefault("evidence_sha256", _evidence_hash(manager.project_dir))
    patch = TimelinePatch.model_validate(incoming)
    if not patch.operations:
        raise ReviewRequiredError("Timeline patch has no deterministic operations.")
    unknown = sorted({item.action for item in patch.operations if item.action not in PATCH_ACTIONS})
    if unknown:
        raise InvalidArgumentError("Timeline patch contains unsupported operations.", details={"unsupported_actions": unknown})
    for operation in patch.operations:
        if not operation.evidence:
            raise ReviewRequiredError(
                f'Patch operation "{operation.id}" has no evidence reference.',
                suggestion="Reference an observation_id or clip_id from Review Room.",
            )
    path = _atomic(_root(manager.project_dir) / "patches" / f"{patch.id}.json", patch.model_dump(mode="json"))
    return {**patch.model_dump(mode="json"), "path": str(path.resolve())}


def _compile_operation(document: ProjectDocument, operation: PatchOperation) -> list[dict[str, Any]]:
    if operation.action != "clip.replace":
        parameters = dict(operation.parameters)
        if operation.action == "clip.delete":
            parameters.pop("allow_protected_cut", None)
        return [{"action": operation.action, **parameters}]
    clip_id = str(operation.parameters.get("clip_id") or operation.parameters.get("target") or "")
    replacement = operation.replacement or {}
    found = None
    for track in document.tracks:
        clip = next((item for item in track.clips if item.id == clip_id), None)
        if clip:
            found = (track, clip); break
    if found is None:
        raise ResourceNotFoundError(f'Clip "{clip_id}" was not found for replacement.')
    track, clip = found
    if not {"media_id", "in", "out"}.issubset(replacement):
        raise InvalidArgumentError("clip.replace requires replacement media_id, in, and out.")
    return [
        {"action": "clip.delete", "clip_id": clip.id, "ripple": False},
        {"action": "timeline.add", "media_id": replacement["media_id"], "track": track.id,
         "at": clip.timeline_start, "in": replacement["in"], "out": replacement["out"]},
    ]


def preview_patch(manager: ProjectManager, source: str | Path | dict[str, Any], *, approved_only: bool = False) -> dict[str, Any]:
    patch = TimelinePatch.model_validate(_load_json(source))
    document = manager.require_document()
    if (patch.project_id, patch.project_revision, patch.project_sha256, patch.evidence_sha256) != (
        document.project.id, document.revision, _document_hash(document), _evidence_hash(manager.project_dir)
    ):
        raise ReviewRequiredError("Timeline patch is stale for the current project or evidence.")
    candidate = document.model_copy(deep=True)
    selected = [item for item in patch.operations if item.approved or not approved_only]
    if not selected:
        raise ReviewRequiredError("No Timeline Patch operations are approved.")
    compiled = []
    for operation in selected:
        _validate_operation_gate(document, operation)
        for command in _compile_operation(candidate, operation):
            CommandEngine.apply(candidate, command); compiled.append(command)
    candidate.recompute_duration()
    _validate_event_chains(document, candidate, manager.project_dir)
    diff = semantic_diff(document, candidate)
    return {
        "patch_id": patch.id, "status": "review_required", "operations": len(selected),
        "compiled_commands": compiled, "diff": diff,
        "invalidated_nodes": sorted({item["entity"] for item in diff["changed_ranges"]}),
        "preview_ranges": diff["changed_ranges"],
        "render_policy": "1080p changed ranges only; approval is required before 4K delivery",
    }


def _validate_operation_gate(document: ProjectDocument, operation: PatchOperation) -> None:
    if operation.action not in {"clip.delete", "clip.replace"}:
        return
    clip_id = str(operation.parameters.get("clip_id") or operation.parameters.get("target") or "")
    clip = document.find_clip(clip_id)
    if clip is None:
        raise ResourceNotFoundError(f'Clip "{clip_id}" was not found.')
    asset = document.find_media(clip.media_id)
    protected = list(asset.metadata.get("protected_spans", [])) if asset else []
    overlaps = [
        span for span in protected
        if float(span.get("end", 0)) > clip.source_in and float(span.get("start", 0)) < clip.source_out
    ]
    if overlaps and not bool(operation.parameters.get("allow_protected_cut", False)):
        raise ReviewRequiredError(
            f'Operation "{operation.id}" would remove protected speech or an atomic event.',
            suggestion="Keep the protected clip, or explicitly set allow_protected_cut=true after reviewing the full span.",
            details={"clip_id": clip_id, "protected_spans": overlaps},
        )


def _validate_event_chains(before: ProjectDocument, after: ProjectDocument, project_dir: Path) -> None:
    evidence_path = project_dir / "cache" / "vlog" / "observations.json"
    if not evidence_path.is_file():
        return
    payload = _safe_json(evidence_path) or {}
    observations = [item for item in payload.get("observations", []) if item.get("event_chain") and item.get("subject_id") and item.get("event_order") is not None]
    if not observations:
        return
    def represented(document: ProjectDocument, observation: dict[str, Any]) -> bool:
        range_data = observation.get("range") or {}
        start, end = float(range_data.get("start", 0)), float(range_data.get("end", 0))
        for track in document.tracks:
            for clip in track.clips:
                if clip.media_id == observation.get("media_id") and clip.source_out > start and clip.source_in < end:
                    return True
        return False
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in observations:
        grouped.setdefault((str(item["subject_id"]), str(item["event_chain"])), []).append(item)
    for (subject, chain), items in grouped.items():
        ordered = sorted(items, key=lambda item: int(item["event_order"]))
        before_present = [item for item in ordered if represented(before, item)]
        after_present = [item for item in ordered if represented(after, item)]
        if len(before_present) < 2 or not after_present:
            continue
        before_orders = [int(item["event_order"]) for item in before_present]
        after_orders = [int(item["event_order"]) for item in after_present]
        if max(before_orders) not in after_orders and min(before_orders) in after_orders:
            raise ReviewRequiredError(
                "Timeline Patch breaks a same-subject event outcome.",
                suggestion="Keep the recovery/outcome beat or remove the incident setup as a reviewed unit.",
                details={"subject_id": subject, "event_chain": chain, "before_orders": before_orders, "after_orders": after_orders},
            )


def apply_patch(manager: ProjectManager, source: str | Path | dict[str, Any], *, approved_only: bool = True) -> dict[str, Any]:
    patch = TimelinePatch.model_validate(_load_json(source))
    preview = preview_patch(manager, patch.model_dump(mode="json"), approved_only=approved_only)
    compiled = preview["compiled_commands"]
    manager.ensure_experiment_branch("review")
    def operation(candidate: ProjectDocument) -> list[Any]:
        return [CommandEngine.apply(candidate, command) for command in compiled]
    results, state = manager.mutate(
        "review.patch.apply", f"Applied approved Timeline Patch {patch.id}", operation,
        command={"patch_id": patch.id, "commands": compiled, "_actor": "agent", "_intent": patch.request},
    )
    payload = patch.model_dump(mode="json"); payload["status"] = "applied"
    _atomic(_root(manager.project_dir) / "patches" / f"{patch.id}.json", payload)
    return {**preview, "status": "applied", "results": results, "project_revision": state.revision}


def prepare_patch_apply(
    manager: ProjectManager, source: str | Path | dict[str, Any], *, approved_only: bool = True
) -> dict[str, Any]:
    """Resolve a patch before entering a CommandEngine atomic batch."""

    patch = TimelinePatch.model_validate(_load_json(source))
    preview = preview_patch(manager, patch.model_dump(mode="json"), approved_only=approved_only)
    return {
        "action": "review.patch.apply.prepared", "patch_id": patch.id,
        "request": patch.request, "compiled_commands": preview["compiled_commands"],
        "preview_ranges": preview["preview_ranges"],
    }


def apply_prepared_patch(document: ProjectDocument, command: dict[str, Any]) -> list[Any]:
    """Apply a prevalidated patch to one in-memory transaction candidate."""

    results = [CommandEngine.apply(document, item) for item in command["compiled_commands"]]
    document.settings.setdefault("director_studio_patches", []).append({
        "patch_id": command["patch_id"], "request": command["request"],
        "preview_ranges": command.get("preview_ranges", []),
    })
    return results


def export_review(manager: ProjectManager, output: str | Path, *, overwrite: bool = False) -> Path:
    destination = Path(output).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise InvalidArgumentError(f'Output "{destination}" already exists.')
    feedback = []
    folder = _root(manager.project_dir) / "feedback"
    if folder.is_dir():
        feedback = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.json"))]
    return _atomic(destination, {"version": "review_session.v1", "snapshot": _project_snapshot(manager), "feedback": feedback})


_HTML = r'''<!doctype html><meta charset="utf-8"><title>FACUT Director Studio</title>
<style>body{font:15px system-ui;margin:0;background:#101216;color:#eef}header{padding:16px 24px;background:#181b22;position:sticky;top:0}main{display:grid;grid-template-columns:2fr 1fr;gap:16px;padding:16px}.card{background:#1c2028;border:1px solid #343a46;border-radius:12px;padding:14px}video{width:100%;max-height:52vh;background:#000}button,select,textarea{font:inherit;margin:4px;padding:8px;border-radius:7px}textarea{width:95%;height:90px}.muted{color:#9da7b5}pre{white-space:pre-wrap;max-height:320px;overflow:auto}@media(max-width:900px){main{grid-template-columns:1fr}}</style>
<header><b>FACUT 1.0 Director Studio</b> <span id="status" class="muted"></span></header><main>
<section><div class="card"><div style="display:grid;grid-template-columns:1fr 1fr;gap:8px"><video id="player" controls></video><video id="playerB" controls></video></div><select id="video"></select><select id="videoB"></select><button id="sync">Sync A/B</button></div><div class="card"><h3>Timeline / StoryGraph / Waveforms</h3><pre id="timeline"></pre></div></section>
<aside><div class="card"><h3>Timecode feedback</h3><div>At <span id="time">0.000</span>s</div><select id="action"><option>comment</option><option>keep</option><option>delete</option><option>shorten</option><option>extend</option><option>replace</option><option>preserve-original</option><option>music</option><option>subtitle</option><option>effect</option></select><textarea id="comment" placeholder="Describe the intended change"></textarea><button id="submit">Save feedback</button><div id="message"></div></div><div class="card"><h3>Evidence</h3><pre id="evidence"></pre></div></aside></main>
<script>const token=new URLSearchParams(location.search).get('token');const api=(p,o={})=>fetch(p+(p.includes('?')?'&':'?')+'token='+encodeURIComponent(token),o).then(async r=>{if(!r.ok)throw Error((await r.json()).error);return r.json()});
let snap;const player=document.querySelector('#player'),playerB=document.querySelector('#playerB'),select=document.querySelector('#video'),selectB=document.querySelector('#videoB');const setVideo=(p,s)=>p.src='/media?token='+encodeURIComponent(token)+'&path='+encodeURIComponent(s.value);api('/api/session').then(x=>{snap=x.snapshot;status.textContent=snap.project.name+' · revision '+snap.revision;timeline.textContent=JSON.stringify({tracks:snap.tracks,story:snap.story,waveforms:snap.waveforms,music:snap.music_plans},null,2);evidence.textContent=JSON.stringify(snap.evidence,null,2);snap.videos.forEach(v=>{select.add(new Option(v,v));selectB.add(new Option(v,v))});if(snap.videos.length){select.value=snap.videos[0];selectB.value=snap.videos[Math.min(1,snap.videos.length-1)];setVideo(player,select);setVideo(playerB,selectB)}});select.onchange=()=>setVideo(player,select);selectB.onchange=()=>setVideo(playerB,selectB);sync.onclick=()=>{playerB.currentTime=player.currentTime;playerB.playbackRate=player.playbackRate};player.ontimeupdate=()=>time.textContent=player.currentTime.toFixed(3);submit.onclick=()=>api('/api/feedback',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({start:player.currentTime,end:player.currentTime,action:action.value,comment:comment.value})}).then(x=>message.textContent='Saved '+x.id).catch(e=>message.textContent=e.message);</script>'''


def _handler(project_dir: Path, token: str):
    manager = ProjectManager(project_dir)
    manager.load()
    class Handler(BaseHTTPRequestHandler):
        def _auth(self) -> bool:
            query = parse_qs(urlparse(self.path).query)
            supplied = query.get("token", [""])[0] or self.headers.get("X-FACUT-Token", "")
            return secrets.compare_digest(supplied, token)
        def _json(self, payload: Any, status: int = 200) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(data)
        def do_GET(self) -> None:
            if not self._auth(): self._json({"error":"unauthorized"}, HTTPStatus.UNAUTHORIZED); return
            parsed = urlparse(self.path)
            if parsed.path == "/":
                data = _HTML.encode("utf-8"); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data); return
            if parsed.path in {"/api/session", "/api/project"}: self._json({"version":"review_session.v1","snapshot":_project_snapshot(manager)}); return
            if parsed.path == "/media":
                requested = parse_qs(parsed.query).get("path", [""])[0]
                source = (project_dir / requested).resolve()
                if project_dir.resolve() not in source.parents or not source.is_file(): self._json({"error":"media not found"},404); return
                size = source.stat().st_size
                start, end = 0, size - 1
                range_header = self.headers.get("Range")
                status = HTTPStatus.OK
                if range_header and range_header.startswith("bytes="):
                    left, _, right = range_header[6:].partition("-")
                    try:
                        start = int(left) if left else 0
                        end = min(int(right), size - 1) if right else size - 1
                    except ValueError:
                        self._json({"error": "invalid range"}, 416); return
                    if start < 0 or start > end or start >= size:
                        self._json({"error": "range not satisfiable"}, 416); return
                    status = HTTPStatus.PARTIAL_CONTENT
                length = end - start + 1
                self.send_response(status); self.send_header("Content-Type","video/mp4"); self.send_header("Content-Length",str(length)); self.send_header("Accept-Ranges","bytes")
                if status == HTTPStatus.PARTIAL_CONTENT: self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                with source.open("rb") as stream:
                    stream.seek(start); remaining = length
                    while remaining:
                        chunk = stream.read(min(1024 * 1024, remaining))
                        if not chunk: break
                        self.wfile.write(chunk); remaining -= len(chunk)
                return
            self._json({"error":"not found"},404)
        def do_POST(self) -> None:
            if not self._auth(): self._json({"error":"unauthorized"},401); return
            if urlparse(self.path).path != "/api/feedback": self._json({"error":"not found"},404); return
            try:
                length=int(self.headers.get("Content-Length","0")); payload=json.loads(self.rfile.read(length)); self._json(create_feedback(manager,payload),201)
            except Exception as error: self._json({"error":str(error)},400)
        def log_message(self, format: str, *args: Any) -> None: return
    return Handler


def run_server(project: str, token: str, state_path: str) -> None:
    project_dir = Path(project).resolve(); state = Path(state_path).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(project_dir, token))
    payload = {"version":"review_session.v1","project":str(project_dir),"host":"127.0.0.1","port":server.server_port,"pid":os.getpid(),"token_sha256":hashlib.sha256(token.encode()).hexdigest(),"started_at":datetime.now(timezone.utc).isoformat(),"running":True}
    _atomic(state,payload)
    try: server.serve_forever(poll_interval=0.2)
    finally:
        payload["running"] = False; _atomic(state,payload); server.server_close()


def open_room(manager: ProjectManager, *, open_browser: bool = True) -> dict[str, Any]:
    root = _root(manager.project_dir); state = root / "session.json"; token = secrets.token_urlsafe(32)
    if getattr(sys, "frozen", False):
        args = [sys.executable, "__review_room_daemon__", str(manager.project_dir), token, str(state)]
    else:
        args = [sys.executable, "-m", "facut.director.review_room", str(manager.project_dir), token, str(state)]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags, start_new_session=os.name != "nt")
    deadline = time.monotonic() + 10
    payload = None
    while time.monotonic() < deadline:
        if state.is_file():
            try:
                candidate=json.loads(state.read_text(encoding="utf-8"))
                if candidate.get("running") and candidate.get("token_sha256") == hashlib.sha256(token.encode()).hexdigest(): payload=candidate; break
            except (OSError, json.JSONDecodeError): pass
        time.sleep(0.05)
    if payload is None: raise RuntimeError("Review Room did not start within 10 seconds.")
    url=f"http://127.0.0.1:{payload['port']}/?token={quote(token)}"
    if open_browser: webbrowser.open(url)
    return {**payload, "url":url, "authentication":"random-token", "bind":"loopback-only"}


def room_status(manager: ProjectManager) -> dict[str, Any]:
    state = _root(manager.project_dir) / "session.json"
    if not state.is_file(): return {"running":False,"project":str(manager.project_dir)}
    payload=json.loads(state.read_text(encoding="utf-8")); pid=int(payload.get("pid",0)); running=bool(payload.get("running"))
    if running:
        running = _process_exists(pid)
    payload["running"]=running; payload.pop("token",None); return payload


def close_room(manager: ProjectManager) -> dict[str, Any]:
    """Stop only this project's recorded Review Room process."""

    state = _root(manager.project_dir) / "session.json"
    status = room_status(manager)
    if not status.get("running"):
        return {**status, "stopped": False}
    pid = int(status["pid"])
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=False, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.kill(pid, signal.SIGTERM)
    status["running"] = False; status["stopped"] = True
    _atomic(state, status)
    return status


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def main() -> None:
    parser=argparse.ArgumentParser(); parser.add_argument("project"); parser.add_argument("token"); parser.add_argument("state"); args=parser.parse_args()
    run_server(args.project,args.token,args.state)


if __name__ == "__main__": main()
