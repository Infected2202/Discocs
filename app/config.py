from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


MODEL_FILES = {
    "discogs_effnet": "discogs-effnet-bs64-1.pb",
    "discogs_multi": "discogs_multi_embeddings-effnet-bs64-1.pb",
    "discogs_track": "discogs_track_embeddings-effnet-bs64-1.pb",
    "discogs_label": "discogs_label_embeddings-effnet-bs64-1.pb",
}

MODEL_OUTPUTS = {
    "discogs_effnet": "PartitionedCall:1",
    "discogs_multi": "PartitionedCall:1",
    "discogs_track": "PartitionedCall:1",
    "discogs_label": "PartitionedCall:1",
}

DISCOGS_EFFNET_MODEL = "discogs_effnet"


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    model_dir: Path
    index_dir: Path
    default_model: str = "discogs_multi"

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("DISCOCS_DATA_DIR", "data"))
        model_dir = Path(os.getenv("DISCOCS_MODEL_DIR", "models"))
        index_dir = Path(os.getenv("DISCOCS_INDEX_DIR", str(data_dir)))
        db_path = Path(os.getenv("DISCOCS_DB_PATH", str(data_dir / "app.db")))
        default_model = os.getenv("DISCOCS_DEFAULT_MODEL", "discogs_multi")
        return cls(
            data_dir=data_dir,
            db_path=db_path,
            model_dir=model_dir,
            index_dir=index_dir,
            default_model=default_model,
        )

    def model_path(self, model_name: str | None = None) -> Path:
        model_key = model_name or self.default_model
        try:
            filename = MODEL_FILES[model_key]
        except KeyError as exc:
            known = ", ".join(sorted(MODEL_FILES))
            raise ValueError(f"Unknown model '{model_key}'. Known models: {known}") from exc
        return self.model_dir / filename

    def index_path(self, model_name: str | None = None) -> Path:
        model_key = model_name or self.default_model
        return self.index_dir / f"index_{model_key}_hnsw.bin"
