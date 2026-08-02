"""Offline GPX route parsing and reproducible route-animation rendering."""

from __future__ import annotations

import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw


def _haversine(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371000 * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def parse_gpx(source: str | Path) -> dict[str, Any]:
    path = Path(source).expanduser().resolve()
    root = ET.parse(path).getroot()
    namespace = root.tag.split("}")[0].removeprefix("{") if "}" in root.tag else ""
    prefix = f"{{{namespace}}}" if namespace else ""
    points: list[dict[str, Any]] = []
    for point in root.iter(f"{prefix}trkpt"):
        lat, lon = float(point.attrib["lat"]), float(point.attrib["lon"])
        elevation_node = point.find(f"{prefix}ele")
        time_node = point.find(f"{prefix}time")
        points.append(
            {
                "lat": lat,
                "lon": lon,
                "elevation": float(elevation_node.text) if elevation_node is not None and elevation_node.text else None,
                "time": time_node.text if time_node is not None else None,
            }
        )
    if len(points) < 2:
        raise ValueError("GPX requires at least two track points.")
    distance = sum(
        _haversine((a["lat"], a["lon"]), (b["lat"], b["lon"]))
        for a, b in zip(points, points[1:])
    )
    times = [datetime.fromisoformat(item["time"].replace("Z", "+00:00")) for item in points if item["time"]]
    elapsed = (times[-1] - times[0]).total_seconds() if len(times) >= 2 else None
    elevations = [item["elevation"] for item in points if item["elevation"] is not None]
    return {
        "source": str(path),
        "schema_namespace": namespace,
        "point_count": len(points),
        "distance_meters": round(distance, 2),
        "elapsed_seconds": elapsed,
        "elevation_gain_meters": round(
            sum(max(0.0, b - a) for a, b in zip(elevations, elevations[1:])), 2
        )
        if len(elevations) >= 2
        else None,
        "bounds": {
            "south": min(item["lat"] for item in points),
            "north": max(item["lat"] for item in points),
            "west": min(item["lon"] for item in points),
            "east": max(item["lon"] for item in points),
        },
        "points": points,
    }


def _screen_points(route: dict[str, Any], width: int, height: int) -> list[tuple[int, int]]:
    bounds = route["bounds"]
    margin = max(32, round(min(width, height) * 0.08))
    lon_span = max(1e-9, bounds["east"] - bounds["west"])
    lat_span = max(1e-9, bounds["north"] - bounds["south"])
    return [
        (
            round(margin + (item["lon"] - bounds["west"]) / lon_span * (width - 2 * margin)),
            round(height - margin - (item["lat"] - bounds["south"]) / lat_span * (height - 2 * margin)),
        )
        for item in route["points"]
    ]


def render_route_video(
    route: dict[str, Any],
    output: str | Path,
    *,
    ffmpeg: str = "ffmpeg",
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    duration: float = 8.0,
    encoder: str = "libx264",
    overwrite: bool = False,
) -> dict[str, Any]:
    destination = Path(output).expanduser().resolve()
    if destination.exists() and not overwrite:
        raise FileExistsError(f'Output "{destination}" already exists; use --overwrite.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, round(duration * fps))
    positions = _screen_points(route, width, height)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "-",
        "-an",
        "-c:v",
        encoder,
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-y" if overwrite else "-n",
        str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdin is not None
    try:
        for frame_index in range(frames):
            image = Image.new("RGB", (width, height), "#07141f")
            draw = ImageDraw.Draw(image)
            spacing = max(80, min(width, height) // 8)
            for x in range(0, width, spacing):
                draw.line((x, 0, x, height), fill="#102b3a", width=1)
            for y in range(0, height, spacing):
                draw.line((0, y, width, y), fill="#102b3a", width=1)
            reveal = max(2, round((frame_index + 1) / frames * len(positions)))
            visible = positions[:reveal]
            draw.line(positions, fill="#23475a", width=max(3, width // 400), joint="curve")
            draw.line(visible, fill="#34d6ff", width=max(6, width // 220), joint="curve")
            radius = max(8, width // 160)
            x, y = visible[-1]
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill="#ffb84d")
            distance = route["distance_meters"] / 1000
            draw.text((width * 0.06, height * 0.055), f"ROUTE  {distance:.1f} KM", fill="white")
            process.stdin.write(image.tobytes())
        process.stdin.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        code = process.wait()
        if code:
            raise RuntimeError(f"FFmpeg route rendering failed: {stderr.strip()}")
    except BrokenPipeError as error:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        process.wait()
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"FFmpeg route rendering failed: {stderr.strip()}") from error
    except Exception:
        process.kill()
        destination.unlink(missing_ok=True)
        raise
    return {
        "output": str(destination),
        "duration": frames / fps,
        "width": width,
        "height": height,
        "fps": fps,
        "encoder": encoder,
        "distance_meters": route["distance_meters"],
        "style": "facut-route-dark-v1",
        "warnings": ["Offline route graphics do not include copyrighted online map tiles."],
    }
