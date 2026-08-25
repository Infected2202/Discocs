from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def parse_user_ids(value: str | list[int]) -> list[int]:
    if isinstance(value, list):
        return value
    return [int(part.strip()) for part in str(value).split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")

    bot_token: str = Field(default="", alias="BOT_TOKEN")
    allowed_telegram_user_ids: Annotated[list[int], NoDecode, BeforeValidator(parse_user_ids)] = Field(
        alias="ALLOWED_TELEGRAM_USER_IDS"
    )

    navidrome_base_url: str = Field(alias="NAVIDROME_BASE_URL")
    navidrome_username: str = Field(alias="NAVIDROME_USERNAME")
    navidrome_password: str = Field(alias="NAVIDROME_PASSWORD")
    navidrome_client_name: str = Field(default="discocs-bot", alias="NAVIDROME_CLIENT_NAME")
    navidrome_api_version: str = Field(default="1.16.1", alias="NAVIDROME_API_VERSION")

    discocs_base_url: str = Field(alias="DISCOCS_BASE_URL")
    discocs_count: int = Field(default=10, alias="DISCOCS_COUNT")
    # Machine-principal token for the discocs access gate. Only sent when set;
    # empty keeps current behaviour while the gate is off.
    discocs_service_token: str = Field(default="", alias="DISCOCS_SERVICE_TOKEN")

    sqlite_path: Path = Field(default=Path("data/bot.sqlite"), alias="SQLITE_PATH")
    temp_dir: Path = Field(default=Path("data/tmp"), alias="TEMP_DIR")

    # Скачивание по ссылке (YouTube/SoundCloud/…): кэш файлов, лимиты, куки.
    external_cache_dir: Path = Field(default=Path("data/cache/external"), alias="EXTERNAL_CACHE_DIR")
    external_cache_max_gb: float = Field(default=10.0, alias="EXTERNAL_CACHE_MAX_GB")
    external_max_parts: int = Field(default=4, alias="EXTERNAL_MAX_PARTS")
    external_max_duration_minutes: int = Field(default=180, alias="EXTERNAL_MAX_DURATION_MINUTES")
    external_max_download_mb: int = Field(default=500, alias="EXTERNAL_MAX_DOWNLOAD_MB")
    external_max_bitrate_kbps: int = Field(default=320, alias="EXTERNAL_MAX_BITRATE_KBPS")
    # Источники отдают 128-160k opus/aac. MP3 на том же битрейте звучит хуже
    # оригинала, поэтому запас настраивается — но по умолчанию его нет.
    external_bitrate_headroom: float = Field(default=1.0, alias="EXTERNAL_BITRATE_HEADROOM")
    ytdlp_cookies_file: str = Field(default="", alias="YTDLP_COOKIES_FILE")
    temp_max_age_hours: float = Field(default=6.0, alias="TEMP_MAX_AGE_HOURS")

    transcode_bitrate: str = Field(default="320k", alias="TRANSCODE_BITRATE")
    transcode_workers: int = Field(default=4, alias="TRANSCODE_WORKERS")
    transcode_fast: bool = Field(default=True, alias="TRANSCODE_FAST")
    navidrome_stream_max_bitrate: int = Field(default=320, alias="NAVIDROME_STREAM_MAX_BITRATE")
    prep_timing_log_events: bool = Field(default=True, alias="PREP_TIMING_LOG_EVENTS")
    max_telegram_audio_mb: int = Field(default=50, alias="MAX_TELEGRAM_AUDIO_MB")

    @property
    def max_telegram_audio_bytes(self) -> int:
        return self.max_telegram_audio_mb * 1024 * 1024

    @property
    def external_cache_max_bytes(self) -> int:
        return int(self.external_cache_max_gb * 1024 * 1024 * 1024)

    @property
    def external_max_download_bytes(self) -> int:
        return self.external_max_download_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
