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

from .models import ConsentRecord, VoiceProfile, VoiceSample, VoiceSampleCandidate


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class VoiceProfileAmbiguousError(ValueError):
    """Raised when a human-readable selector matches multiple profiles."""

    code = "VOICE_AMBIGUOUS"
    exit_code = 2


class VoiceAliasConflictError(ValueError):
    """Raised when an alias is already owned by another profile."""

    code = "VOICE_ALIAS_CONFLICT"
    exit_code = 2


class VoiceSpeakerGuardError(ValueError):
    """Raised when a sample cannot be safely assigned to an existing voice."""

    code = "VOICE_SPEAKER_MISMATCH"
    exit_code = 2


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

    def _candidate_file(self, profile_id: str, candidate_id: str) -> Path:
        if not re.fullmatch(r"candidate_[A-Z0-9_]{4,64}", candidate_id):
            raise ValueError(f'Invalid voice sample candidate ID "{candidate_id}".')
        return self._profile_dir(profile_id) / "candidates" / f"{candidate_id}.json"

    @staticmethod
    def _atomic_write_model(destination: Path, payload: dict) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, destination)

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

    def resolve(self, selector: str) -> VoiceProfile:
        """Resolve a profile by stable ID, unique alias, or unique display name.

        IDs have highest priority, followed by aliases. Display names are
        intentionally allowed to repeat, but a repeated name is never guessed.
        Matching for human-readable selectors is case-insensitive.
        """

        value = selector.strip()
        if not value:
            raise ValueError("Voice profile selector cannot be empty.")
        if re.fullmatch(r"voice_[A-Z0-9_]{4,64}", value):
            return self.get(value)
        profiles = self.list()
        key = value.casefold()
        alias_matches = [
            profile
            for profile in profiles
            if any(alias.casefold() == key for alias in profile.aliases)
        ]
        if len(alias_matches) == 1:
            return alias_matches[0]
        if len(alias_matches) > 1:
            raise VoiceProfileAmbiguousError(
                f'Voice selector "{selector}" matches multiple aliases: '
                + ", ".join(item.id for item in alias_matches)
                + ". Use the stable voice ID."
            )
        name_matches = [
            profile for profile in profiles if profile.display_name.casefold() == key
        ]
        if len(name_matches) == 1:
            return name_matches[0]
        if len(name_matches) > 1:
            raise VoiceProfileAmbiguousError(
                f'Voice selector "{selector}" matches multiple display names: '
                + ", ".join(item.id for item in name_matches)
                + ". Assign a unique alias or use the stable voice ID."
            )
        raise FileNotFoundError(f'Voice profile "{selector}" was not found.')

    def rename(self, selector: str, display_name: str) -> VoiceProfile:
        """Rename a profile without changing its stable identity or samples."""

        name = display_name.strip()
        if not name:
            raise ValueError("Voice profile display name cannot be empty.")
        profile = self.resolve(selector)
        profile.display_name = name
        self._save(profile)
        return profile

    def set_alias(self, selector: str, alias: str) -> VoiceProfile:
        """Add a globally unique command-line alias to one profile."""

        value = alias.strip()
        profile = self.resolve(selector)
        # Let the model validator enforce whitespace and length constraints.
        profile.aliases = [*profile.aliases, value]
        key = value.casefold()
        for other in self.list():
            if other.id == profile.id:
                continue
            if other.id.casefold() == key or any(
                existing.casefold() == key for existing in other.aliases
            ) or other.display_name.casefold() == key:
                raise VoiceAliasConflictError(
                    f'Voice alias "{alias}" conflicts with {other.id}.'
                )
        self._save(profile)
        return profile

    @staticmethod
    def _project_default_path(project: str | Path) -> Path:
        target = Path(project).expanduser().resolve()
        project_dir = target.parent if target.is_file() or target.name == "facut.json" else target
        return project_dir / ".facut" / "voice-default.json"

    def set_default(
        self,
        selector: str,
        *,
        scope: str = "global",
        project: str | Path | None = None,
    ) -> VoiceProfile:
        """Persist a stable default profile ID globally or for one project."""

        profile = self.resolve(selector)
        if scope == "global":
            destination = self.root / "default.json"
        elif scope == "project":
            if project is None:
                raise ValueError("A project path is required for a project-scoped default voice.")
            destination = self._project_default_path(project)
        else:
            raise ValueError('Voice default scope must be "global" or "project".')
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": "1.0", "profile_id": profile.id, "scope": scope}
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False, suffix=".tmp"
        ) as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            temporary = Path(stream.name)
        os.replace(temporary, destination)
        return profile

    def get_default(self, *, project: str | Path | None = None) -> VoiceProfile | None:
        """Return a project default, then the global default, if still present."""

        candidates: list[Path] = []
        if project is not None:
            candidates.append(self._project_default_path(project))
        candidates.append(self.root / "default.json")
        for path in candidates:
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return self.get(str(payload["profile_id"]))
            except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return None

    def import_samples(
        self,
        profile_id: str,
        paths: list[str | Path],
        *,
        transcript: str | None = None,
        category: str | None = None,
        delivery: str | None = None,
        speaker_similarity: float | None = None,
        speaker_confirmed: bool = False,
        confirmation_statement: str | None = None,
        trusted_capture: bool = False,
    ) -> VoiceProfile:
        profile = self.get(profile_id)
        threshold = 0.72
        statement = (confirmation_statement or "").strip()
        if speaker_confirmed and len(statement) < 12:
            raise VoiceSpeakerGuardError(
                "Explicit speaker confirmation requires a confirmation statement of at least 12 characters."
            )
        if speaker_similarity is not None and not 0.0 <= speaker_similarity <= 1.0:
            raise ValueError("speaker_similarity must be between 0 and 1.")
        if profile.samples and not trusted_capture:
            similarity_passed = (
                speaker_similarity is not None and speaker_similarity >= threshold
            )
            explicit_override = speaker_confirmed and len(statement) >= 12
            if not similarity_passed and not explicit_override:
                detail = (
                    f"measured similarity {speaker_similarity:.3f} is below {threshold:.2f}"
                    if speaker_similarity is not None
                    else "no speaker similarity result was supplied"
                )
                raise VoiceSpeakerGuardError(
                    f'Voice sample import into "{profile.display_name}" was blocked: {detail}. '
                    "Quarantine it with `facut voice sample propose`, or explicitly confirm "
                    "the speaker with --speaker-confirmed and --confirmation-statement."
                )
        elif len({str(Path(item).expanduser().resolve()) for item in paths}) > 1 and not trusted_capture and not (
            speaker_confirmed and len(statement) >= 12
        ):
            raise VoiceSpeakerGuardError(
                "A first-time batch import cannot establish that every file contains the same speaker. "
                "Import one baseline sample, quarantine candidates, or explicitly confirm the batch."
            )
        verification: dict[str, str | float | bool] = {
            "basis": (
                "trusted_capture"
                if trusted_capture
                else "speaker_similarity"
                if speaker_similarity is not None and speaker_similarity >= threshold
                else "manual_confirmation"
                if speaker_confirmed
                else "initial_sample"
            ),
            "speaker_confirmed": bool(speaker_confirmed or trusted_capture),
        }
        if speaker_similarity is not None:
            verification["similarity"] = round(speaker_similarity, 6)
            verification["threshold"] = threshold
        if statement:
            verification["confirmation_sha256"] = hashlib.sha256(
                statement.encode("utf-8")
            ).hexdigest()
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
                    identity_verification=verification,
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

    def propose_candidate(
        self,
        selector: str,
        source_path: str | Path,
        *,
        transcript: str | None = None,
        category: str | None = None,
        delivery: str | None = None,
        source_media_id: str | None = None,
        source_start: float | None = None,
        source_end: float | None = None,
        identity_basis: str = "similarity",
    ) -> VoiceSampleCandidate:
        """Copy audio into quarantine without making it a synthesis reference."""

        profile = self.resolve(selector)
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f'Voice candidate "{source}" was not found.')
        if source.suffix.casefold() != ".wav":
            raise ValueError("FACUT voice candidates must be PCM WAV files.")
        if source_end is not None and source_start is not None and source_end <= source_start:
            raise ValueError("Voice candidate source_end must be greater than source_start.")
        digest = _hash_file(source)
        for existing in self.list_candidates(profile.id):
            if existing.sha256 == digest:
                return existing
        duration, rate, channels, width = _wave_info(source)
        safe_name = _SAFE_NAME.sub("-", source.name).strip(".-") or "candidate.wav"
        relative = f"candidates/audio/{digest[:16]}-{safe_name}"
        destination = self._profile_dir(profile.id) / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
        candidate = VoiceSampleCandidate(
            profile_id=profile.id,
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
            source_media_id=source_media_id,
            source_start=source_start,
            source_end=source_end,
            identity_basis=identity_basis,
        )
        self._atomic_write_model(
            self._candidate_file(profile.id, candidate.id),
            candidate.model_dump(mode="json"),
        )
        return candidate

    def list_candidates(self, selector: str | None = None) -> list[VoiceSampleCandidate]:
        profiles = [self.resolve(selector)] if selector else self.list()
        result: list[VoiceSampleCandidate] = []
        for profile in profiles:
            directory = self._profile_dir(profile.id) / "candidates"
            for path in sorted(directory.glob("candidate_*.json")):
                result.append(VoiceSampleCandidate.model_validate_json(path.read_text("utf-8")))
        return sorted(result, key=lambda item: (item.proposed_at, item.id))

    def get_candidate(self, candidate_id: str) -> VoiceSampleCandidate:
        matches = [item for item in self.list_candidates() if item.id == candidate_id]
        if len(matches) != 1:
            raise FileNotFoundError(f'Voice sample candidate "{candidate_id}" was not found.')
        return matches[0]

    def approve_candidate(
        self,
        candidate_id: str,
        *,
        speaker_confirmed: bool,
        confirmation_statement: str,
    ) -> tuple[VoiceSampleCandidate, VoiceProfile]:
        candidate = self.get_candidate(candidate_id)
        if candidate.status != "pending":
            raise ValueError(f'Voice sample candidate "{candidate_id}" is already {candidate.status}.')
        statement = confirmation_statement.strip()
        if not speaker_confirmed or len(statement) < 12:
            raise PermissionError(
                "Approving candidate audio requires --speaker-confirmed and a confirmation statement."
            )
        source = (self._profile_dir(candidate.profile_id) / candidate.stored_path).resolve()
        profile_root = self._profile_dir(candidate.profile_id).resolve()
        if profile_root not in source.parents or not source.is_file():
            raise FileNotFoundError("Quarantined voice candidate audio is missing.")
        profile = self.import_samples(
            candidate.profile_id,
            [source],
            transcript=candidate.transcript,
            category=candidate.category,
            delivery=candidate.delivery,
            speaker_confirmed=True,
            confirmation_statement=statement,
            trusted_capture=True,
        )
        candidate.status = "approved"
        candidate.speaker_confirmed = True
        candidate.confirmation_statement = statement
        candidate.resolution_reason = (
            "The user confirmed this recording belongs to the authorized speaker."
        )
        candidate.resolved_at = datetime.now(timezone.utc)
        self._atomic_write_model(
            self._candidate_file(candidate.profile_id, candidate.id),
            candidate.model_dump(mode="json"),
        )
        return candidate, profile

    def reject_candidate(self, candidate_id: str, *, reason: str) -> VoiceSampleCandidate:
        candidate = self.get_candidate(candidate_id)
        if candidate.status != "pending":
            raise ValueError(f'Voice sample candidate "{candidate_id}" is already {candidate.status}.')
        explanation = reason.strip()
        if not explanation:
            raise ValueError("Rejecting a voice candidate requires a reason.")
        candidate.status = "rejected"
        candidate.resolution_reason = explanation
        candidate.resolved_at = datetime.now(timezone.utc)
        self._atomic_write_model(
            self._candidate_file(candidate.profile_id, candidate.id),
            candidate.model_dump(mode="json"),
        )
        return candidate

    def sample_paths(self, profile: VoiceProfile) -> list[Path]:
        root = self._profile_dir(profile.id).resolve()
        result: list[Path] = []
        for sample in profile.samples:
            candidate = (root / sample.stored_path).resolve()
            if root not in candidate.parents:
                raise ValueError("Voice sample path escaped its profile directory.")
            result.append(candidate)
        return result

    def remove_sample(self, selector: str, sample_id: str) -> tuple[VoiceSample, str]:
        """Remove one sample from synthesis and keep a recoverable private copy."""

        profile = self.resolve(selector)
        matches = [item for item in profile.samples if item.id == sample_id]
        if len(matches) != 1:
            raise FileNotFoundError(
                f'Voice sample "{sample_id}" was not found in profile "{profile.id}".'
            )
        sample = matches[0]
        profile_root = self._profile_dir(profile.id).resolve()
        source = (profile_root / sample.stored_path).resolve()
        if profile_root not in source.parents:
            raise ValueError("Voice sample path escaped its profile directory.")
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        trash_name = f"{sample.id}-{stamp}.wav"
        destination = profile_root / ".trash" / "samples" / trash_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_file():
            os.replace(source, destination)
        profile.samples = [item for item in profile.samples if item.id != sample.id]
        profile.status = "draft"
        self._save(profile)
        # Derived references may have mixed this sample into cached prompts.
        derived = profile_root / "derived"
        if derived.is_dir():
            shutil.rmtree(derived)
        return sample, trash_name

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
