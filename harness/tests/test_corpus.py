from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness import corpus


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_manifest(path: Path, clips: list[dict[str, str]]) -> None:
    path.write_text(json.dumps({"clips": clips}), encoding="utf-8")


def test_ensure_downloads_verifies_and_sorts_file_urls(tmp_path: Path) -> None:
    source_a = tmp_path / "source-a.y4m"
    source_b = tmp_path / "source-b.y4m"
    source_a.write_bytes(b"clip-a")
    source_b.write_bytes(b"clip-b")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "epoch": 1,
                "clips": [
                    {
                        "name": "b.y4m",
                        "url": source_b.as_uri(),
                        "sha256": _sha256(b"clip-b"),
                        "attribution": "metadata is ignored",
                    },
                    {
                        "name": "a.y4m",
                        "url": source_a.as_uri(),
                        "sha256": _sha256(b"clip-a"),
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    entries = corpus.ensure(manifest, tmp_path / "cache")

    assert [entry.name for entry in entries] == ["a.y4m", "b.y4m"]
    assert [entry.path.read_bytes() for entry in entries] == [b"clip-a", b"clip-b"]


def test_poisoned_cache_is_deleted_and_redownloaded(
    tmp_path: Path, monkeypatch
) -> None:
    source = tmp_path / "source.y4m"
    source.write_bytes(b"trusted-clip")
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "name": "clip.y4m",
                "url": source.as_uri(),
                "sha256": _sha256(b"trusted-clip"),
            }
        ],
    )
    cache = tmp_path / "cache"
    entry = corpus.ensure(manifest, cache)[0]
    entry.path.write_bytes(b"poison")
    calls: list[str] = []
    original = corpus._download_once

    def recording_download(url: str, destination: Path, timeout: float) -> None:
        calls.append(url)
        original(url, destination, timeout)

    monkeypatch.setattr(corpus, "_download_once", recording_download)

    healed = corpus.ensure(manifest, cache)

    assert calls == [source.as_uri()]
    assert healed[0].path.read_bytes() == b"trusted-clip"
    assert hashlib.sha256(healed[0].path.read_bytes()).hexdigest() == _sha256(
        b"trusted-clip"
    )


def test_valid_cached_file_does_not_download(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.y4m"
    source.write_bytes(b"trusted-clip")
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        [
            {
                "name": "clip.y4m",
                "url": source.as_uri(),
                "sha256": _sha256(b"trusted-clip"),
            }
        ],
    )
    cache = tmp_path / "cache"
    corpus.ensure(manifest, cache)

    def fail_download(*_args, **_kwargs) -> None:
        raise AssertionError("valid cache should not download")

    monkeypatch.setattr(corpus, "_download_once", fail_download)

    assert corpus.ensure(manifest, cache)[0].path.read_bytes() == b"trusted-clip"
