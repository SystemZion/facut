"""Orchestration for file- and project-level automated QC."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from facut.core.project_manager import ProjectManager
from facut.media.probe import probe_raw
from facut.media.tools import find_executable

from .detectors import (
    check_black_frames,
    check_decode,
    check_loudness,
    check_silence,
    generate_contact_sheet,
)
from .models import CheckResult, FileQCResult, QCReport, QCStatus


@dataclass(frozen=True, slots=True)
class QCSource:
    path: Path
    media_id: str | None = None
    kind: str | None = None


def resolve_qc_scope(
    target: str | Path | None,
    *,
    project: str | Path | None = None,
) -> tuple[dict[str, Any], list[QCSource]]:
    """Resolve one media file or every media reference in a facut project."""

    if target is not None:
        candidate = Path(target).expanduser().resolve()
        is_project = candidate.is_dir() or candidate.name.lower() == ProjectManager.FILE_NAME
        if not is_project:
            if not candidate.is_file():
                raise FileNotFoundError(f'QC target "{candidate}" was not found.')
            return {"type": "file", "path": str(candidate)}, [QCSource(candidate)]
        manager = ProjectManager(candidate)
    elif project is not None:
        manager = ProjectManager(project)
    else:
        manager = ProjectManager.discover(Path.cwd())

    document = manager.load() if manager.document is None else manager.require_document()
    sources = [
        QCSource(
            path=manager.resolve_path(asset.path),
            media_id=asset.id,
            kind=asset.kind.value,
        )
        for asset in document.media
    ]
    return (
        {
            "type": "project",
            "path": str(manager.project_file.resolve()),
            "name": document.project.name,
            "revision": document.revision,
        },
        sources,
    )


class QCEngine:
    """Run bounded-memory FFprobe and full-decode quality checks."""

    def __init__(
        self,
        *,
        ffmpeg: str | Path | None = None,
        ffprobe: str | Path | None = None,
        black_minimum_duration: float = 0.5,
        black_pixel_threshold: float = 0.10,
        silence_threshold_db: float = -50.0,
        silence_minimum_duration: float = 0.5,
        target_lufs: float = -14.0,
        loudness_tolerance_lu: float = 2.0,
        maximum_true_peak_dbfs: float = -1.0,
        timeout: float | None = None,
    ) -> None:
        self.ffmpeg = find_executable("ffmpeg", ffmpeg)
        self.ffprobe = find_executable("ffprobe", ffprobe)
        self.black_minimum_duration = black_minimum_duration
        self.black_pixel_threshold = black_pixel_threshold
        self.silence_threshold_db = silence_threshold_db
        self.silence_minimum_duration = silence_minimum_duration
        self.target_lufs = target_lufs
        self.loudness_tolerance_lu = loudness_tolerance_lu
        self.maximum_true_peak_dbfs = maximum_true_peak_dbfs
        self.timeout = timeout

    def run(
        self,
        scope: dict[str, Any],
        sources: list[QCSource],
        *,
        contact_sheet: str | Path | None = None,
        overwrite: bool = False,
    ) -> QCReport:
        results = [
            self.analyze_file(
                source,
                contact_sheet=self._contact_output(contact_sheet, source, index, len(sources)),
                overwrite=overwrite,
            )
            for index, source in enumerate(sources)
        ]
        passed = sum(item.status == QCStatus.PASS for item in results)
        warned = sum(item.status in {QCStatus.WARNING, QCStatus.SKIPPED} for item in results)
        failed = sum(item.status == QCStatus.FAIL for item in results)
        if failed:
            status = QCStatus.FAIL
        elif warned or not results:
            status = QCStatus.WARNING
        else:
            status = QCStatus.PASS
        warnings = ["The project contains no media assets."] if not results else []
        return QCReport(
            scope=scope,
            status=status,
            summary={
                "total": len(results),
                "passed": passed,
                "warnings": warned,
                "failed": failed,
            },
            files=results,
            warnings=warnings,
        )

    def analyze_file(
        self,
        source: QCSource,
        *,
        contact_sheet: Path | None = None,
        overwrite: bool = False,
    ) -> FileQCResult:
        checks: dict[str, CheckResult] = {}
        metadata: dict[str, Any] = {}
        if source.kind == "subtitle":
            checks["probe"] = CheckResult(
                status=QCStatus.SKIPPED,
                summary="Text subtitle assets are outside audio/video QC scope.",
            )
            return self._file_result(source, metadata, checks)
        try:
            raw = probe_raw(source.path, ffprobe=self.ffprobe, timeout=60.0)
            metadata = _compact_metadata(raw)
            checks["probe"] = CheckResult(
                status=QCStatus.PASS,
                summary="FFprobe metadata was read successfully.",
                data={
                    "stream_count": len(raw.get("streams") or []),
                    "format_name": (raw.get("format") or {}).get("format_name"),
                },
            )
        except Exception as error:
            checks["probe"] = CheckResult(
                status=QCStatus.FAIL,
                summary="FFprobe metadata inspection failed.",
                errors=[str(error)],
            )
            return self._file_result(source, metadata, checks)

        streams = metadata.get("streams") or []
        has_video = any(item.get("codec_type") == "video" for item in streams)
        has_audio = any(item.get("codec_type") == "audio" for item in streams)
        duration = _duration(metadata)
        if has_video or has_audio:
            checks["decode"] = self._safe_check(
                "Full decode",
                check_decode,
                source.path,
                self.ffmpeg,
                timeout=self.timeout,
            )
        else:
            checks["decode"] = CheckResult(
                status=QCStatus.SKIPPED,
                summary="No decodable video or audio stream was found.",
            )
        if has_video and source.kind != "image":
            checks["black_frames"] = self._safe_check(
                "Black-frame analysis",
                check_black_frames,
                source.path,
                self.ffmpeg,
                minimum_duration=self.black_minimum_duration,
                pixel_threshold=self.black_pixel_threshold,
                timeout=self.timeout,
            )
        else:
            checks["black_frames"] = CheckResult(
                status=QCStatus.SKIPPED,
                summary="Black-segment detection requires a timed video stream.",
            )
        if has_audio:
            checks["silence"] = self._safe_check(
                "Silence analysis",
                check_silence,
                source.path,
                self.ffmpeg,
                threshold_db=self.silence_threshold_db,
                minimum_duration=self.silence_minimum_duration,
                media_duration=duration,
                timeout=self.timeout,
            )
            checks["loudness"] = self._safe_check(
                "EBU R128 loudness analysis",
                check_loudness,
                source.path,
                self.ffmpeg,
                target_lufs=self.target_lufs,
                tolerance_lu=self.loudness_tolerance_lu,
                maximum_true_peak_dbfs=self.maximum_true_peak_dbfs,
                timeout=self.timeout,
            )
        else:
            checks["silence"] = CheckResult(
                status=QCStatus.SKIPPED,
                summary="Silence detection requires an audio stream.",
            )
            checks["loudness"] = CheckResult(
                status=QCStatus.SKIPPED,
                summary="Loudness analysis requires an audio stream.",
            )
        generated: str | None = None
        if contact_sheet is not None:
            if has_video:
                try:
                    generated_path = generate_contact_sheet(
                        source.path,
                        contact_sheet,
                        self.ffmpeg,
                        duration=duration,
                        overwrite=overwrite,
                        timeout=self.timeout,
                    )
                    generated = str(generated_path.resolve())
                    checks["contact_sheet"] = CheckResult(
                        status=QCStatus.PASS,
                        summary="Contact sheet was generated.",
                        data={"output": generated},
                    )
                except Exception as error:
                    checks["contact_sheet"] = CheckResult(
                        status=QCStatus.FAIL,
                        summary="Contact-sheet generation failed.",
                        errors=[str(error)],
                    )
            else:
                checks["contact_sheet"] = CheckResult(
                    status=QCStatus.SKIPPED,
                    summary="Contact sheets require a video or image stream.",
                )
        return self._file_result(source, metadata, checks, contact_sheet=generated)

    @staticmethod
    def _safe_check(
        label: str,
        operation: Callable[..., CheckResult],
        *args: Any,
        **kwargs: Any,
    ) -> CheckResult:
        """Convert tool/runtime failures into the stable per-check contract."""

        try:
            return operation(*args, **kwargs)
        except Exception as error:
            stderr = getattr(error, "stderr", "")
            errors = [str(error)]
            if stderr:
                errors.extend(str(stderr).splitlines()[-20:])
            return CheckResult(
                status=QCStatus.FAIL,
                summary=f"{label} failed.",
                errors=errors,
            )

    @staticmethod
    def _file_result(
        source: QCSource,
        metadata: dict[str, Any],
        checks: dict[str, CheckResult],
        *,
        contact_sheet: str | None = None,
    ) -> FileQCResult:
        if any(item.status == QCStatus.FAIL for item in checks.values()):
            status = QCStatus.FAIL
        elif any(item.status == QCStatus.WARNING for item in checks.values()):
            status = QCStatus.WARNING
        elif checks and all(item.status == QCStatus.SKIPPED for item in checks.values()):
            status = QCStatus.SKIPPED
        else:
            status = QCStatus.PASS
        issues = [
            f"{name}: {check.summary}"
            for name, check in checks.items()
            if check.status in {QCStatus.WARNING, QCStatus.FAIL}
        ]
        return FileQCResult(
            path=str(source.path.resolve()),
            media_id=source.media_id,
            kind=source.kind,
            status=status,
            metadata=metadata,
            checks=checks,
            issues=issues,
            contact_sheet=contact_sheet,
        )

    @staticmethod
    def _contact_output(
        requested: str | Path | None,
        source: QCSource,
        index: int,
        total: int,
    ) -> Path | None:
        if requested is None:
            return None
        destination = Path(requested).expanduser().resolve()
        if total == 1 and destination.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
            return destination
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", source.media_id or source.path.stem)
        return destination / f"{index + 1:03d}-{safe_id}-contact-sheet.jpg"


def _compact_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    format_data = raw.get("format") or {}
    streams = []
    for stream in raw.get("streams") or []:
        streams.append(
            {
                key: stream.get(key)
                for key in (
                    "index",
                    "codec_type",
                    "codec_name",
                    "profile",
                    "width",
                    "height",
                    "pix_fmt",
                    "r_frame_rate",
                    "avg_frame_rate",
                    "sample_rate",
                    "channels",
                    "channel_layout",
                    "duration",
                    "bit_rate",
                )
                if stream.get(key) is not None
            }
        )
    return {
        "format": {
            key: format_data.get(key)
            for key in ("format_name", "format_long_name", "duration", "size", "bit_rate")
            if format_data.get(key) is not None
        },
        "streams": streams,
    }


def _duration(metadata: dict[str, Any]) -> float | None:
    candidates = [(metadata.get("format") or {}).get("duration")]
    candidates.extend(item.get("duration") for item in metadata.get("streams") or [])
    values: list[float] = []
    for candidate in candidates:
        try:
            values.append(float(candidate))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None
