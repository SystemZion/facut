from __future__ import annotations

import io
import json
import math
import struct
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import wave

from facut.voice import RecordingStudioServer, VoiceProfileStore


def _wav_bytes(seconds: float = 1.0) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        frames = bytearray()
        for index in range(round(seconds * 48000)):
            value = round(math.sin(2 * math.pi * 220 * index / 48000) * 6000)
            frames.extend(struct.pack("<h", value))
        stream.writeframes(frames)
    return output.getvalue()


def _profile(store: VoiceProfileStore) -> str:
    return store.create(
        "我的自然口播",
        speaker_id="self",
        consent_relationship="self",
        consent_statement="I confirm this is my own voice and authorize local synthesis.",
    ).id


def _json(request: Request) -> tuple[int, dict]:
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def test_local_recording_server_imports_qc_checked_take(tmp_path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    profile_id = _profile(store)
    studio = RecordingStudioServer(
        profile_id, store=store, target_minutes=1, port=0
    )
    thread = studio.start_background()
    try:
        with urlopen(studio.url, timeout=5) as response:
            page = response.read().decode("utf-8")
            assert response.headers["X-Frame-Options"] == "DENY"
            csp = response.headers["Content-Security-Policy"]
            assert "script-src 'self' 'unsafe-inline'" in csp
            assert "worker-src 'self' blob:" in csp
            assert "FACUT LOCAL VOICE STUDIO" in page
            assert '<option value="">使用系统默认麦克风</option>' in page
            assert "OverconstrainedError" in page
            assert "audioWorklet.addModule('/voice-worklet.js')" in page
        with urlopen(studio.url + "voice-worklet.js", timeout=5) as response:
            worklet = response.read().decode("utf-8")
            assert response.headers["Content-Type"].startswith("application/javascript")
            assert "registerProcessor('facut-capture'" in worklet
        token = studio.session.token
        status, session = _json(
            Request(studio.url + "api/session", headers={"X-Facut-Token": token})
        )
        assert status == 200
        prompt = session["data"]["plan"]["prompts"][0]
        status, saved = _json(
            Request(
                studio.url + f"api/recordings/{prompt['id']}",
                data=_wav_bytes(),
                method="POST",
                headers={"X-Facut-Token": token, "Content-Type": "audio/wav"},
            )
        )
        assert status == 201
        assert saved["data"]["quality"]["status"] == "pass"
        imported = store.get(profile_id)
        assert len(imported.samples) == 1
        assert imported.samples[0].transcript == prompt["text"]
        assert imported.samples[0].category == prompt["category"]
        assert imported.samples[0].delivery == prompt["delivery"]
        with urlopen(studio.url, timeout=5) as response:
            updated_page = response.read().decode("utf-8")
            assert '<div id="progress">1 / 10</div>' in updated_page
            assert "新建人物/风格" in updated_page
        created_status, created = _json(
            Request(
                studio.url + "api/profiles",
                data=json.dumps(
                    {
                        "name": "家人的纪录片声音",
                        "speaker": "family-a",
                        "consent": "authorized",
                        "consent_statement": "The speaker explicitly authorizes local FACUT voice recording.",
                        "style": "travel-documentary",
                    }
                ).encode("utf-8"),
                method="POST",
                headers={"X-Facut-Token": token, "Content-Type": "application/json"},
            )
        )
        assert created_status == 201
        second_id = created["data"]["profile"]["id"]
        assert second_id != profile_id
        assert len(store.list()) == 2
        switched_status, switched = _json(
            Request(
                studio.url + f"api/switch/{profile_id}",
                data=b"",
                method="POST",
                headers={"X-Facut-Token": token},
            )
        )
        assert switched_status == 200
        assert switched["data"]["accepted_prompt_ids"] == [prompt["id"]]
        status, finished = _json(
            Request(
                studio.url + "api/finish",
                data=b"",
                method="POST",
                headers={"X-Facut-Token": token},
            )
        )
        assert status == 200
        assert finished["data"]["report"]["status"] == "warning"
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        studio._httpd.server_close()


def test_recording_server_rejects_unauthorized_and_invalid_audio(tmp_path) -> None:
    store = VoiceProfileStore(tmp_path / "voices")
    studio = RecordingStudioServer(_profile(store), store=store, port=0)
    studio.start_background()
    try:
        try:
            urlopen(Request(studio.url + "api/session"), timeout=5)
        except HTTPError as error:
            assert error.code == 403
        else:
            raise AssertionError("Session endpoint accepted a request without its token")
        try:
            urlopen(
                Request(
                    studio.url + "api/recordings/prompt_001",
                    data=b"not a wav",
                    method="POST",
                    headers={
                        "X-Facut-Token": studio.session.token,
                        "Content-Type": "audio/wav",
                    },
                ),
                timeout=5,
            )
        except HTTPError as error:
            assert error.code == 422
            payload = json.loads(error.read().decode("utf-8"))
            assert payload["error"]["code"] == "VOICE_QC_FAILED"
        else:
            raise AssertionError("Invalid WAV was accepted")
    finally:
        studio.close()
