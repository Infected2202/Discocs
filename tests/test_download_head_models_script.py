from scripts import download_head_models


def test_download_head_models_script_lists_head_pack_files():
    files = dict(download_head_models.required_files())

    assert len(files) == 63
    assert "moods_mirex-discogs-effnet-1.pb" not in files
    assert (
        files["discogs-effnet-bs64-1.pb"]
        == "https://essentia.upf.edu/models/feature-extractors/discogs-effnet/discogs-effnet-bs64-1.pb"
    )
    assert (
        files["genre_discogs400-discogs-effnet-1.pb"]
        == "https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.pb"
    )
    assert (
        files["genre_discogs400-discogs-effnet-1.json"]
        == "https://essentia.upf.edu/models/classification-heads/genre_discogs400/genre_discogs400-discogs-effnet-1.json"
    )


def test_download_head_models_script_dry_run(capsys):
    result = download_head_models.main_from_args(["--dry-run", "models"])

    assert result == 0
    output = capsys.readouterr().out
    assert "Discogs-EffNet head-pack files: 63" in output
    assert "discogs-effnet-bs64-1.pb" in output
