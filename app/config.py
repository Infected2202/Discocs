from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


MODEL_FILES = {
    "discogs_effnet": "discogs-effnet-bs64-1.pb",
    "discogs_multi": "discogs_multi_embeddings-effnet-bs64-1.pb",
    "discogs_track": "discogs_track_embeddings-effnet-bs64-1.pb",
    "discogs_release": "discogs_release_embeddings-effnet-bs64-1.pb",
    "discogs_label": "discogs_label_embeddings-effnet-bs64-1.pb",
}

_DEFAULT_MODEL_OUTPUT = "PartitionedCall:1"
MODEL_OUTPUTS = {
    "discogs_effnet": _DEFAULT_MODEL_OUTPUT,
    "discogs_multi": _DEFAULT_MODEL_OUTPUT,
    "discogs_track": _DEFAULT_MODEL_OUTPUT,
    "discogs_release": _DEFAULT_MODEL_OUTPUT,
    "discogs_label": _DEFAULT_MODEL_OUTPUT,
}

DISCOGS_EFFNET_MODEL = "discogs_effnet"
MUQ_MULAN_MODEL = "muq_mulan"


@dataclass(frozen=True)
class NavidromeSettings:
    url: str = ""
    user: str = ""
    password: str = ""
    auth_mode: str = "token"
    timeout_seconds: int = 60
    download_mode: str = "download"
    temp_dir: Path = Path("data/tmp/navidrome")
    # Lightweight periodic refresh of play_count/last_played_at from recently
    # played albums. 0 disables the background refresh (full sync only).
    play_state_refresh_seconds: int = 60
    play_state_refresh_albums: int = 25

    @classmethod
    def from_env(cls, data_dir: Path) -> "NavidromeSettings":
        saved = load_runtime_settings(data_dir).get("navidrome", {})
        return cls(
            url=os.getenv("DISCOCS_NAVIDROME_URL", str(saved.get("url", ""))),
            user=os.getenv("DISCOCS_NAVIDROME_USER", str(saved.get("user", ""))),
            password=os.getenv("DISCOCS_NAVIDROME_PASSWORD", str(saved.get("password", ""))),
            auth_mode=os.getenv(
                "DISCOCS_NAVIDROME_AUTH_MODE",
                str(saved.get("auth_mode", "token")),
            ),
            timeout_seconds=_positive_int(
                os.getenv("DISCOCS_NAVIDROME_TIMEOUT_SECONDS"),
                _positive_int(saved.get("timeout_seconds"), 60),
            ),
            download_mode=os.getenv(
                "DISCOCS_NAVIDROME_DOWNLOAD_MODE",
                str(saved.get("download_mode", "download")),
            ),
            temp_dir=Path(
                os.getenv(
                    "DISCOCS_NAVIDROME_TEMP_DIR",
                    str(saved.get("temp_dir", data_dir / "tmp" / "navidrome")),
                )
            ),
            play_state_refresh_seconds=_non_negative_int(
                os.getenv("DISCOCS_NAVIDROME_PLAY_STATE_REFRESH_SECONDS"),
                _non_negative_int(saved.get("play_state_refresh_seconds"), 60),
            ),
            play_state_refresh_albums=_positive_int(
                os.getenv("DISCOCS_NAVIDROME_PLAY_STATE_REFRESH_ALBUMS"),
                _positive_int(saved.get("play_state_refresh_albums"), 25),
            ),
        )


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    model_dir: Path
    index_dir: Path
    default_model: str = "discogs_multi"
    navidrome: NavidromeSettings = field(default_factory=NavidromeSettings)

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
            navidrome=NavidromeSettings.from_env(data_dir),
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


def _positive_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _non_negative_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except (ValueError, TypeError):
        return default
    return parsed if parsed >= 0 else default


def runtime_settings_path(data_dir: Path) -> Path:
    return data_dir / "settings.json"


def load_runtime_settings(data_dir: Path) -> dict[str, object]:
    path = runtime_settings_path(data_dir)
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_runtime_settings(data_dir: Path, settings: dict[str, object]) -> None:
    path = runtime_settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(settings, indent=2, sort_keys=True),
        encoding="utf-8",
    )
