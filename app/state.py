"""Global application state — locks, caches, runtime variables.

Extracted from main.py so that services and API modules can share state
without importing the entire FastAPI application.

Note: JOBS values are typed as `object` to avoid a circular import with
main.py where JobStatus is defined. Consumers that need the full type
should import JobStatus from main.py directly.
"""
from __future__ import annotations

import os
from concurrent.futures import ProcessPoolExecutor
from threading import Event, Lock
from typing import Callable

from app.embedder import MuqMulanEmbedder

# ---------------------------------------------------------------------------
# Maintenance loop signal
# ---------------------------------------------------------------------------

MAINTENANCE_STOP = Event()

# ---------------------------------------------------------------------------
# In-process job registry
# ---------------------------------------------------------------------------

JOBS_LOCK = Lock()
JOBS: dict[str, object] = {}          # values are JobStatus instances (main.py)

DEFERRED_JOBS_LOCK = Lock()
DEFERRED_JOB_ORDER: list[str] = []
DEFERRED_JOB_STARTERS: dict[str, Callable[[], None]] = {}

# ---------------------------------------------------------------------------
# Analysis worker pool
# ---------------------------------------------------------------------------

ANALYZE_EXECUTORS_LOCK = Lock()
ANALYZE_EXECUTORS: set[ProcessPoolExecutor] = set()
SHUTDOWN_REQUESTED = False

# Worker / analysis tunables
MAX_ANALYZE_WORKERS = max(1, os.cpu_count() or 1)
DEFAULT_ANALYZE_WORKERS = min(4, MAX_ANALYZE_WORKERS)
MAX_ANALYZE_TF_THREADS = MAX_ANALYZE_WORKERS
DEFAULT_ANALYZE_TF_THREADS = min(4, MAX_ANALYZE_TF_THREADS)
MAX_AUDIO_FEATURE_WORKERS = max(32, MAX_ANALYZE_WORKERS)
DEFAULT_AUDIO_FEATURE_WORKERS = min(8, MAX_AUDIO_FEATURE_WORKERS)
MAX_MIX_SEEDS = 50

# ---------------------------------------------------------------------------
# Worker heartbeat / TTL constants
# ---------------------------------------------------------------------------

COVER_TIMEOUT_SECONDS = 5
WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS = 60
WORKER_CONNECTED_TTL_SECONDS = WORKER_HEARTBEAT_WRITE_INTERVAL_SECONDS * 3

# ---------------------------------------------------------------------------
# Cover image cache
# ---------------------------------------------------------------------------

COVER_CACHE_TTL_SECONDS = 3600
COVER_ERROR_CACHE_TTL_SECONDS = 300
COVER_CACHE_MAX_ITEMS = 512

COVER_CACHE_LOCK = Lock()
COVER_CACHE: dict[tuple[str, int], tuple[float, bytes, str]] = {}
COVER_ERROR_CACHE: dict[tuple[str, int], tuple[float, str]] = {}

# ---------------------------------------------------------------------------
# Stats cache
# ---------------------------------------------------------------------------

STATS_CACHE_TTL_SECONDS = 10
STATS_CACHE_LOCK = Lock()
STATS_CACHE: dict[str, tuple[float, dict[str, object]]] = {}

# ---------------------------------------------------------------------------
# Auto-index tracking
# ---------------------------------------------------------------------------

AUTO_INDEX_LOCK = Lock()
AUTO_INDEX_ANALYSIS_JOBS: set[str] = set()

# ---------------------------------------------------------------------------
# Text search embedder (lazy-loaded singleton)
# ---------------------------------------------------------------------------

TEXT_SEARCH_EMBEDDER_LOCK = Lock()
TEXT_SEARCH_EMBEDDER: MuqMulanEmbedder | None = None

# ---------------------------------------------------------------------------
# Mix generation lock
# ---------------------------------------------------------------------------

MIX_GENERATION_LOCK = Lock()

# ---------------------------------------------------------------------------
# Misc constants
# ---------------------------------------------------------------------------

ACTIVE_JOB_STATUSES = {"queued", "running"}
UI_BUILD_ID = "mix-cover-mosaic-20260624-2215"
