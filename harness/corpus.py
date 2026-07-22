"""Corpus manifest validation, download, and content-address verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
import urllib.request


_DOWNLOAD_RETRIES = 3
_DOWNLOAD_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class ClipEntry:
    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class _ManifestClip:
    name: str
    url: str
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_manifest(path: Path) -> list[_ManifestClip]:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot load corpus manifest {path}: {exc}") from exc
    if not isinstance(raw, dict) or "clips" not in raw:
        raise RuntimeError("corpus manifest must be an object containing 'clips'")
    if not isinstance(raw["clips"], list):
        raise RuntimeError("corpus manifest 'clips' must be an array")

    clips: list[_ManifestClip] = []
    names: set[str] = set()
    for index, item in enumerate(raw["clips"]):
        required_fields = {"name", "url", "sha256"}
        if not isinstance(item, dict) or not required_fields.issubset(item):
            raise RuntimeError(
                f"corpus manifest clip {index} must contain name, url, and sha256"
            )
        name, url, expected = item["name"], item["url"], item["sha256"]
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or name in {".", ".."}
        ):
            raise RuntimeError(f"corpus manifest clip {index} has an invalid name")
        if name in names:
            raise RuntimeError(f"corpus manifest contains duplicate clip name {name!r}")
        if not isinstance(url, str) or not url:
            raise RuntimeError(f"corpus manifest clip {name!r} has an invalid URL")
        if (
            not isinstance(expected, str)
            or len(expected) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in expected)
        ):
            raise RuntimeError(f"corpus manifest clip {name!r} has an invalid sha256")
        names.add(name)
        clips.append(_ManifestClip(name, url, expected.lower()))
    return sorted(clips, key=lambda clip: clip.name)


def _download_once(url: str, destination: Path, timeout: float) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "videofast-harness/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        with destination.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)


def _download_verified(clip: _ManifestClip, destination: Path) -> None:
    partial = destination.with_name(f".{destination.name}.part")
    last_error: Exception | None = None
    # One initial attempt followed by the configured number of retries.
    for _attempt in range(_DOWNLOAD_RETRIES + 1):
        partial.unlink(missing_ok=True)
        try:
            _download_once(clip.url, partial, _DOWNLOAD_TIMEOUT_SECONDS)
            actual = _sha256(partial)
            if actual != clip.sha256:
                raise RuntimeError(
                    f"sha256 mismatch for {clip.name}: expected {clip.sha256}, got {actual}"
                )
            partial.replace(destination)
            return
        except Exception as exc:  # urllib raises several unrelated exception classes.
            last_error = exc
            partial.unlink(missing_ok=True)
    raise RuntimeError(
        f"failed to download corpus clip {clip.name!r} after "
        f"{_DOWNLOAD_RETRIES + 1} attempts: {last_error}"
    ) from last_error


def ensure(manifest_path: str | Path, cache_dir: str | Path) -> list[ClipEntry]:
    """Ensure all manifest clips are present and hash-verified in the cache."""

    manifest = Path(manifest_path)
    clips = _parse_manifest(manifest)
    corpus_dir = Path(cache_dir) / "corpus"
    corpus_dir.mkdir(parents=True, exist_ok=True)

    entries: list[ClipEntry] = []
    for clip in clips:
        destination = corpus_dir / clip.name
        if destination.exists():
            try:
                valid = destination.is_file() and _sha256(destination) == clip.sha256
            except OSError:
                valid = False
            if not valid:
                if destination.is_dir():
                    raise RuntimeError(
                        f"corpus cache path is a directory, expected a file: {destination}"
                    )
                destination.unlink(missing_ok=True)
        if not destination.exists():
            _download_verified(clip, destination)
        actual = _sha256(destination)
        if actual != clip.sha256:
            destination.unlink(missing_ok=True)
            raise RuntimeError(
                f"sha256 mismatch for cached clip {clip.name}: "
                f"expected {clip.sha256}, got {actual}"
            )
        entries.append(ClipEntry(clip.name, destination))
    return entries
