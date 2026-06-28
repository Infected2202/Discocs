from bot.storage.user_prefs import UserDeliveryPrefs

LOSSLESS_SUFFIXES = frozenset({"flac", "alac", "wav", "ape", "aiff", "wv"})
TELEGRAM_AUDIO_SUFFIXES = frozenset({"mp3", "m4a", "ogg", "oga"})
OPUS_CONTAINER_SUFFIXES = frozenset({"ogg", "oga", "opus"})


def normalize_suffix(suffix: str | None) -> str:
    return (suffix or "").lower().lstrip(".")


def parse_bitrate_kbps(value: str | None) -> int | None:
    if not value:
        return None
    digits = value.rstrip("kK")
    try:
        return int(digits)
    except ValueError:
        return None


def prefs_bitrate_kbps(prefs: UserDeliveryPrefs) -> int | None:
    return parse_bitrate_kbps(prefs.bitrate)


def is_lossless_suffix(suffix: str | None) -> bool:
    return normalize_suffix(suffix) in LOSSLESS_SUFFIXES


def send_as_document(suffix: str | None) -> bool:
    normalized = normalize_suffix(suffix)
    if normalized in TELEGRAM_AUDIO_SUFFIXES:
        return False
    return True


def source_cache_profile(
    suffix: str | None,
    bitrate_kbps: int | None,
    *,
    transcoded_with_cover: bool = False,
) -> str:
    cover = "+cover-v2" if transcoded_with_cover else ""
    normalized = normalize_suffix(suffix)
    if is_lossless_suffix(normalized):
        return f"source:{normalized}{cover}"
    if bitrate_kbps:
        return f"{bitrate_kbps}k{cover}"
    return f"source:{normalized or 'audio'}{cover}"


def is_opus_container_suffix(suffix: str | None) -> bool:
    return normalize_suffix(suffix) in OPUS_CONTAINER_SUFFIXES


def should_send_original(
    source_suffix: str | None,
    source_bitrate_kbps: int | None,
    prefs: UserDeliveryPrefs,
) -> bool:
    suffix = normalize_suffix(source_suffix)

    if prefs.format == "flac":
        if suffix == "flac":
            return True
        if is_lossless_suffix(suffix):
            return True
        return True

    if prefs.format == "opus":
        target_kbps = prefs_bitrate_kbps(prefs)
        if target_kbps is None:
            return False
        if not is_opus_container_suffix(suffix):
            return False
        if source_bitrate_kbps is None:
            return False
        return source_bitrate_kbps <= target_kbps

    target_kbps = prefs_bitrate_kbps(prefs)
    if target_kbps is None:
        return False

    if is_lossless_suffix(suffix):
        return False

    if source_bitrate_kbps is None:
        return False

    return source_bitrate_kbps <= target_kbps


def should_use_navidrome_transcode(
    source_suffix: str | None,
    source_bitrate_kbps: int | None,
    prefs: UserDeliveryPrefs,
    *,
    enabled: bool,
) -> bool:
    if not enabled or prefs.format != "mp3":
        return False

    suffix = normalize_suffix(source_suffix)
    if suffix == "mp3":
        return False
    if is_lossless_suffix(suffix):
        return True

    target_kbps = prefs_bitrate_kbps(prefs)
    if target_kbps is None:
        return False
    if source_bitrate_kbps is None:
        return False
    return source_bitrate_kbps > target_kbps
