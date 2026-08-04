"""Localhost-only browser recording studio for authorized voice profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import tempfile
import threading
from typing import Any
from urllib.parse import urlparse
import webbrowser

from .models import VoiceProfile
from .qc import validate_voice_samples
from .scripts import build_recording_plan
from .store import VoiceProfileStore


MAX_RECORDING_BYTES = 64 * 1024 * 1024

_WORKLET_JS = """class FacutCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.offset = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      let at = 0;
      while (at < channel.length) {
        const count = Math.min(channel.length - at, this.buffer.length - this.offset);
        this.buffer.set(channel.subarray(at, at + count), this.offset);
        this.offset += count;
        at += count;
        if (this.offset === this.buffer.length) {
          const packet = this.buffer;
          this.port.postMessage(packet, [packet.buffer]);
          this.buffer = new Float32Array(2048);
          this.offset = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('facut-capture', FacutCapture);
"""


@dataclass(slots=True)
class RecordingSession:
    """State shared by one short-lived local recording server."""

    store: VoiceProfileStore
    profile: VoiceProfile | None
    plan: dict[str, Any]
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    accepted_prompt_ids: set[str] = field(default_factory=set)
    finished: bool = False
    final_report: dict[str, Any] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._restore_prompt_progress()

    def _restore_prompt_progress(self) -> None:
        if self.profile is None:
            self.accepted_prompt_ids = set()
            return
        recorded = {sample.transcript for sample in self.profile.samples if sample.transcript}
        self.accepted_prompt_ids = {
            item["id"] for item in self.plan["prompts"] if item["text"] in recorded
        }

    @property
    def prompts(self) -> dict[str, dict[str, Any]]:
        return {item["id"]: item for item in self.plan["prompts"]}

    def public_payload(self) -> dict[str, Any]:
        return {
            "version": "1.0",
            "profile": self.profile.public_dict() if self.profile is not None else None,
            "plan": self.plan,
            "accepted_prompt_ids": sorted(self.accepted_prompt_ids),
            "profiles": self.profile_options(),
            "requires_profile_creation": self.profile is None,
        }

    def profile_options(self) -> list[dict[str, Any]]:
        styles = {"natural", "broadcast", "chat", "comedy", "excited"}
        return [
            {
                "id": item.id,
                "display_name": item.display_name,
                "speaker_id": item.speaker_id,
                "style": item.style,
                "status": item.status,
                "sample_count": len(item.samples),
                "duration_seconds": round(sum(sample.duration for sample in item.samples), 3),
                "aliases": list(item.aliases),
                "style_coverage": {
                    "recorded": sorted(
                        styles.intersection(
                            sample.delivery for sample in item.samples if sample.delivery
                        )
                    ),
                    "total": len(styles),
                },
            }
            for item in self.store.list()
        ]

    def switch_profile(self, profile_id: str) -> dict[str, Any]:
        with self.lock:
            profile = self.store.resolve(profile_id)
            self.profile = profile
            self.plan = build_recording_plan(
                profile,
                target_minutes=int(self.plan["target_minutes"]),
                script=str(self.plan["script"]),
            )
            self._restore_prompt_progress()
        return self.public_payload()

    def create_and_switch_profile(self, payload: dict[str, Any]) -> dict[str, Any]:
        required = ("name", "consent", "consent_statement")
        missing = [key for key in required if not str(payload.get(key, "")).strip()]
        if missing:
            raise ValueError(f"Missing voice profile fields: {', '.join(missing)}.")
        profile = self.store.create(
            str(payload["name"]).strip(),
            speaker_id=str(payload.get("speaker") or payload["name"]).strip(),
            language=str(payload.get("language") or "zh-CN").strip(),
            style=str(payload.get("style") or "natural-vlog").strip(),
            consent_relationship=str(payload["consent"]).strip(),
            consent_statement=str(payload["consent_statement"]).strip(),
        )
        result = self.switch_profile(profile.id)
        mode = str(payload.get("mode") or "").strip()
        if mode:
            with self.lock:
                self.plan = build_recording_plan(profile, script=mode)
                self._restore_prompt_progress()
            result = self.public_payload()
        return result

    def accept_recording(self, prompt_id: str, content: bytes) -> dict[str, Any]:
        if self.profile is None:
            raise ValueError("Create or select a voice profile before recording.")
        prompt = self.prompts.get(prompt_id)
        if prompt is None:
            raise ValueError(f'Unknown recording prompt "{prompt_id}".')
        if not content or len(content) > MAX_RECORDING_BYTES:
            raise ValueError("Recording must be a non-empty WAV file smaller than 64 MiB.")
        with tempfile.NamedTemporaryFile(
            prefix=f"{prompt_id}-", suffix=".wav", delete=False
        ) as stream:
            stream.write(content)
            temporary = Path(stream.name)
        try:
            report = validate_voice_samples([temporary], recommended_total_seconds=0)
            if report["status"] == "fail":
                messages = "; ".join(
                    issue["message"]
                    for item in report["files"]
                    for issue in item["issues"]
                    if issue["severity"] == "error"
                )
                raise ValueError(messages or "The recording failed voice quality checks.")
            with self.lock:
                self.profile = self.store.import_samples(
                    self.profile.id,
                    [temporary],
                    transcript=prompt["text"],
                    category=prompt.get("category"),
                    delivery=prompt.get("delivery"),
                )
                self.accepted_prompt_ids.add(prompt_id)
            return {
                "prompt_id": prompt_id,
                "accepted": True,
                "profile_sample_count": len(self.profile.samples),
                "quality": report["files"][0],
            }
        finally:
            temporary.unlink(missing_ok=True)

    def finish(self) -> dict[str, Any]:
        with self.lock:
            if self.profile is None:
                raise ValueError("Create or select a voice profile before finishing.")
            self.profile = self.store.get(self.profile.id)
            report = validate_voice_samples(
                self.store.sample_paths(self.profile),
                recommended_total_seconds=float(self.plan["target_minutes"] * 60),
            )
            self.profile = self.store.set_status(
                self.profile.id,
                {"pass": "ready", "warning": "warning", "fail": "invalid"}[report["status"]],
            )
            self.finished = True
            self.final_report = report
        return {
            "profile": self.profile.public_dict(),
            "accepted_prompt_ids": sorted(self.accepted_prompt_ids),
            "report": report,
        }


class RecordingStudioServer:
    """Serve a recording UI on loopback and import accepted PCM WAV takes."""

    def __init__(
        self,
        profile_id: str | None = None,
        *,
        store: VoiceProfileStore | None = None,
        target_minutes: int = 10,
        script: str = "mandarin-balanced-v1",
        mode: str | None = None,
        port: int = 0,
    ) -> None:
        voice_store = store or VoiceProfileStore()
        selected_script = mode or script
        if profile_id is not None:
            profile = voice_store.resolve(profile_id)
        else:
            profile = voice_store.get_default()
            if profile is None:
                profiles = voice_store.list()
                profile = profiles[0] if profiles else None
        self.session = RecordingSession(
            store=voice_store,
            profile=profile,
            plan=build_recording_plan(
                profile, target_minutes=target_minutes, script=selected_script
            ),
        )
        self._httpd = ThreadingHTTPServer(
            ("127.0.0.1", port), self._handler_type()
        )
        self._httpd.daemon_threads = True

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}/"

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        session = self.session

        class Handler(BaseHTTPRequestHandler):
            server_version = "FACUTVoiceStudio/1.0"

            def log_message(self, format: str, *args: object) -> None:
                del format, args

            def _send_json(
                self, status: int, payload: dict[str, Any]
            ) -> None:
                encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.end_headers()
                self.wfile.write(encoded)

            def _authorized(self) -> bool:
                return secrets.compare_digest(
                    self.headers.get("X-Facut-Token", ""), session.token
                )

            def _error(self, status: int, code: str, message: str) -> None:
                self._send_json(
                    status,
                    {"status": "error", "error": {"code": code, "message": message}},
                )

            def do_GET(self) -> None:  # noqa: N802
                path = urlparse(self.path).path
                if path == "/":
                    page = _recording_page(session).encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(page)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Frame-Options", "DENY")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header(
                        "Content-Security-Policy",
                        "default-src 'self'; script-src 'self' 'unsafe-inline'; worker-src 'self' blob:; "
                        "style-src 'unsafe-inline'; media-src 'self' blob:; connect-src 'self'; img-src 'self' data:",
                    )
                    self.end_headers()
                    self.wfile.write(page)
                    return
                if path == "/api/session":
                    if not self._authorized():
                        self._error(HTTPStatus.FORBIDDEN, "INVALID_TOKEN", "Invalid recording session token.")
                        return
                    self._send_json(HTTPStatus.OK, {"status": "success", "data": session.public_payload()})
                    return
                if path == "/health":
                    self._send_json(HTTPStatus.OK, {"status": "ok", "service": "facut-voice-studio"})
                    return
                if path == "/voice-worklet.js":
                    script = _WORKLET_JS.encode("utf-8")
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/javascript; charset=utf-8")
                    self.send_header("Content-Length", str(len(script)))
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    self.wfile.write(script)
                    return
                if path == "/favicon.ico":
                    self.send_response(HTTPStatus.NO_CONTENT)
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    return
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Route not found.")

            def do_POST(self) -> None:  # noqa: N802
                if not self._authorized():
                    self._error(HTTPStatus.FORBIDDEN, "INVALID_TOKEN", "Invalid recording session token.")
                    return
                path = urlparse(self.path).path
                if path.startswith("/api/recordings/"):
                    prompt_id = path.rsplit("/", 1)[-1]
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if length <= 0 or length > MAX_RECORDING_BYTES:
                        self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "INVALID_SIZE", "Recording must be between 1 byte and 64 MiB.")
                        return
                    if self.headers.get("Content-Type", "").split(";", 1)[0] not in {
                        "audio/wav", "audio/wave", "application/octet-stream"
                    }:
                        self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "INVALID_MEDIA_TYPE", "Upload a PCM WAV recording.")
                        return
                    try:
                        result = session.accept_recording(prompt_id, self.rfile.read(length))
                    except (ValueError, OSError) as error:
                        self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "VOICE_QC_FAILED", str(error))
                        return
                    self._send_json(HTTPStatus.CREATED, {"status": "success", "data": result})
                    return
                if path.startswith("/api/switch/"):
                    profile_id = path.rsplit("/", 1)[-1]
                    try:
                        result = session.switch_profile(profile_id)
                    except (ValueError, OSError, FileNotFoundError) as error:
                        self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "VOICE_SWITCH_FAILED", str(error))
                        return
                    self._send_json(HTTPStatus.OK, {"status": "success", "data": result})
                    return
                if path == "/api/profiles":
                    try:
                        length = int(self.headers.get("Content-Length", "0"))
                    except ValueError:
                        length = 0
                    if length <= 0 or length > 32 * 1024:
                        self._error(HTTPStatus.BAD_REQUEST, "INVALID_PROFILE_REQUEST", "Profile request must be non-empty and smaller than 32 KiB.")
                        return
                    try:
                        payload = json.loads(self.rfile.read(length).decode("utf-8"))
                        if not isinstance(payload, dict):
                            raise ValueError("Profile request must be a JSON object.")
                        result = session.create_and_switch_profile(payload)
                    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
                        self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "VOICE_PROFILE_CREATE_FAILED", str(error))
                        return
                    self._send_json(HTTPStatus.CREATED, {"status": "success", "data": result})
                    return
                if path == "/api/finish":
                    try:
                        result = session.finish()
                    except (ValueError, OSError) as error:
                        self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "VOICE_FINISH_FAILED", str(error))
                        return
                    self._send_json(HTTPStatus.OK, {"status": "success", "data": result})
                    threading.Thread(target=self.server.shutdown, daemon=True).start()
                    return
                self._error(HTTPStatus.NOT_FOUND, "NOT_FOUND", "Route not found.")

        return Handler

    def serve(self, *, open_browser: bool = True) -> dict[str, Any]:
        """Block until the user finishes the session or interrupts the server."""

        if open_browser:
            webbrowser.open(self.url, new=2)
        try:
            self._httpd.serve_forever(poll_interval=0.2)
        finally:
            self._httpd.server_close()
        return {
            "profile_id": self.session.profile.id if self.session.profile is not None else None,
            "finished": self.session.finished,
            "accepted_prompts": len(self.session.accepted_prompt_ids),
            "profile_sample_count": (
                len(self.session.profile.samples) if self.session.profile is not None else 0
            ),
            "report": self.session.final_report,
        }

    def start_background(self) -> threading.Thread:
        """Start the server for tests and embedding without opening a browser."""

        thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


def _recording_page(session: RecordingSession) -> str:
    profile_name = html.escape(
        session.profile.display_name if session.profile is not None else "尚未创建"
    )
    active_profile_id = session.profile.id if session.profile is not None else None
    token = json.dumps(session.token)
    prompts = json.dumps(session.plan["prompts"], ensure_ascii=False)
    accepted = json.dumps(sorted(session.accepted_prompt_ids))
    profiles = session.profile_options()
    profile_options = "".join(
        f'<option value="{html.escape(item["id"])}"'
        f'{" selected" if item["id"] == active_profile_id else ""}>'
        f'{html.escape(item["display_name"])} · {html.escape(item["style"])}'
        f' · {item["sample_count"]}条</option>'
        for item in profiles
    )
    if not profile_options:
        profile_options = '<option value="">尚无声音档案</option>'
    has_profile = "true" if session.profile is not None else "false"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FACUT 人声采集</title>
<style>
:root{{--bg:#090d14;--panel:#111827;--line:#263247;--text:#f4f7fb;--muted:#98a6bb;--accent:#58d6a9;--danger:#ff7070}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% 0,#162337,var(--bg) 42%);color:var(--text);font:16px/1.55 system-ui,"Microsoft YaHei",sans-serif}}
main{{max-width:920px;margin:auto;padding:32px 20px 64px}}header{{display:flex;justify-content:space-between;gap:16px;align-items:end;margin-bottom:26px}}
h1{{margin:0;font-size:clamp(26px,5vw,44px);letter-spacing:-1px}}.tag{{color:var(--accent);font-weight:700}}.muted{{color:var(--muted)}}
.card{{background:rgba(17,24,39,.94);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 18px 50px #0005;margin:14px 0}}
.prompt{{font-size:clamp(20px,3.5vw,30px);line-height:1.55;margin:22px 0;min-height:95px}}button,select,input,textarea{{font:inherit;border-radius:10px;padding:10px 16px;border:1px solid var(--line)}}select,input,textarea{{background:#0b1220;color:var(--text)}}button{{background:#1c293d;color:var(--text);cursor:pointer}}button.primary{{background:var(--accent);color:#052118;border:0;font-weight:800}}button.danger{{background:#4a2026;color:#ffd9dc}}button:disabled{{opacity:.45;cursor:not-allowed}}
.controls{{display:flex;flex-wrap:wrap;gap:10px}}.meter{{height:12px;background:#05080d;border-radius:999px;overflow:hidden;margin:15px 0}}#meter{{height:100%;width:0;background:linear-gradient(90deg,var(--accent),#ffd866,var(--danger));transition:width .06s}}
.row{{display:flex;justify-content:space-between;gap:14px;align-items:center}}#status{{min-height:26px}}audio{{width:100%;margin:12px 0}}.ok{{color:var(--accent)}}.bad{{color:var(--danger)}}
dialog{{max-width:620px;width:calc(100% - 32px);background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:18px;padding:24px}}dialog::backdrop{{background:#000a}}.form-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.form-grid label{{display:flex;flex-direction:column;gap:6px}}.form-grid .wide{{grid-column:1/-1}}textarea{{min-height:90px;resize:vertical}}
@media(max-width:620px){{header,.row{{align-items:stretch;flex-direction:column}}}}
</style></head><body><main>
<header><div><div class="tag">FACUT LOCAL VOICE STUDIO</div><h1>人声采集</h1><div class="muted">当前档案：{profile_name} · 数据仅发送到本机 FACUT</div></div><div id="progress">{len(session.accepted_prompt_ids)} / {len(session.plan['prompts'])}</div></header>
<section class="card"><div class="row"><label>声音档案 <select id="profiles">{profile_options}</select></label><div class="controls"><button id="switchProfile">切换档案</button><button id="newProfile" title="新建人物/风格">+ 新建声音</button></div></div><div class="muted">名称可以自由填写；内部稳定 ID 会自动生成。不同人物必须分别授权和录音。</div></section>
<section class="card"><div class="row"><label>麦克风 <select id="devices"><option value="">使用系统默认麦克风</option></select></label><button id="permission">启用麦克风</button></div><div class="meter"><div id="meter"></div></div><div id="status" class="muted">请先授权麦克风，然后逐条录制。</div></section>
<section class="card"><div class="row"><span id="promptMeta" class="tag"></span><span id="delivery" class="muted"></span></div><div class="prompt" id="prompt"></div><audio id="playback" controls hidden></audio><div class="controls"><button id="record" class="primary" disabled>开始录音</button><button id="stop" class="danger" disabled>停止</button><button id="retry" disabled>重录</button><button id="save" disabled>保存本条</button><button id="next" disabled>下一条</button></div></section>
<section class="card"><div class="row"><div><b>完成采集</b><div class="muted">可提前结束；FACUT 会按实际时长给出质量报告。</div></div><button id="finish">完成并导入声音档案</button></div></section>
<dialog id="profileDialog"><form id="profileForm"><h2>新建声音档案</h2><div class="form-grid"><label>自定义名称<input name="name" required placeholder="例如：Zion、妈妈、旅行旁白"></label><label>人物标识（可选）<input name="speaker" placeholder="留空时使用自定义名称"></label><label>授权关系<select name="consent"><option value="self">本人声音</option><option value="authorized">已获授权的人声</option></select></label><label>录入模式<select name="mode"><option value="quick">快速试用 · 3 条</option><option value="recommended" selected>推荐采集 · 8 条</option><option value="styles">风格增强 · 5 条</option></select></label><label>声音用途<select name="style"><option value="natural-vlog">自然 VLOG</option><option value="travel-documentary">旅行纪录片</option><option value="cheerful">轻快</option><option value="calm">沉稳</option></select></label><label>语言<input name="language" value="zh-CN"></label><label class="wide">授权声明<textarea name="consent_statement" required placeholder="请记录声音本人对本机采集与合成用途的明确授权。"></textarea></label></div><div class="controls" style="margin-top:16px"><button type="button" id="cancelProfile">取消</button><button class="primary" type="submit">创建并开始录制</button></div></form></dialog>
</main><script>
const TOKEN={token}; const HAS_PROFILE={has_profile}; const prompts={prompts}; let saved=new Set({accepted}),index=prompts.findIndex(p=>!saved.has(p.id));if(index<0)index=0;let stream,context,source,worklet,monitorGain,analyser,meterFrame,chunks=[],recording=false,wavBlob=null;
const $=id=>document.getElementById(id); function status(text,bad=false){{$('status').textContent=text;$('status').className=bad?'bad':'muted'}}
function render(){{if(!HAS_PROFILE||!prompts.length){{$('prompt').textContent='请先点击“+ 新建声音”，填写名称和授权信息。';$('promptMeta').textContent='等待创建声音档案';$('delivery').textContent='';$('progress').textContent='0 / 0';$('permission').disabled=true;$('finish').disabled=true;return}}const p=prompts[index],names={{neutral:'自然',conversational:'对话感',informative:'清楚说明',warm:'温暖',restrained:'克制',natural:'自然',broadcast:'播音',chat:'聊天',comedy:'轻松幽默',excited:'真实兴奋','natural-vlog':'轻松 VLOG','travel-documentary':'旅行纪录片'}},categories={{opening:'开场',observation:'现场观察','date-time':'日期与时间','price-number':'价格与数字',navigation:'方向指引',question:'疑问句',reflection:'感受与思考',explanation:'信息解释','ambient-sound':'环境声描述','family-reaction':'人物反应',english:'英文','mixed-alphabet':'中英混合',closing:'收尾'}};$('prompt').textContent=p.text;$('promptMeta').textContent=`第 ${{index+1}} 条 · ${{categories[p.category]||p.category}}`;$('delivery').textContent=`表达方式：${{names[p.delivery]||p.delivery}}`;$('progress').textContent=`${{saved.size}} / ${{prompts.length}}`;$('next').disabled=!saved.has(p.id)||index>=prompts.length-1}}
async function devices(){{const all=await navigator.mediaDevices.enumerateDevices();const inputs=all.filter(x=>x.kind==='audioinput');$('devices').innerHTML='';inputs.forEach((d,i)=>{{const o=document.createElement('option');o.value=d.deviceId;o.textContent=d.label||`麦克风 ${{i+1}}`;$('devices').appendChild(o)}})}}
async function enable(){{if(!navigator.mediaDevices?.getUserMedia)throw new Error('当前浏览器不支持麦克风采集');if(stream)stream.getTracks().forEach(t=>t.stop());if(context)await context.close().catch(()=>{{}});if(meterFrame)cancelAnimationFrame(meterFrame);const selected=$('devices').value,settings={{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}};if(selected)settings.deviceId={{exact:selected}};try{{stream=await navigator.mediaDevices.getUserMedia({{audio:settings}})}}catch(e){{if(selected&&e.name==='OverconstrainedError'){{stream=await navigator.mediaDevices.getUserMedia({{audio:{{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:false}}}})}}else throw e}}await devices();context=new AudioContext({{latencyHint:'interactive'}});await context.resume();if(!context.audioWorklet)throw new Error('当前浏览器不支持稳定音频线程，请使用最新版 Edge 或 Chrome');await context.audioWorklet.addModule('/voice-worklet.js');source=context.createMediaStreamSource(stream);worklet=new AudioWorkletNode(context,'facut-capture',{{numberOfInputs:1,numberOfOutputs:1,outputChannelCount:[1]}});worklet.port.onmessage=e=>{{if(recording)chunks.push(new Float32Array(e.data))}};monitorGain=context.createGain();monitorGain.gain.value=0;analyser=context.createAnalyser();source.connect(analyser);source.connect(worklet);worklet.connect(monitorGain);monitorGain.connect(context.destination);$('record').disabled=false;status(`麦克风已启用 · ${{context.sampleRate}} Hz · 稳定音频线程`);meterLoop()}}
function meterLoop(){{if(!analyser)return;const d=new Uint8Array(analyser.fftSize);analyser.getByteTimeDomainData(d);let peak=0;for(const v of d)peak=Math.max(peak,Math.abs(v-128)/128);$('meter').style.width=`${{Math.min(100,peak*180)}}%`;meterFrame=requestAnimationFrame(meterLoop)}}
function resample(input,from,to=48000){{if(from===to)return input;const n=Math.round(input.length*to/from),out=new Float32Array(n),ratio=from/to;for(let i=0;i<n;i++){{const p=i*ratio,a=Math.floor(p),b=Math.min(a+1,input.length-1),f=p-a;out[i]=input[a]*(1-f)+input[b]*f}}return out}}
function encodeWav(parts,rate){{let n=parts.reduce((a,x)=>a+x.length,0),all=new Float32Array(n),p=0;for(const x of parts){{all.set(x,p);p+=x.length}}all=resample(all,rate);const b=new ArrayBuffer(44+all.length*2),v=new DataView(b),s=(o,t)=>{{for(let i=0;i<t.length;i++)v.setUint8(o+i,t.charCodeAt(i))}};s(0,'RIFF');v.setUint32(4,36+all.length*2,true);s(8,'WAVE');s(12,'fmt ');v.setUint32(16,16,true);v.setUint16(20,1,true);v.setUint16(22,1,true);v.setUint32(24,48000,true);v.setUint32(28,96000,true);v.setUint16(32,2,true);v.setUint16(34,16,true);s(36,'data');v.setUint32(40,all.length*2,true);let o=44;for(const x of all){{v.setInt16(o,Math.max(-1,Math.min(1,x))*32767,true);o+=2}}return new Blob([b],{{type:'audio/wav'}})}}
$('permission').onclick=()=>enable().catch(e=>status(`无法启用麦克风：${{e.message||e.name||'请检查浏览器权限'}}`,true));$('devices').onchange=()=>enable().catch(e=>status(e.message||e.name||'无法切换麦克风',true));
$('switchProfile').onclick=async()=>{{const id=$('profiles').value;if(!id)return;status('正在切换声音档案…');try{{const r=await fetch(`/api/switch/${{id}}`,{{method:'POST',headers:{{'X-Facut-Token':TOKEN}}}}),j=await r.json();if(!r.ok)throw new Error(j.error?.message||'切换失败');location.reload()}}catch(e){{status(e.message,true)}}}};
$('newProfile').onclick=()=>$('profileDialog').showModal();$('cancelProfile').onclick=()=>$('profileDialog').close();
$('profileForm').onsubmit=async e=>{{e.preventDefault();const form=new FormData(e.currentTarget),payload=Object.fromEntries(form.entries());try{{const r=await fetch('/api/profiles',{{method:'POST',headers:{{'X-Facut-Token':TOKEN,'Content-Type':'application/json'}},body:JSON.stringify(payload)}}),j=await r.json();if(!r.ok)throw new Error(j.error?.message||'创建失败');location.reload()}}catch(error){{status(error.message,true);$('profileDialog').close()}}}};
$('record').onclick=()=>{{chunks=[];wavBlob=null;recording=true;$('record').disabled=true;$('stop').disabled=false;$('save').disabled=true;$('retry').disabled=true;status('正在录音…请自然朗读')}};
$('stop').onclick=()=>{{recording=false;wavBlob=encodeWav(chunks,context.sampleRate);$('playback').src=URL.createObjectURL(wavBlob);$('playback').hidden=false;$('stop').disabled=true;$('save').disabled=false;$('retry').disabled=false;status('请试听。满意后保存本条，或选择重录。')}};
$('retry').onclick=()=>{{$('playback').hidden=true;$('record').disabled=false;$('save').disabled=true;wavBlob=null;status('已清除当前录音，可以重新开始。')}};
$('save').onclick=async()=>{{const p=prompts[index];$('save').disabled=true;status('FACUT 正在检查录音质量…');try{{const r=await fetch(`/api/recordings/${{p.id}}`,{{method:'POST',headers:{{'X-Facut-Token':TOKEN,'Content-Type':'audio/wav'}},body:wavBlob}}),j=await r.json();if(!r.ok)throw new Error(j.error?.message||'保存失败');saved.add(p.id);$('next').disabled=index>=prompts.length-1;$('record').disabled=false;status(`已保存 · 峰值 ${{j.data.quality.metrics.peak_dbfs}} dBFS`);render()}}catch(e){{$('save').disabled=false;status(e.message,true)}}}};
$('next').onclick=()=>{{if(index<prompts.length-1)index++;wavBlob=null;$('playback').hidden=true;$('save').disabled=true;$('retry').disabled=true;$('record').disabled=!stream;render()}};
$('finish').onclick=async()=>{{if(!confirm(`已保存 ${{saved.size}} 条录音，确定完成吗？`))return;$('finish').disabled=true;status('正在生成最终质量报告…');try{{const r=await fetch('/api/finish',{{method:'POST',headers:{{'X-Facut-Token':TOKEN}}}}),j=await r.json();if(!r.ok)throw new Error(j.error?.message||'完成失败');status(`已导入声音档案。质量状态：${{j.data.report.status}}`);document.querySelectorAll('button').forEach(b=>b.disabled=true);if(stream)stream.getTracks().forEach(t=>t.stop())}}catch(e){{$('finish').disabled=false;status(e.message,true)}}}};render();if(!HAS_PROFILE)$('profileDialog').showModal();
</script></body></html>"""


__all__ = ["RecordingSession", "RecordingStudioServer"]
