"""Atomic publication and validation for versioned timeline artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from app.timeline.codec import TimelineFormatError, manifest_json_bytes, validate_timeline


def artifact_paths(root: Path, track_id: int, extractor: str) -> tuple[Path, Path]:
    directory = root / str(track_id) / extractor
    return directory / "manifest.json", directory / "payload.bin"


def publish_artifact(store, root: Path, manifest: dict[str, object], payload: bytes) -> None:
    validate_timeline(manifest, payload)
    manifest_path, payload_path = artifact_paths(root, int(manifest["track_id"]), str(manifest["extractor"]))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    manifest_tmp = manifest_path.with_name(f".{manifest_path.name}.{token}.tmp")
    payload_tmp = payload_path.with_name(f".{payload_path.name}.{token}.tmp")
    try:
        payload_tmp.write_bytes(payload)
        manifest_tmp.write_bytes(manifest_json_bytes(manifest))
        os.replace(payload_tmp, payload_path)
        os.replace(manifest_tmp, manifest_path)
        source = manifest["source"]
        payload_meta = manifest["payload"]
        store.upsert_timeline_artifact({
            "track_id": manifest["track_id"], "pack_name": manifest["pack_name"],
            "extractor": manifest["extractor"], "schema_version": manifest["schema_version"],
            "source_path": source["path"], "source_mtime": source["mtime"],
            "source_file_size": source["file_size"], "manifest_path": str(manifest_path),
            "payload_path": str(payload_path), "payload_bytes": len(payload),
            "checksum_sha256": payload_meta["sha256"],
        })
    finally:
        manifest_tmp.unlink(missing_ok=True)
        payload_tmp.unlink(missing_ok=True)


def load_valid_artifact(store, root: Path, track, pack_name: str, extractor: str):
    row = store.get_timeline_artifact(track.id, pack_name, extractor)
    if row is None:
        return None
    if (row["source_path"], float(row["source_mtime"]), int(row["source_file_size"])) != (
        track.path, float(track.mtime), track.file_size,
    ):
        raise TimelineFormatError("source identity is stale")
    try:
        manifest_path = _inside(root, Path(row["manifest_path"]))
        payload_path = _inside(root, Path(row["payload_path"]))
    except ValueError as exc:
        raise TimelineFormatError("artifact path is outside configured root") from exc
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = payload_path.read_bytes()
    except (OSError, json.JSONDecodeError) as exc:
        raise TimelineFormatError("artifact files are missing or corrupt") from exc
    validate_timeline(manifest, payload)
    if len(payload) != row["payload_bytes"] or hashlib.sha256(payload).hexdigest() != row["checksum_sha256"]:
        raise TimelineFormatError("artifact metadata does not match payload")
    return manifest, payload, manifest_path, payload_path


def cleanup_artifact(store, root: Path, track_id: int, pack_name: str, extractor: str) -> bool:
    row = store.get_timeline_artifact(track_id, pack_name, extractor)
    if row is None:
        return False
    manifest_path = _inside(root, Path(row["manifest_path"]))
    payload_path = _inside(root, Path(row["payload_path"]))
    store.delete_timeline_artifact(track_id, pack_name, extractor)
    for path in (manifest_path, payload_path):
        path.unlink(missing_ok=True)
    directory = manifest_path.parent
    try:
        directory.rmdir()
    except OSError:
        pass
    return True


def cleanup_orphan_artifacts(store, root: Path) -> int:
    """Remove only known artifact filenames that have no owning DB record."""
    if not root.exists():
        return 0
    referenced = {str(Path(path).resolve()) for path in store.timeline_artifact_file_paths()}
    removed = 0
    for name in ("manifest.json", "payload.bin"):
        for candidate in root.glob(f"*/*/{name}"):
            removed += _cleanup_orphan_file(root, candidate, referenced)
    return removed


def _cleanup_orphan_file(root: Path, candidate: Path, referenced: set[str]) -> int:
    resolved = _inside(root, candidate)
    if str(resolved) in referenced:
        return 0
    resolved.unlink(missing_ok=True)
    for directory in (resolved.parent, resolved.parent.parent):
        try:
            directory.rmdir()
        except OSError:
            break
    return 1


def _inside(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ValueError("timeline artifact path escapes configured root")
    return resolved
