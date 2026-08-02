"""Project-aware proxy creation, validation, relinking, and status."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from facut.core.models import MediaAsset, MediaKind, ProjectDocument
from facut.core.project_manager import ProjectManager, ProjectError

from .probe import probe_media
from .proxy import generate_proxy


class ProxyError(ProjectError):
    """A proxy cannot be created or safely associated with its source."""

    code = "PROXY_ERROR"
    exit_code = 4


class ProxyManager:
    """Maintain one portable original-to-proxy relationship per media asset."""

    def __init__(
        self,
        manager: ProjectManager,
        *,
        ffmpeg: str | Path | None = None,
        ffprobe: str | Path | None = None,
    ) -> None:
        self.manager = manager
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    @staticmethod
    def _asset(document: ProjectDocument, media_id: str) -> MediaAsset:
        asset = document.find_media(media_id)
        if asset is None:
            raise ProxyError(f'Media "{media_id}" was not found.')
        if asset.kind != MediaKind.VIDEO:
            raise ProxyError("Only video assets can use a video proxy.")
        return asset

    def _validate_candidate(self, asset: MediaAsset, path: Path) -> dict[str, Any]:
        if not path.is_file():
            raise FileNotFoundError(f'Proxy file "{path}" was not found.')
        technical = probe_media(path, ffprobe=self.ffprobe, include_keyframes=False)
        if not technical.video_codec:
            raise ProxyError(f'Proxy "{path.name}" has no video stream.')
        original_duration = asset.technical.duration
        if original_duration and technical.duration:
            tolerance = max(0.25, 2 / (asset.technical.frame_rate or 25.0))
            if abs(original_duration - technical.duration) > tolerance:
                raise ProxyError(
                    f'Proxy duration {technical.duration:.3f}s does not match original '
                    f'{original_duration:.3f}s (tolerance {tolerance:.3f}s).'
                )
        return technical.model_dump(mode="json")

    def _link_prevalidated(
        self,
        media_id: str,
        path: Path,
        technical: dict[str, Any],
        *,
        dry_run: bool,
    ) -> tuple[MediaAsset, ProjectDocument]:
        stored_path = self.manager.store_path(path)

        def operation(document: ProjectDocument) -> MediaAsset:
            asset = self._asset(document, media_id)
            asset.proxy_path = stored_path
            asset.metadata["proxy"] = {
                "path": stored_path,
                "technical": technical,
            }
            return asset

        return self.manager.mutate(
            "proxy.link",
            f"Linked proxy {path.name} to {media_id}",
            operation,
            command={"media_id": media_id, "path": str(path)},
            dry_run=dry_run,
        )

    def link(
        self, media_id: str, path: str | Path, *, dry_run: bool = False
    ) -> tuple[MediaAsset, ProjectDocument]:
        document = self.manager.require_document()
        asset = self._asset(document, media_id)
        candidate = Path(path).expanduser().resolve()
        technical = self._validate_candidate(asset, candidate)
        return self._link_prevalidated(
            media_id, candidate, technical, dry_run=dry_run
        )

    def create(
        self,
        media_id: str,
        *,
        height: int = 540,
        codec: str = "h264",
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> tuple[MediaAsset | None, ProjectDocument, Path]:
        document = self.manager.require_document()
        asset = self._asset(document, media_id)
        destination = (
            self.manager.project_dir / "proxies" / f"{asset.id}-{height}p.mp4"
        ).resolve()
        if dry_run:
            return None, document, destination
        source = self.manager.resolve_path(asset.path)
        generated = generate_proxy(
            source,
            destination,
            height=height,
            codec=codec,
            ffmpeg=self.ffmpeg,
            overwrite=overwrite,
        )
        technical = self._validate_candidate(asset, generated)
        linked, state = self._link_prevalidated(
            media_id, generated, technical, dry_run=False
        )
        return linked, state, generated

    def relink(
        self,
        media_id: str,
        search_directory: str | Path | None = None,
        *,
        dry_run: bool = False,
    ) -> tuple[MediaAsset, ProjectDocument, Path]:
        document = self.manager.require_document()
        asset = self._asset(document, media_id)
        original = self.manager.resolve_path(asset.path)
        roots = [
            Path(search_directory).expanduser().resolve()
            if search_directory is not None
            else self.manager.project_dir / "proxies",
            original.parent,
        ]
        stems = {
            original.stem.casefold(),
            f"{original.stem}_lrf".casefold(),
            f"{original.stem}-lrf".casefold(),
            f"{original.stem}_proxy".casefold(),
            f"{original.stem}-proxy".casefold(),
            f"{asset.id}-540p".casefold(),
        }
        candidates: list[Path] = []
        for root in dict.fromkeys(roots):
            if not root.is_dir():
                continue
            candidates.extend(
                item
                for item in root.iterdir()
                if item.is_file()
                and item != original
                and item.stem.casefold() in stems
                and item.suffix.lower() in {".mp4", ".mov", ".mkv", ".mxf"}
            )
        errors: list[str] = []
        for candidate in sorted(set(candidates), key=lambda item: str(item).casefold()):
            try:
                technical = self._validate_candidate(asset, candidate)
            except Exception as error:
                errors.append(f"{candidate.name}: {error}")
                continue
            linked, state = self._link_prevalidated(
                media_id, candidate, technical, dry_run=dry_run
            )
            return linked, state, candidate
        detail = "; ".join(errors[:3]) if errors else "no matching proxy filenames"
        raise ProxyError(f"No valid proxy was found for {asset.original_name}: {detail}.")

    def status(self, media_id: str | None = None) -> list[dict[str, Any]]:
        document = self.manager.require_document()
        assets = [
            asset
            for asset in document.media
            if asset.kind == MediaKind.VIDEO
            and (media_id is None or asset.id == media_id)
        ]
        if media_id is not None and not assets:
            raise ProxyError(f'Media "{media_id}" was not found or is not video.')
        result: list[dict[str, Any]] = []
        for asset in assets:
            original = self.manager.resolve_path(asset.path)
            proxy = (
                self.manager.resolve_path(asset.proxy_path)
                if asset.proxy_path
                else None
            )
            result.append(
                {
                    "media_id": asset.id,
                    "original_name": asset.original_name,
                    "original_path": str(original),
                    "original_online": original.is_file(),
                    "proxy_path": str(proxy) if proxy else None,
                    "proxy_linked": proxy is not None,
                    "proxy_online": proxy.is_file() if proxy else False,
                    "preview_source": str(proxy)
                    if proxy is not None and proxy.is_file()
                    else str(original),
                    "final_source": str(original),
                }
            )
        return result
