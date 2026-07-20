"""Waveform analysis job runner."""
from __future__ import annotations

import logging

from app.audio_source import track_audio_path
from app.timeline.artifacts import cleanup_artifact, cleanup_orphan_artifacts, publish_artifact
from app.timeline.codec import EXTRACTOR, PACK_NAME
from app.timeline.extractor import extract_waveform

logger = logging.getLogger(__name__)


def run_timeline_job(store, settings, tracks, *, job_id: str, reset: bool = False) -> None:
    root = settings.data_dir / "timeline"
    cleanup_orphan_artifacts(store, root)
    done = failed = 0
    for track in tracks:
        store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "running", job_id=job_id)
        try:
            if reset:
                cleanup_artifact(store, root, track.id, PACK_NAME, EXTRACTOR)
            with track_audio_path(store, settings, track) as audio_path:
                manifest, payload = extract_waveform(
                    audio_path, track_id=track.id, duration=float(track.duration or 0),
                    source={"path": track.path, "mtime": track.mtime, "file_size": track.file_size},
                )
            publish_artifact(store, root, manifest, payload)
            store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "ready", job_id=job_id)
            done += 1
        except Exception as exc:
            failed += 1
            store.set_timeline_analysis_status(track.id, PACK_NAME, EXTRACTOR, "failed", error=str(exc), job_id=job_id)
            logger.exception("Timeline analysis failed track_id=%s", track.id)
        store.update_progress_job(job_id, done=done, failed=failed, message=f"Waveforms ready {done}, failed {failed}")
    status = "completed" if failed == 0 else "failed"
    store.update_progress_job(job_id, done=done, failed=failed, status=status, message=f"Waveforms ready {done}, failed {failed}", finished=True)
