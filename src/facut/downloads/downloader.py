"""Safe, resumable HTTP downloader for separately distributed AI models."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from facut.exceptions import FacutError, InvalidArgumentError

from .catalog import DownloadSource, ModelFile, ModelPackage


class DownloadError(FacutError):
    code = "MODEL_DOWNLOAD_FAILED"


ProgressCallback = Callable[[dict[str, Any]], None]
_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class SourceBenchmark:
    source: DownloadSource
    bytes_read: int
    elapsed_seconds: float
    supports_resume: bool
    error: str | None = None

    @property
    def bytes_per_second(self) -> float:
        return self.bytes_read / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    def public_dict(self) -> dict[str, Any]:
        return {
            "source": self.source.name,
            "bytes_read": self.bytes_read,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "mbps": round(self.bytes_per_second / 1_000_000, 3),
            "supports_resume": self.supports_resume,
            "available": self.error is None and self.bytes_read > 0,
            "error": self.error,
        }


def _sha256(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, model_file: ModelFile, *, full_hash: bool) -> tuple[bool, str | None]:
    if not path.is_file():
        return False, "missing"
    size = path.stat().st_size
    if size != model_file.size:
        return False, f"size mismatch: expected {model_file.size}, got {size}"
    if full_hash and model_file.sha256:
        actual = _sha256(path)
        if actual.lower() != model_file.sha256.lower():
            return False, f"SHA-256 mismatch: expected {model_file.sha256}, got {actual}"
    return True, None


def _quarantine(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = path.with_name(f"{path.name}.invalid-{stamp}")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(f"{path.name}.invalid-{stamp}-{counter}")
        counter += 1
    os.replace(path, candidate)
    return candidate


class ModelDownloader:
    """Download a pinned package with mirror benchmarking and Range resume."""

    def __init__(
        self,
        package: ModelPackage,
        root: Path,
        *,
        timeout_seconds: float = 30.0,
        chunk_size: int = 1024 * 1024,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.package = package
        self.root = root.expanduser().resolve()
        self.timeout_seconds = timeout_seconds
        self.chunk_size = chunk_size
        self.progress = progress or (lambda event: None)

    @property
    def destination(self) -> Path:
        return self.root / self.package.directory_name

    @property
    def receipt_path(self) -> Path:
        return self.destination / ".facut-model.json"

    def _catalog_fingerprint(self) -> str:
        payload = [
            {"path": item.path, "size": item.size, "sha256": item.sha256}
            for item in self.package.files
        ]
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _receipt_valid(self) -> bool:
        try:
            payload = json.loads(self.receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        if payload.get("model") != self.package.name:
            return False
        if payload.get("catalog_fingerprint") != self._catalog_fingerprint():
            return False
        entries = {item.get("path"): item for item in payload.get("files", [])}
        for model_file in self.package.files:
            path = self.destination / model_file.path
            try:
                stat = path.stat()
            except OSError:
                return False
            entry = entries.get(model_file.path) or {}
            if stat.st_size != model_file.size:
                return False
            if entry.get("size") != stat.st_size or entry.get("mtime_ns") != stat.st_mtime_ns:
                return False
        return True

    def _write_receipt(self) -> None:
        payload = {
            "version": 1,
            "model": self.package.name,
            "display_name": self.package.display_name,
            "catalog_fingerprint": self._catalog_fingerprint(),
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "files": [
                {
                    "path": item.path,
                    "size": (self.destination / item.path).stat().st_size,
                    "mtime_ns": (self.destination / item.path).stat().st_mtime_ns,
                    "sha256": item.sha256,
                }
                for item in self.package.files
            ],
        }
        temporary = self.receipt_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, self.receipt_path)

    def _emit(self, event: str, **values: Any) -> None:
        self.progress({"event": event, "model": self.package.name, **values})

    def benchmark_sources(
        self,
        *,
        sample_bytes: int = 2 * 1024 * 1024,
        maximum_seconds: float = 12.0,
    ) -> list[SourceBenchmark]:
        item = next(file for file in self.package.files if file.path == self.package.benchmark_file)
        results: list[SourceBenchmark] = []
        for source in self.package.sources:
            request = Request(
                source.url(item.path),
                headers={
                    "Range": f"bytes=0-{sample_bytes - 1}",
                    "User-Agent": "facut-model-downloader/1",
                    "Accept-Encoding": "identity",
                },
            )
            started = time.perf_counter()
            count = 0
            supports_resume = False
            error: str | None = None
            try:
                with urlopen(request, timeout=min(self.timeout_seconds, maximum_seconds)) as response:
                    status = getattr(response, "status", response.getcode())
                    content_range = response.headers.get("Content-Range", "")
                    supports_resume = status == 206 and bool(_CONTENT_RANGE.fullmatch(content_range.strip()))
                    while count < sample_bytes and time.perf_counter() - started < maximum_seconds:
                        chunk = response.read(min(64 * 1024, sample_bytes - count))
                        if not chunk:
                            break
                        count += len(chunk)
                    if count == 0:
                        error = "source returned zero bytes"
            except (HTTPError, URLError, OSError, TimeoutError) as exc:
                error = str(exc)
            elapsed = max(time.perf_counter() - started, 0.000001)
            result = SourceBenchmark(source, count, elapsed, supports_resume, error)
            results.append(result)
            self._emit("source_benchmark", **result.public_dict())
        return sorted(
            results,
            key=lambda item: (
                item.error is None and item.bytes_read > 0,
                item.supports_resume,
                item.bytes_per_second,
            ),
            reverse=True,
        )

    def _source_order(self, source_name: str) -> tuple[list[DownloadSource], list[dict[str, Any]]]:
        if source_name != "auto":
            matches = [item for item in self.package.sources if item.name == source_name]
            if not matches:
                raise InvalidArgumentError(
                    f'Source "{source_name}" is not available for {self.package.name}.',
                    suggestion="Use --source auto or inspect `facut download --help`.",
                )
            return matches, []
        benchmarks = self.benchmark_sources()
        successful = [item for item in benchmarks if item.error is None and item.bytes_read > 0]
        resumable = [item for item in successful if item.supports_resume]
        usable = [item.source for item in (resumable or successful)]
        if not usable:
            raise DownloadError(
                f"No download source returned data for {self.package.display_name}.",
                suggestion="Check the proxy/network, then run the same command again; .part files are retained.",
                details={"benchmarks": [item.public_dict() for item in benchmarks]},
            )
        return usable, [item.public_dict() for item in benchmarks]

    def _download_one(self, item: ModelFile, sources: list[DownloadSource]) -> dict[str, Any]:
        target = self.destination / Path(item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        valid, reason = _verify(target, item, full_hash=True)
        if valid:
            self._emit("file_cached", file=item.path, bytes=item.size)
            return {"file": item.path, "status": "cached", "bytes": item.size}
        quarantined: list[str] = []
        if target.exists():
            quarantined.append(str(_quarantine(target)))

        partial = target.with_name(target.name + ".part")
        if partial.exists() and partial.stat().st_size > item.size:
            quarantined.append(str(_quarantine(partial)))
        errors: list[dict[str, str]] = []
        for source in sources:
            offset = partial.stat().st_size if partial.exists() else 0
            headers = {
                "User-Agent": "facut-model-downloader/1",
                "Accept-Encoding": "identity",
            }
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = Request(source.url(item.path), headers=headers)
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    status = getattr(response, "status", response.getcode())
                    if offset:
                        content_range = response.headers.get("Content-Range", "")
                        match = _CONTENT_RANGE.fullmatch(content_range.strip())
                        if status != 206 or match is None or int(match.group(1)) != offset:
                            raise DownloadError(
                                f"Source {source.name} does not support safe resume for {item.path}."
                            )
                    mode = "ab" if offset else "wb"
                    received = 0
                    with partial.open(mode) as stream:
                        while chunk := response.read(self.chunk_size):
                            stream.write(chunk)
                            received += len(chunk)
                            offset += len(chunk)
                            self._emit(
                                "download_progress",
                                file=item.path,
                                source=source.name,
                                completed_bytes=offset,
                                total_bytes=item.size,
                                progress=round(offset / item.size, 6),
                            )
                        stream.flush()
                        os.fsync(stream.fileno())
                    if received == 0 and offset == 0:
                        raise DownloadError(
                            f"Source {source.name} returned zero bytes for {item.path}."
                        )
                if partial.stat().st_size < item.size:
                    raise DownloadError(
                        f"Source {source.name} ended early for {item.path}: "
                        f"{partial.stat().st_size}/{item.size} bytes."
                    )
                if partial.stat().st_size > item.size:
                    raise DownloadError(
                        f"Source {source.name} exceeded the expected size for {item.path}."
                    )
                valid, reason = _verify(partial, item, full_hash=True)
                if not valid:
                    raise DownloadError(f"Integrity check failed for {item.path}: {reason}")
                os.replace(partial, target)
                self._emit("file_complete", file=item.path, source=source.name, bytes=item.size)
                return {
                    "file": item.path,
                    "status": "downloaded",
                    "source": source.name,
                    "bytes": item.size,
                    "quarantined": quarantined,
                }
            except (HTTPError, URLError, OSError, TimeoutError, DownloadError) as exc:
                errors.append({"source": source.name, "error": str(exc)})
                self._emit("source_failed", file=item.path, source=source.name, error=str(exc))
                continue
        raise DownloadError(
            f"Could not download {item.path}; the partial file was kept for resume.",
            suggestion="Run the same command again. FACUT will continue from the .part byte offset.",
            details={"file": item.path, "errors": errors},
        )

    def _complete_manifest(
        self,
        files: list[dict[str, Any]],
        *,
        benchmarks: list[dict[str, Any]],
        receipt_cached: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.package.name,
            "display_name": self.package.display_name,
            "license": self.package.license,
            "directory": str(self.destination),
            "total_bytes": self.package.total_size,
            "files": files,
            "benchmarks": benchmarks,
            "resumable": True,
            "verified": True,
            "receipt_cached": receipt_cached,
        }

    def download(self, *, source_name: str = "auto", force_verify: bool = False) -> dict[str, Any]:
        self.root.mkdir(parents=True, exist_ok=True)
        if not force_verify and self._receipt_valid():
            files = [
                {"file": item.path, "status": "cached", "bytes": item.size}
                for item in self.package.files
            ]
            manifest = self._complete_manifest(files, benchmarks=[], receipt_cached=True)
            self._emit("model_cached", **manifest)
            return manifest
        free = shutil.disk_usage(self.root).free
        already_present = sum(
            item.size
            for item in self.package.files
            if _verify(self.destination / item.path, item, full_hash=False)[0]
        )
        partial_present = sum(
            min((self.destination / item.path).with_name(Path(item.path).name + ".part").stat().st_size, item.size)
            for item in self.package.files
            if (self.destination / item.path).with_name(Path(item.path).name + ".part").exists()
        )
        remaining = max(0, self.package.total_size - already_present - partial_present)
        if free < remaining + 512 * 1024 * 1024:
            raise DownloadError(
                f"Not enough free space for {self.package.display_name}.",
                suggestion="Choose a larger drive with --directory or free disk space.",
                details={"free_bytes": free, "required_bytes": remaining},
            )
        complete = all(
            _verify(self.destination / item.path, item, full_hash=True)[0]
            for item in self.package.files
        )
        if complete:
            files = [
                {"file": item.path, "status": "cached", "bytes": item.size}
                for item in self.package.files
            ]
            benchmarks: list[dict[str, Any]] = []
        else:
            sources, benchmarks = self._source_order(source_name)
            files = [self._download_one(item, sources) for item in self.package.files]
        self._write_receipt()
        manifest = self._complete_manifest(files, benchmarks=benchmarks, receipt_cached=False)
        self._emit("model_complete", **manifest)
        return manifest
