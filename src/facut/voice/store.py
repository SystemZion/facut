"""Atomic, local-only storage for multiple authorized voice profiles."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import warnings
import wave

from platformdirs import user_data_path

from .models import ConsentRecord, VoiceProfile, VoiceSample


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _platform_voice_homes() -> tuple[Path, Path]:
    """Return the canonical and pre-0.5.3 platform voice directories.

    ``platformdirs`` treats the application name as the application author on
    Windows when ``appauthor`` is omitted.  Older FACUT builds therefore used
    ``.../facut/facut/voices``.  Passing ``appauthor=False`` produces the
    intended ``.../facut/voices`` path.
    """

    canonical = (user_data_path("facut", appauthor=False) / "voices").resolve()
    legacy = (user_data_path("facut") / "voices").resolve()
    return canonical, legacy


def default_voice_home() -> Path:
    override = os.environ.get("FACUT_VOICE_HOME")
    if override:
        return Path(override).expanduser().resolve()
    canonical, _legacy = _platform_voice_homes()
    return canonical


def _atomic_copy_if_missing(source: Path, destination: Path) -> None:
    """Copy one legacy file without replacing an existing canonical file."""

    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        try:
            # A hard link atomically claims the destination without replacing a
            # file another process may have created after the existence check.
            os.link(temporary, destination)
        except FileExistsError:
            return
        except OSError:
            # Some filesystems disallow hard links.  Exclusive creation keeps
            # the same no-overwrite guarantee on those systems.
            try:
                with temporary.open("rb") as input_stream, destination.open("xb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                shutil.copystat(source, destination)
            except FileExistsError:
                return
            except Exception:
                destination.unlink(missing_ok=True)
                raise
    finally:
        temporary.unlink(missing_ok=True)


def _merge_legacy_voice_home(legacy: Path, canonical: Path) -> None:
    """Non-destructively copy legacy voice data into the canonical directory."""

    if legacy == canonical or not legacy.is_dir():
        return
    canonical.mkdir(parents=True, exist_ok=True)
    for source in sorted(legacy.rglob("*")):
        if source.is_symlink():
            continue
        destination = canonical / source.relative_to(legacy)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            _atomic_copy_if_missing(source, destination)


def _prepare_default_voice_home() -> Path:
    """Prepare the canonical store, falling back to readable legacy data."""

    canonical, legacy = _platform_voice_homes()
    try:
        canonical.mkdir(parents=True, exist_ok=True)
        _merge_legacy_voice_home(legacy, canonical)
    except OSError as error:
        if legacy.is_dir():
            warnings.warn(
                f"Could not migrate FACUT voice profiles to {canonical}; "
                f"continuing with the legacy store at {legacy}: {error}",
                RuntimeWarning,
                stacklevel=2,
            )
            return legacy
        raise
    return canonical


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _wave_info(path: Path) -> tuple[float, int, int, int]:
    try:
        with wave.open(str(path), "rb") as stream:
            rate = stream.getframerate()
            frames = stream.getnframes()
            return frames / rate, rate, stream.getnchannels(), stream.getsampwidth()
    except (wave.Error, EOFError) as error:
        raise ValueError(f'Voice sample "{path.name}" is not a readable PCM WAV file.') from error


class VoiceProfileStore:
    """Manage profile JSON and copied samples without exposing absolute paths."""

    def __init__(self, root: str | Path | None = None) -> None:
        if root is not None:
            self.root = Path(root).expanduser().resolve()
        elif os.environ.get("FACUT_VOICE_HOME"):
            self.root = default_voice_home()
        else:
            self.root = _prepare_default_voice_home()
        self.profiles_dir = self.root / "profiles"
        self.trash_dir = self.root / ".trash"
        self.profiles_dir.mkdir(parents=True, exist_ok=True)

    def _profile_dir(self, profile_id: str) -> Path:
        if not re.fullmatch(r"voice_[A-Z0-9_]{4,64}", profile_id):
            raise ValueError(f'Invalid voice profile ID "{profile_id}".')
        return self.profiles_dir / profile_id

    def _profile_file(self, profile_id: str) -> Path:
        return self._profile_dir(profile_id) / "profile.json"

    def _save(self, profile: VoiceProfile) -> None:
        destination = self._profile_file(profile.id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        profile.updated_at = datetime.now(timezone.utc)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
        ) as stream:
            json.dump(profile.model_dump(mode="json"), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, destination)

    def create(
        self,
        display_name: str,
        *,
        speaker_id: str,
        language: str = "zh-CN",
        style: str = "natural-vlog",
        consent_relationship: str,
        consent_statement: str,
    ) -> VoiceProfile:
        profile = VoiceProfile(
            display_name=display_name,
            speaker_id=speaker_id,
            language=language,
            style=style,
            consent=ConsentRecord(
                relationship=consent_relationship,
                statement=consent_statement,
            ),
        )
        self._save(profile)
        return profile

    def get(self, profile_id: str) -> VoiceProfile:
        path = self._profile_file(profile_id)
        if not path.is_file():
            raise FileNotFoundError(f'Voice profile "{profile_id}" was not found.')
        return VoiceProfile.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[VoiceProfile]:
        profiles: list[VoiceProfile] = []
        for path in sorted(self.profiles_dir.glob("voice_*/profile.json")):
            profiles.append(VoiceProfile.model_validate_json(path.read_text(encoding="utf-8")))
        return sorted(profiles, key=lambda item: (item.display_name.casefold(), item.id))

    def import_samples(
        self,
        profile_id: str,
        paths: list[str | Path],
        *,
        transcript: str | None = None,
        category: str | None = None,
        delivery: str | None = None,
    ) -> VoiceProfile:
        profile = self.get(profile_id)
        samples_dir = self._profile_dir(profile_id) / "samples"
        samples_dir.mkdir(parents=True, exist_ok=True)
        known = {sample.sha256 for sample in profile.samples}
        changed = False
        for raw in paths:
            source = Path(raw).expanduser().resolve()
            if not source.is_file():
                raise FileNotFoundError(f'Voice sample "{source}" was not found.')
            if source.suffix.casefold() != ".wav":
                raise ValueError("FACUT voice profile v1 accepts PCM WAV samples only.")
            digest = _hash_file(source)
            if digest in known:
                continue
            duration, rate, channels, width = _wave_info(source)
            safe_name = _SAFE_NAME.sub("-", source.name).strip(".-") or "sample.wav"
            relative = f"samples/{digest[:16]}-{safe_name}"
            destination = self._profile_dir(profile_id) / relative
            temporary = destination.with_suffix(destination.suffix + ".tmp")
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
            profile.samples.append(
                VoiceSample(
                    id=f"sample_{digest[:16].upper()}",
                    sha256=digest,
                    stored_path=relative,
                    original_name=source.name,
                    size=source.stat().st_size,
                    duration=duration,
                    sample_rate=rate,
                    channels=channels,
                    sample_width=width,
                    transcript=transcript,
                    category=category,
                    delivery=delivery,
                )
            )
            known.add(digest)
            changed = True
        if changed:
            # Any new evidence requires revalidation; do not leave a previously
            # empty/invalid or ready profile with a stale status.
            profile.status = "draft"
        self._save(profile)
        return profile

    def sample_paths(self, profile: VoiceProfile) -> list[Path]:
        root = self._profile_dir(profile.id).resolve()
        result: list[Path] = []
        for sample in profile.samples:
            candidate = (root / sample.stored_path).resolve()
            if root not in candidate.parents:
                raise ValueError("Voice sample path escaped its profile directory.")
            result.append(candidate)
        return result

    def profile_directory(self, profile_id: str) -> Path:
        """Return the validated private profile directory for local providers."""

        self.get(profile_id)
        return self._profile_dir(profile_id).resolve()

    def set_status(self, profile_id: str, status: str) -> VoiceProfile:
        profile = self.get(profile_id)
        profile.status = status
        self._save(profile)
        return profile

    def delete(self, profile_id: str) -> Path:
        source = self._profile_dir(profile_id)
        self.get(profile_id)
        self.trash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.trash_dir / f"{profile_id}-{stamp}"
        counter = 1
        while destination.exists():
            destination = self.trash_dir / f"{profile_id}-{stamp}-{counter}"
            counter += 1
        shutil.move(str(source), str(destination))
        return destination

    def restore(self, trash_name: str) -> VoiceProfile:
        if Path(trash_name).name != trash_name or not trash_name.startswith("voice_"):
            raise ValueError("trash_name must be a FACUT voice trash entry name.")
        source = self.trash_dir / trash_name
        if not source.is_dir():
            raise FileNotFoundError(f'Voice trash entry "{trash_name}" was not found.')
        profile_file = source / "profile.json"
        profile = VoiceProfile.model_validate_json(profile_file.read_text(encoding="utf-8"))
        destination = self._profile_dir(profile.id)
        if destination.exists():
            raise FileExistsError(f'Voice profile "{profile.id}" already exists.')
        shutil.move(str(source), str(destination))
        return self.get(profile.id)
