"""Streaming, content-addressed media import."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from facut.core.models import MediaAsset, MediaKind, MediaTechnicalInfo
from facut.core.project_manager import ProjectManager, ProjectError

from .probe import probe_media

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".aac", ".flac", ".m4a", ".ogg", ".opus"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}
SUPPORTED_EXTENSIONS = (
    VIDEO_EXTENSIONS | AUDIO_EXTENSIONS | IMAGE_EXTENSIONS | SUBTITLE_EXTENSIONS
)


class UnsupportedMediaError(ProjectError):
    code = "UNSUPPORTED_MEDIA"
    exit_code = 4


class MediaImporter:
    def __init__(self, manager: ProjectManager) -> None:
        self.manager = manager

    def import_paths(
        self,
        paths: Iterable[str | Path],
        *,
        recursive: bool = False,
        dry_run: bool = False,
    ) -> list[MediaAsset]:
        files = self._expand_paths(paths, recursive=recursive)
        prepared = [self._prepare(path) for path in files]

        def operation(document):
            existing_by_hash = {asset.sha256: asset for asset in document.media}
            imported: list[MediaAsset] = []
            for asset in prepared:
                existing = existing_by_hash.get(asset.sha256)
                if existing is not None:
                    imported.append(existing)
                    continue
                document.media.append(asset)
                existing_by_hash[asset.sha256] = asset
                imported.append(asset)
            return imported

        names = ", ".join(path.name for path in files[:3])
        if len(files) > 3:
            names += f" and {len(files) - 3} more"
        result, _ = self.manager.mutate(
            "media.import",
            f"Imported {len(files)} media file(s): {names}",
            operation,
            command={"paths": [str(path) for path in files], "recursive": recursive},
            dry_run=dry_run,
        )
        return result

    def _expand_paths(
        self, paths: Iterable[str | Path], *, recursive: bool
    ) -> list[Path]:
        discovered: list[Path] = []
        for supplied in paths:
            path = Path(supplied).expanduser().resolve()
            if not path.exists():
                raise FileNotFoundError(f'Media path "{path}" was not found.')
            if path.is_file():
                discovered.append(path)
                continue
            iterator = path.rglob("*") if recursive else path.glob("*")
            discovered.extend(item for item in iterator if item.is_file())
        result = sorted(
            {path for path in discovered if path.suffix.lower() in SUPPORTED_EXTENSIONS},
            key=lambda item: str(item).casefold(),
        )
        if not result:
            raise UnsupportedMediaError("No supported media files were found.")
        return result

    def _prepare(self, path: Path) -> MediaAsset:
        extension = path.suffix.lower()
        kind = kind_for_extension(extension)
        digest = hash_file(path)
        technical = (
            MediaTechnicalInfo()
            if kind == MediaKind.SUBTITLE
            else probe_media(path, include_keyframes=False)
        )
        return MediaAsset(
            id=f"media_{digest[:16].upper()}",
            kind=kind,
            path=self.manager.store_path(path),
            original_name=path.name,
            size=path.stat().st_size,
            sha256=digest,
            technical=technical,
        )


def hash_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def kind_for_extension(extension: str) -> MediaKind:
    if extension in VIDEO_EXTENSIONS:
        return MediaKind.VIDEO
    if extension in AUDIO_EXTENSIONS:
        return MediaKind.AUDIO
    if extension in IMAGE_EXTENSIONS:
        return MediaKind.IMAGE
    if extension in SUBTITLE_EXTENSIONS:
        return MediaKind.SUBTITLE
    raise UnsupportedMediaError(f'Unsupported media extension "{extension}".')
