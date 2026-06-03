from scripts.benchmark_matrix import (
    format_matrix_summary,
    parse_preset,
    preset_pipeline,
    preset_workers,
    summarize_resources,
)


def test_parse_builtin_preset():
    name, env = parse_preset("tf4")

    assert name == "tf4"
    assert env["TF_NUM_INTRAOP_THREADS"] == "4"
    assert env["TF_NUM_INTEROP_THREADS"] == "1"
    assert env["OMP_NUM_THREADS"] == "4"


def test_parse_workers_preset():
    name, env = parse_preset("workers2")

    assert name == "workers2"
    assert env == {}
    assert preset_workers(name, default_workers=1) == 2


def test_parse_workers_tf_preset():
    name, env = parse_preset("workers4-tf8")

    assert name == "workers4-tf8"
    assert preset_workers(name, default_workers=1) == 4
    assert env["TF_NUM_INTRAOP_THREADS"] == "8"
    assert env["TF_NUM_INTEROP_THREADS"] == "1"
    assert env["OMP_NUM_THREADS"] == "8"


def test_parse_dynamic_workers_tf_preset():
    name, env = parse_preset("workers4-tf6")

    assert name == "workers4-tf6"
    assert preset_workers(name, default_workers=1) == 4
    assert env["TF_NUM_INTRAOP_THREADS"] == "6"
    assert env["TF_NUM_INTEROP_THREADS"] == "1"
    assert env["OMP_NUM_THREADS"] == "6"


def test_parse_dynamic_workers_only_preset():
    name, env = parse_preset("workers6")

    assert name == "workers6"
    assert preset_workers(name, default_workers=1) == 6
    assert env == {}


def test_parse_dynamic_workers_tf_ffmpeg_preset():
    name, env = parse_preset("workers6-tf3-ff1")

    assert name == "workers6-tf3-ff1"
    assert preset_workers(name, default_workers=1) == 6
    assert env["TF_NUM_INTRAOP_THREADS"] == "3"
    assert env["TF_NUM_INTEROP_THREADS"] == "1"
    assert env["OMP_NUM_THREADS"] == "3"
    assert env["DISCOCS_FFMPEG_THREADS"] == "1"


def test_prefetch_preset_selects_prefetch_pipeline():
    name, env = parse_preset("prefetch")

    assert name == "prefetch"
    assert env == {}
    assert preset_pipeline(name, default_pipeline="sequential") == "prefetch"


def test_parse_custom_preset():
    name, env = parse_preset("wide:TF_NUM_INTRAOP_THREADS=12,OMP_NUM_THREADS=12")

    assert name == "wide"
    assert env == {"TF_NUM_INTRAOP_THREADS": "12", "OMP_NUM_THREADS": "12"}


def test_summarize_resources_handles_samples():
    summary = summarize_resources(
        [
            {
                "process_cpu_percent": 50.0,
                "system_cpu_percent": 20.0,
                "rss_mb": 100.0,
                "threads": 8,
                "pids": 1,
            },
            {
                "process_cpu_percent": 75.0,
                "system_cpu_percent": 30.0,
                "rss_mb": 140.0,
                "threads": 10,
                "pids": 2,
            },
        ]
    )

    assert summary["samples"] == 2
    assert summary["process_cpu_avg"] == 62.5
    assert summary["process_cpu_max"] == 75.0
    assert summary["system_cpu_avg"] == 25.0
    assert summary["rss_mb_max"] == 140.0
    assert summary["threads_max"] == 10
    assert summary["pids_max"] == 2


def test_format_matrix_summary_orders_by_throughput():
    rendered = format_matrix_summary(
        [
            {
                "name": "slow",
                "benchmark": {
                    "tracks_per_min": 10.0,
                    "predict": {"avg": 4.0},
                    "warnings": 0,
                },
                "resources": {"process_cpu_avg": 20.0, "rss_mb_max": 100.0},
            },
            {
                "name": "fast",
                "benchmark": {
                    "tracks_per_min": 12.0,
                    "predict": {"avg": 3.0},
                    "warnings": 0,
                },
                "resources": {"process_cpu_avg": 40.0, "rss_mb_max": 120.0},
            },
        ]
    )

    assert rendered.splitlines()[1].startswith("- fast:")
    assert "12.00 tracks/min" in rendered
    assert "proc CPU avg 40.0%" in rendered
