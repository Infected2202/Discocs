from dataclasses import dataclass

DEFAULT_AUDIO_PROFILE = "mp3:320"

AUDIO_PROFILES: dict[str, dict[str, str | None]] = {
    "mp3:192": {"format": "mp3", "bitrate": "192k", "label": "MP3 192"},
    "mp3:256": {"format": "mp3", "bitrate": "256k", "label": "MP3 256"},
    "mp3:320": {"format": "mp3", "bitrate": "320k", "label": "MP3 320"},
    "opus:192": {"format": "opus", "bitrate": "192k", "label": "Opus 192"},
    "opus:256": {"format": "opus", "bitrate": "256k", "label": "Opus 256"},
    "flac": {"format": "flac", "bitrate": None, "label": "FLAC"},
}


def _parse_bitrate_kbps(value: str | None) -> int | None:
    if not value:
        return None
    digits = value.rstrip("kK")
    try:
        return int(digits)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class UserDeliveryPrefs:
    profile: str
    format: str
    bitrate: str | None
    label: str

    @property
    def as_document(self) -> bool:
        return self.format == "flac"

    @property
    def file_extension(self) -> str:
        if self.format == "flac":
            return "flac"
        if self.format == "opus":
            return "ogg"
        return "mp3"

    def cache_profile(self, *, with_cover: bool) -> str:
        suffix = "+cover-v2" if with_cover else ""
        if self.format == "flac":
            return f"flac{suffix}"
        if self.format == "opus":
            kbps = _parse_bitrate_kbps(self.bitrate)
            return f"opus{kbps}k{suffix}" if kbps else f"opus{suffix}"
        return f"{self.bitrate}{suffix}"


def normalize_audio_profile(value: str | None) -> str:
    if value and value in AUDIO_PROFILES:
        return value
    return DEFAULT_AUDIO_PROFILE


def delivery_prefs_from_profile(profile: str | None) -> UserDeliveryPrefs:
    key = normalize_audio_profile(profile)
    spec = AUDIO_PROFILES[key]
    return UserDeliveryPrefs(
        profile=key,
        format=str(spec["format"]),
        bitrate=spec["bitrate"] if spec["bitrate"] is None else str(spec["bitrate"]),
        label=str(spec["label"]),
    )
