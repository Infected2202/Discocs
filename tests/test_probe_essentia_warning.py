from pathlib import Path

from scripts.probe_essentia_warning import build_parser, format_summary, parse_probe_log


def test_parse_probe_log_finds_warning_capture_and_slowest_steps():
    log = """
audio=/music/a.flac
=== TRACK 1/2 /music/a.flac ===
>>> STEP START track 1 TensorflowPredictEffnetDiscogs call
<<< STEP END track 1 TensorflowPredictEffnetDiscogs call (3.125s)

--- captured stderr for track 2 TensorflowPredictEffnetDiscogs call ---
[ WARNING  ] No network created, or last created network has been deleted...
--- end captured stderr for track 2 TensorflowPredictEffnetDiscogs call ---

=== TRACK 2/2 /music/b.flac ===
>>> STEP START track 2 TensorflowPredictEffnetDiscogs call
<<< STEP END track 2 TensorflowPredictEffnetDiscogs call (4.250s)
""".strip()

    summary = parse_probe_log(log)

    assert len(summary["tracks"]) == 2
    assert summary["warning_captures"] == [
        {
            "label": "track 2 TensorflowPredictEffnetDiscogs call",
            "stderr": "[ WARNING  ] No network created, or last created network has been deleted...",
            "has_warning": True,
            "has_info": False,
        }
    ]
    assert summary["slowest"][0] == {
        "label": "track 2 TensorflowPredictEffnetDiscogs call",
        "seconds": 4.25,
    }


def test_format_summary_includes_warning_and_info_blocks():
    log = """
--- captured stderr for import essentia.standard.MonoLoader ---
[   INFO   ] MusicExtractorSVM: no classifier models were configured by default
--- end captured stderr for import essentia.standard.MonoLoader ---
--- captured stderr for predictor shared delete+gc ---
[ WARNING  ] No network created, or last created network has been deleted...
--- end captured stderr for predictor shared delete+gc ---
""".strip()

    rendered = format_summary(parse_probe_log(log))

    assert "warning captures: 1" in rendered
    assert "predictor shared delete+gc" in rendered
    assert "MusicExtractorSVM" in rendered


def test_summarize_log_mode_does_not_require_audio(tmp_path: Path):
    log_path = tmp_path / "probe.log"
    log_path.write_text("probe complete\n", encoding="utf-8")

    args = build_parser().parse_args(["--summarize-log", str(log_path)])

    assert args.audio is None
    assert args.summarize_log == log_path
