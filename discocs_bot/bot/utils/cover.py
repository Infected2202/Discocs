import base64
from pathlib import Path

_MINIMAL_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP/bAEMCAQEBAQECAQECAwICAgMEAwMDAwQF"
    "BAQEBAQFBgUFBQUFBQYGBgYGBgYGBgICAgICAgICAwMDAwMDAwMDAwMDAwMDAwMD"
    "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA//bAEMCAQEBAgECAgICAgICAgMC"
    "AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAw"
    "MDAwMDAwMDAwMDAwMDA//AABEIAAEAAQMBIgACEQEDEQH/xAAVAAEBAAAAAAAAAAA"
    "AAAAACv/EABQQAQAAAAAAAAAAAAAAAAAAAAD/xAAUAQEAAAAAAAAAAAAAAAAAAAAA"
    "/9oADAMBAAIRAxEAPwCwABmX/9k="
)

_PLACEHOLDER_PATH = Path(__file__).resolve().parent.parent / "assets" / "no_cover.jpg"


def placeholder_cover_path() -> Path:
    if not _PLACEHOLDER_PATH.exists():
        _PLACEHOLDER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PLACEHOLDER_PATH.write_bytes(_MINIMAL_JPEG)
    return _PLACEHOLDER_PATH


async def download_track_cover(
    track,
    navidrome,
    temp_dir: Path,
) -> Path:
    if track.cover_art_id:
        cover_path = temp_dir / f"card_{track.id}.cover.jpg"
        if await navidrome.download_cover_art(track.cover_art_id, cover_path, size=None):
            return cover_path
    return placeholder_cover_path()
