"""Pinned model manifests kept outside the application binary and repository."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from facut.exceptions import InvalidArgumentError


@dataclass(frozen=True, slots=True)
class ModelFile:
    path: str
    size: int
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DownloadSource:
    name: str
    repository: str
    revision: str
    template: str

    def url(self, path: str) -> str:
        encoded_path = quote(path, safe="/")
        return self.template.format(
            repository=self.repository,
            revision=self.revision,
            path=encoded_path,
        )


@dataclass(frozen=True, slots=True)
class ModelPackage:
    name: str
    directory_name: str
    display_name: str
    purpose: str
    files: tuple[ModelFile, ...]
    sources: tuple[DownloadSource, ...]
    benchmark_file: str
    license: str

    @property
    def total_size(self) -> int:
        return sum(item.size for item in self.files)


_VOICE_FILES = (
    ModelFile("README.md", 11364, "9ce4334fdd276864e60a072fe65cd8791c77239d0204ac7f6c8d04466e455c56"),
    ModelFile("campplus.onnx", 28303423, "a6ac6a63997761ae2997373e2ee1c47040854b4b759ea41ec48e4e42df0f4d73"),
    ModelFile("configuration.json", 47, "c502b6328c67638b401df8dd05de89e9e8d1cff9cd0ada10dfbdbe13556c20de"),
    ModelFile("cosyvoice3.yaml", 6934, "f5a6b2c6f05139d0f18861a1fe506f751e787026b77c05f7e8fef9f8a4405965"),
    ModelFile("flow.pt", 1329116148, "a6fab32a7825e5b0bc855ddd948f8db9370b0a786fbc249caa4595e95b608e4b"),
    ModelFile("hift.pt", 83202622, "b279d7641eb97ae55b3b540cfba4f953c26492a2df758328a89a4d007ab87a65"),
    ModelFile("llm.pt", 2024669519, "69f43bd545131c30e98947fb360ea8b4dc9916d8e83dded7757c7ea4f5a24970"),
    ModelFile("speech_tokenizer_v3.onnx", 969451503, "23236a74175dbdda47afc66dbadd5bcb41303c467a57c261cb8539ad9db9208d"),
    ModelFile("CosyVoice-BlankEN/config.json", 659, "168aa1bd401abc3bc262ba15ba4e499627a8b4e006e9d050b47c22de20660185"),
    ModelFile("CosyVoice-BlankEN/generation_config.json", 242, "e558847a8b4402616f1273797b015104dc266fe4b520056fca88823ba8f8ebe6"),
    ModelFile("CosyVoice-BlankEN/merges.txt", 1402109, "ac8ff86a72bee70828fbc1119bc4398c6f3a9a6e490d7b0dbe917be025478bd0"),
    ModelFile("CosyVoice-BlankEN/model.safetensors", 988097824, "130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96"),
    ModelFile("CosyVoice-BlankEN/tokenizer_config.json", 1287, "482bd979881423375ca5414e4e0d94cd7c5349dbb17fffd46b4d36d71e62a1bc"),
    ModelFile("CosyVoice-BlankEN/vocab.json", 2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
)

_SRT_FILES = (
    ModelFile("config.json", 2263),
    ModelFile("model.bin", 1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
    ModelFile("preprocessor_config.json", 340),
    ModelFile("tokenizer.json", 2710337),
    ModelFile("vocabulary.json", 1068114),
)


def _modelscope(repository: str, revision: str = "master") -> DownloadSource:
    return DownloadSource(
        name="modelscope",
        repository=repository,
        revision=revision,
        template="https://www.modelscope.cn/models/{repository}/resolve/{revision}/{path}",
    )


def _huggingface(repository: str, revision: str) -> DownloadSource:
    return DownloadSource(
        name="huggingface",
        repository=repository,
        revision=revision,
        template="https://huggingface.co/{repository}/resolve/{revision}/{path}?download=true",
    )


def _hf_mirror(repository: str, revision: str) -> DownloadSource:
    return DownloadSource(
        name="hf-mirror",
        repository=repository,
        revision=revision,
        template="https://hf-mirror.com/{repository}/resolve/{revision}/{path}",
    )


MODEL_CATALOG: dict[str, ModelPackage] = {
    "voice_model": ModelPackage(
        name="voice_model",
        directory_name="Fun-CosyVoice3-0.5B-2512",
        display_name="Fun-CosyVoice3 0.5B 2512",
        purpose="Authorized local zero-shot voice cloning and narration",
        files=_VOICE_FILES,
        sources=(
            _modelscope("FunAudioLLM/Fun-CosyVoice3-0.5B-2512"),
            _huggingface("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "main"),
            _hf_mirror("FunAudioLLM/Fun-CosyVoice3-0.5B-2512", "main"),
        ),
        benchmark_file="flow.pt",
        license="Apache-2.0",
    ),
    "srt_model": ModelPackage(
        name="srt_model",
        directory_name="faster-whisper-large-v3-turbo",
        display_name="faster-whisper large-v3-turbo",
        purpose="Multilingual speech recognition and editable subtitle generation",
        files=_SRT_FILES,
        sources=(
            _huggingface(
                "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
            ),
            _hf_mirror(
                "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
                "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
            ),
        ),
        benchmark_file="model.bin",
        license="MIT",
    ),
}


def get_model_package(name: str) -> ModelPackage:
    normalized = name.strip().lower().replace("-", "_")
    try:
        return MODEL_CATALOG[normalized]
    except KeyError as error:
        raise InvalidArgumentError(
            f'Unknown downloadable model "{name}".',
            suggestion=f"Choose one of: {', '.join(MODEL_CATALOG)}.",
        ) from error
