"""Project-aware proxy creation, validation, relinking, and status."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from facut.core.models import MediaAsset, MediaKind, ProjectDocument
from facut.core.project_manager import ProjectManager, ProjectError

from .probe import probe_media
from .proxy import generate_proxy


class ProxyError(ProjectError):
    """A proxy cannot be created or safely associated with its source."""

    code = "PROXY_ERROR"
    exit_code = 4


class ProxyAmbiguousError(ProxyError):
    """More than one proxy candidate is equally plausible."""

    code = "PROXY_AMBIGUOUS"


_PROXY_MARKER = re.compile(r"(?:[._-](?:lrf|proxy))$", re.IGNORECASE)
_VIDEO_PROXY_EXTENSIONS = {".mp4", ".mov", ".mkv", ".mxf", ".lrf"}


def proxy_base_stem(path: str | Path) -> str:
    """Return a case-insensitive camera stem with a proxy suffix removed."""

    item = Path(path)
    stem = item.stem
    # A native .LRF file commonly shares the original's complete stem.
    return _PROXY_MARKER.sub("", stem).casefold()


def is_probable_proxy_path(path: str | Path) -> bool:
    """Recognize camera and FACUT proxy naming without probing the file."""

    item = Path(path)
    return item.suffix.casefold() == ".lrf" or bool(_PROXY_MARKER.search(item.stem))


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
        if path.stat().st_size <= 0:
            raise ProxyError(f'Proxy "{path.name}" is empty.')
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

    def _candidate_roots(
        self,
        original: Path,
        search_directories: list[str | Path] | None,
    ) -> list[Path]:
        supplied = [
            Path(item).expanduser().resolve() for item in (search_directories or [])
        ]
        defaults = [
            original.parent,
            original.parent / "proxy",
            original.parent / "proxies",
            self.manager.project_dir / "proxies",
        ]
        return list(dict.fromkeys([*supplied, *defaults]))

    def _candidate_files(
        self,
        original: Path,
        roots: list[Path],
        *,
        recursive_supplied: bool,
    ) -> list[Path]:
        candidates: set[Path] = set()
        for index, root in enumerate(roots):
            if not root.is_dir():
                continue
            iterator = root.rglob("*") if recursive_supplied and index == 0 else root.iterdir()
            for item in iterator:
                if (
                    item.is_file()
                    and item.resolve() != original.resolve()
                    and item.suffix.casefold() in _VIDEO_PROXY_EXTENSIONS
                    and proxy_base_stem(item) == proxy_base_stem(original)
                ):
                    candidates.add(item.resolve())
        return sorted(candidates, key=lambda item: str(item).casefold())

    @staticmethod
    def _candidate_score(
        asset: MediaAsset,
        candidate: Path,
        technical: dict[str, Any],
    ) -> tuple[float, list[str]]:
        reasons: list[str] = []
        score = 0.0
        if proxy_base_stem(candidate) == proxy_base_stem(asset.original_name):
            score += 0.45
            reasons.append("matching_base_name")
        if is_probable_proxy_path(candidate):
            score += 0.20
            reasons.append("explicit_proxy_name")
        original_duration = asset.technical.duration
        candidate_duration = technical.get("duration")
        if original_duration and candidate_duration:
            score += 0.20
            reasons.append("matching_duration")
        original_rate = asset.technical.frame_rate or asset.technical.average_frame_rate
        candidate_rate = technical.get("frame_rate") or technical.get("average_frame_rate")
        if original_rate and candidate_rate:
            tolerance = max(0.1, original_rate * 0.01)
            if abs(float(original_rate) - float(candidate_rate)) <= tolerance:
                score += 0.05
                reasons.append("matching_frame_rate")
        if int(technical.get("rotation") or 0) % 360 == int(asset.technical.rotation or 0) % 360:
            score += 0.05
            reasons.append("matching_rotation")
        width = technical.get("width")
        height = technical.get("height")
        if (
            width
            and height
            and asset.technical.width
            and asset.technical.height
            and int(width) <= asset.technical.width
            and int(height) <= asset.technical.height
        ):
            score += 0.05
            reasons.append("lower_resolution")
        return min(score, 1.0), reasons

    def _evaluate_asset(
        self,
        asset: MediaAsset,
        search_directories: list[str | Path] | None = None,
    ) -> dict[str, Any]:
        original = self.manager.resolve_path(asset.path)
        roots = self._candidate_roots(original, search_directories)
        paths = self._candidate_files(
            original,
            roots,
            recursive_supplied=bool(search_directories),
        )
        candidates: list[dict[str, Any]] = []
        for candidate in paths:
            try:
                technical = self._validate_candidate(asset, candidate)
                score, reasons = self._candidate_score(asset, candidate, technical)
                candidates.append(
                    {
                        "path": str(candidate),
                        "score": round(score, 3),
                        "reasons": reasons,
                        "technical": technical,
                        "valid": True,
                        "error": None,
                    }
                )
            except Exception as error:
                candidates.append(
                    {
                        "path": str(candidate),
                        "score": 0.0,
                        "reasons": [],
                        "technical": None,
                        "valid": False,
                        "error": str(error),
                    }
                )
        valid = sorted(
            (item for item in candidates if item["valid"]),
            key=lambda item: (-float(item["score"]), str(item["path"]).casefold()),
        )
        selected = valid[0] if valid and float(valid[0]["score"]) >= 0.85 else None
        ambiguous = bool(
            selected
            and len(valid) > 1
            and float(valid[1]["score"]) >= 0.85
            and float(selected["score"]) - float(valid[1]["score"]) < 0.10
        )
        status = "ambiguous" if ambiguous else "matched" if selected else "not_found"
        return {
            "media_id": asset.id,
            "original_name": asset.original_name,
            "status": status,
            "selected": None if ambiguous else selected,
            "candidates": candidates,
        }

    def scan(
        self,
        media_id: str | None = None,
        search_directories: list[str | Path] | None = None,
        *,
        link: bool = False,
        dry_run: bool = False,
    ) -> tuple[list[dict[str, Any]], ProjectDocument]:
        """Score LRF/proxy candidates and optionally link all safe matches atomically."""

        document = self.manager.require_document()
        assets = [
            asset
            for asset in document.media
            if asset.kind == MediaKind.VIDEO
            and (media_id is None or asset.id == media_id)
        ]
        if media_id is not None and not assets:
            raise ProxyError(f'Media "{media_id}" was not found or is not video.')
        results = [self._evaluate_asset(asset, search_directories) for asset in assets]
        if not link:
            return results, document
        ambiguous = [item["media_id"] for item in results if item["status"] == "ambiguous"]
        if ambiguous:
            raise ProxyAmbiguousError(
                "Multiple equally plausible proxies were found for " + ", ".join(ambiguous) + ".",
                suggestion="Run `facut proxy scan --json` and link the intended files explicitly.",
            )
        selected = {
            str(item["media_id"]): item["selected"]
            for item in results
            if item["selected"] is not None
        }
        if not selected:
            return results, document

        def operation(candidate_document: ProjectDocument) -> list[str]:
            linked_ids: list[str] = []
            for selected_media_id, match in selected.items():
                asset = self._asset(candidate_document, selected_media_id)
                path = Path(str(match["path"])).resolve()
                stored_path = self.manager.store_path(path)
                asset.proxy_path = stored_path
                asset.metadata["proxy"] = {
                    "path": stored_path,
                    "technical": match["technical"],
                    "match_score": match["score"],
                    "match_reasons": match["reasons"],
                    "source": "automatic_scan",
                }
                linked_ids.append(selected_media_id)
            return linked_ids

        linked_ids, state = self.manager.mutate(
            "proxy.link_auto",
            f"Automatically linked {len(selected)} validated proxy file(s)",
            operation,
            command={
                "media_id": media_id,
                "search_directories": [str(item) for item in (search_directories or [])],
            },
            dry_run=dry_run,
        )
        linked_set = set(linked_ids)
        for item in results:
            if item["media_id"] in linked_set:
                item["status"] = "would_link" if dry_run else "linked"
        return results, state

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
        evaluation = self._evaluate_asset(
            asset,
            [search_directory] if search_directory is not None else None,
        )
        if evaluation["status"] == "ambiguous":
            raise ProxyAmbiguousError(
                f"Multiple equally plausible proxies were found for {asset.original_name}.",
                suggestion="Run `facut proxy scan --json` and use `facut proxy link`.",
            )
        selected = evaluation["selected"]
        if selected is None:
            errors = [
                f"{Path(item['path']).name}: {item['error']}"
                for item in evaluation["candidates"]
                if item["error"]
            ]
            detail = "; ".join(errors[:3]) if errors else "no high-confidence proxy"
            raise ProxyError(f"No valid proxy was found for {asset.original_name}: {detail}.")
        candidate = Path(str(selected["path"])).resolve()
        linked, state = self._link_prevalidated(
            media_id,
            candidate,
            dict(selected["technical"]),
            dry_run=dry_run,
        )
        return linked, state, candidate

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
                    "match_score": asset.metadata.get("proxy", {}).get("match_score"),
                    "match_reasons": asset.metadata.get("proxy", {}).get("match_reasons", []),
                }
            )
        return result
