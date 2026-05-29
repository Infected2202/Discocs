from __future__ import annotations

import os
from pathlib import Path
import tempfile


os.environ.setdefault(
    "DISCOCS_LOG_DIR",
    str(Path(tempfile.gettempdir()) / "discocs-test-logs" / str(os.getpid())),
)
