from pathlib import Path

from app.metadata import AudioMetadata
import app.scanner as scanner


def test_scan_music_folder_includes_genre_and_year(tmp_path: Path, monkeypatch):
    audio_path = tmp_path / "track.flac"
    audio_path.write_bytes(b"fake")

    def fake_metadata(path: Path) -> AudioMetadata:
        return AudioMetadata(
            artist="Artist",
            title="Title",
            album="Album",
            genre="Techno",
            year=1998,
            duration=123.0,
        )

    monkeypatch.setattr(scanner, "read_audio_metadata", fake_metadata)

    scanned = list(scanner.scan_music_folder(tmp_path))

    assert len(scanned) == 1
    assert scanned[0].genre == "Techno"
    assert scanned[0].year == 1998


def test_iter_audio_files_includes_wma_and_ape(tmp_path: Path):
    wma_path = tmp_path / "track.WMA"
    ape_path = tmp_path / "album" / "track.ape"
    text_path = tmp_path / "notes.txt"
    ape_path.parent.mkdir()
    wma_path.write_bytes(b"fake")
    ape_path.write_bytes(b"fake")
    text_path.write_text("not audio", encoding="utf-8")

    paths = {path.relative_to(tmp_path).as_posix() for path in scanner.iter_audio_files(tmp_path)}

    assert paths == {"track.WMA", "album/track.ape"}
