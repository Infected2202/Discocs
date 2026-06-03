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
