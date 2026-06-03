from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
from urllib.error import URLError
from urllib.request import urlretrieve


CLASSIFICATION_HEAD_BASE_URL = "https://essentia.upf.edu/models/classification-heads"
FEATURE_EXTRACTOR_BASE_URL = "https://essentia.upf.edu/models/feature-extractors/discogs-effnet"

HEADS = (
    ("genre_discogs400", "genre_discogs400-discogs-effnet-1.pb"),
    ("mtg_jamendo_genre", "mtg_jamendo_genre-discogs-effnet-1.pb"),
    ("mtg_jamendo_moodtheme", "mtg_jamendo_moodtheme-discogs-effnet-1.pb"),
    ("mtg_jamendo_instrument", "mtg_jamendo_instrument-discogs-effnet-1.pb"),
    ("mtg_jamendo_top50tags", "mtg_jamendo_top50tags-discogs-effnet-1.pb"),
    ("danceability", "danceability-discogs-effnet-1.pb"),
    ("voice_instrumental", "voice_instrumental-discogs-effnet-1.pb"),
    ("tonal_atonal", "tonal_atonal-discogs-effnet-1.pb"),
    ("approachability", "approachability_2c-discogs-effnet-1.pb"),
    ("approachability", "approachability_3c-discogs-effnet-1.pb"),
    ("approachability", "approachability_regression-discogs-effnet-1.pb"),
    ("engagement", "engagement_2c-discogs-effnet-1.pb"),
    ("engagement", "engagement_3c-discogs-effnet-1.pb"),
    ("engagement", "engagement_regression-discogs-effnet-1.pb"),
    ("timbre", "timbre-discogs-effnet-1.pb"),
    ("nsynth_instrument", "nsynth_instrument-discogs-effnet-1.pb"),
    ("nsynth_acoustic_electronic", "nsynth_acoustic_electronic-discogs-effnet-1.pb"),
    ("nsynth_bright_dark", "nsynth_bright_dark-discogs-effnet-1.pb"),
    ("nsynth_reverb", "nsynth_reverb-discogs-effnet-1.pb"),
    ("mood_acoustic", "mood_acoustic-discogs-effnet-1.pb"),
    ("mood_aggressive", "mood_aggressive-discogs-effnet-1.pb"),
    ("mood_electronic", "mood_electronic-discogs-effnet-1.pb"),
    ("mood_happy", "mood_happy-discogs-effnet-1.pb"),
    ("mood_party", "mood_party-discogs-effnet-1.pb"),
    ("mood_relaxed", "mood_relaxed-discogs-effnet-1.pb"),
    ("mood_sad", "mood_sad-discogs-effnet-1.pb"),
    ("genre_electronic", "genre_electronic-discogs-effnet-1.pb"),
    ("genre_dortmund", "genre_dortmund-discogs-effnet-1.pb"),
    ("genre_rosamerica", "genre_rosamerica-discogs-effnet-1.pb"),
    ("genre_tzanetakis", "genre_tzanetakis-discogs-effnet-1.pb"),
    ("fma_small", "fma_small-discogs-effnet-1.pb"),
)


def required_files() -> list[tuple[str, str]]:
    files = [
        (
            "discogs-effnet-bs64-1.pb",
            f"{FEATURE_EXTRACTOR_BASE_URL}/discogs-effnet-bs64-1.pb",
        )
    ]
    for folder, filename in HEADS:
        files.append((filename, f"{CLASSIFICATION_HEAD_BASE_URL}/{folder}/{filename}"))
        metadata = filename.replace(".pb", ".json")
        files.append((metadata, f"{CLASSIFICATION_HEAD_BASE_URL}/{folder}/{metadata}"))
    return files


def validate_file(path: Path) -> None:
    if path.suffix == ".json":
        with path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object")
    elif path.stat().st_size <= 0:
        raise ValueError(f"{path} is empty")


def download_file(filename: str, url: str, output_dir: Path, retries: int) -> str:
    target = output_dir / filename
    if target.exists():
        validate_file(target)
        return "exists"

    partial = target.with_name(f"{target.name}.part")
    for attempt in range(1, retries + 1):
        try:
            if partial.exists():
                partial.unlink()
            urlretrieve(url, partial)
            validate_file(partial)
            shutil.move(str(partial), str(target))
            return "downloaded"
        except (OSError, URLError, ValueError) as exc:
            if attempt == retries:
                if partial.exists():
                    partial.unlink()
                raise RuntimeError(f"{filename}: {exc!r} url={url}") from exc
            time.sleep(min(2**attempt, 10))
    raise RuntimeError(f"{filename}: download failed url={url}")


def main_from_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download Discogs-EffNet head-pack model files into a local models directory."
    )
    parser.add_argument(
        "output_dir",
        nargs="?",
        default="models",
        help="Directory to place downloaded files into. Default: models",
    )
    parser.add_argument("--retries", type=int, default=3, help="Attempts per file. Default: 3")
    parser.add_argument("--dry-run", action="store_true", help="Print files without downloading")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)
    files = required_files()
    print(f"Discogs-EffNet head-pack files: {len(files)}")
    print(f"Output directory: {output_dir.resolve()}")

    if args.dry_run:
        for filename, url in files:
            print(f"{filename}\t{url}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    existing = 0
    for index, (filename, url) in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] {filename}")
        try:
            status = download_file(filename, url, output_dir, max(1, args.retries))
        except Exception as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        if status == "downloaded":
            downloaded += 1
        else:
            existing += 1
        print(f"  {status}")

    print(f"Done. Downloaded {downloaded}, already present {existing}.")
    return 0


def main() -> int:
    return main_from_args()


if __name__ == "__main__":
    raise SystemExit(main())
