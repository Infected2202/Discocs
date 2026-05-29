from __future__ import annotations

import argparse
import gc
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO


CAPTURE_START_RE = re.compile(r"^--- captured stderr for (.+) ---$")
CAPTURE_END_RE = re.compile(r"^--- end captured stderr for (.+) ---$")
STEP_END_RE = re.compile(r"^<<< STEP END (.+) \(([0-9.]+)s\)$")
TRACK_RE = re.compile(r"^=== TRACK (\d+)/(\d+) (.+) ===$")
LOG_FILE: TextIO | None = None
QUIET_STDOUT = False


def emit(message: str = "", *, force: bool = False) -> None:
    if LOG_FILE is not None:
        print(message, file=LOG_FILE, flush=True)
    if force or not QUIET_STDOUT:
        print(message, flush=True)


@contextmanager
def capture_stderr(label: str, enabled: bool = True):
    if not enabled:
        yield
        return

    sys.stderr.flush()
    original_fd = os.dup(2)
    with tempfile.TemporaryFile(mode="w+b") as captured:
        os.dup2(captured.fileno(), 2)
        try:
            yield
        finally:
            sys.stderr.flush()
            os.dup2(original_fd, 2)
            os.close(original_fd)
            captured.seek(0)
            data = captured.read().decode(errors="replace").strip()
            if data:
                emit()
                emit(f"--- captured stderr for {label} ---")
                emit(data)
                emit(f"--- end captured stderr for {label} ---")
                emit()


def step(label: str, func, *, capture: bool = True):
    emit(f">>> STEP START {label}")
    started = time.perf_counter()
    with capture_stderr(label, capture):
        result = func()
    elapsed = time.perf_counter() - started
    emit(f"<<< STEP END {label} ({elapsed:.3f}s)")
    return result


def delete_and_collect(name: str, namespace: dict[str, object]) -> int:
    namespace.pop(name, None)
    return gc.collect()


def parse_probe_log(text: str) -> dict[str, object]:
    captures: list[dict[str, object]] = []
    durations: list[dict[str, object]] = []
    tracks: list[dict[str, object]] = []
    current_capture: dict[str, object] | None = None

    for line in text.splitlines():
        track_match = TRACK_RE.match(line)
        if track_match:
            tracks.append(
                {
                    "index": int(track_match.group(1)),
                    "total": int(track_match.group(2)),
                    "path": track_match.group(3),
                }
            )
            continue

        step_match = STEP_END_RE.match(line)
        if step_match:
            durations.append({"label": step_match.group(1), "seconds": float(step_match.group(2))})
            continue

        capture_start = CAPTURE_START_RE.match(line)
        if capture_start:
            current_capture = {"label": capture_start.group(1), "stderr_lines": []}
            continue

        capture_end = CAPTURE_END_RE.match(line)
        if capture_end and current_capture is not None:
            stderr = "\n".join(current_capture.pop("stderr_lines")).strip()
            current_capture["stderr"] = stderr
            current_capture["has_warning"] = "[ WARNING" in stderr
            current_capture["has_info"] = "[   INFO" in stderr
            captures.append(current_capture)
            current_capture = None
            continue

        if current_capture is not None:
            current_capture["stderr_lines"].append(line)

    warning_captures = [capture for capture in captures if capture["has_warning"]]
    info_captures = [capture for capture in captures if capture["has_info"]]
    slowest = sorted(durations, key=lambda item: item["seconds"], reverse=True)[:8]
    return {
        "tracks": tracks,
        "captures": captures,
        "warning_captures": warning_captures,
        "info_captures": info_captures,
        "durations": durations,
        "slowest": slowest,
    }


def format_summary(summary: dict[str, object]) -> str:
    tracks = summary["tracks"]
    captures = summary["captures"]
    warning_captures = summary["warning_captures"]
    info_captures = summary["info_captures"]
    slowest = summary["slowest"]
    lines = [
        "Probe summary",
        f"- tracks seen: {len(tracks)}",
        f"- stderr captures: {len(captures)}",
        f"- warning captures: {len(warning_captures)}",
    ]

    if warning_captures:
        lines.extend(["", "Warnings:"])
        for capture in warning_captures:
            stderr = str(capture["stderr"])
            first_line = stderr.splitlines()[0] if stderr else ""
            lines.append(f"- {capture['label']}: {first_line}")

    if info_captures:
        lines.extend(["", "Info stderr blocks:"])
        for capture in info_captures[:10]:
            stderr = str(capture["stderr"])
            first_line = stderr.splitlines()[0] if stderr else ""
            lines.append(f"- {capture['label']}: {first_line}")
        if len(info_captures) > 10:
            lines.append(f"- ... {len(info_captures) - 10} more")

    if slowest:
        lines.extend(["", "Slowest steps:"])
        for item in slowest:
            lines.append(f"- {item['seconds']:.3f}s {item['label']}")

    return "\n".join(lines)


def summarize_log_file(path: Path) -> int:
    print(format_summary(parse_probe_log(path.read_text(encoding="utf-8", errors="replace"))))
    return 0


def load_audio_paths(audio: Path, repeat: int, audio_list: Path | None) -> list[Path]:
    if audio_list is None:
        return [audio.resolve()] * repeat
    return [
        Path(line.strip()).resolve()
        for line in audio_list.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def run_probe(args: argparse.Namespace) -> int:
    capture = not args.no_capture
    audio_path = args.audio.resolve()
    audio_paths = load_audio_paths(args.audio, args.repeat, args.audio_list)

    emit(f"audio={audio_path}")
    emit(f"audio_count={len(audio_paths)}")
    emit(f"model={args.model}")
    emit(f"loader={args.loader}")
    emit(f"new_predictor_per_track={args.new_predictor_per_track}")
    emit(f"capture_stderr={capture}")

    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

    settings_cls = step(
        "import app.config.Settings",
        lambda: __import__("app.config", fromlist=["Settings"]).Settings,
        capture=capture,
    )
    settings = step("Settings.from_env", settings_cls.from_env, capture=capture)
    model_path = settings.model_path(args.model)
    emit(f"resolved_model_path={model_path}")
    emit(f"model_exists={model_path.exists()}")

    embedder_mod = step(
        "import app.embedder",
        lambda: __import__("app.embedder", fromlist=["DiscogsEffnetEmbedder"]),
        capture=capture,
    )

    mono_loader_cls = None
    app_embedder = None
    if args.loader == "essentia":
        mono_loader_cls = step(
            "import essentia.standard.MonoLoader",
            lambda: __import__("essentia.standard", fromlist=["MonoLoader"]).MonoLoader,
            capture=capture,
        )
    elif args.loader == "app":
        app_embedder = step(
            "DiscogsEffnetEmbedder init",
            lambda: embedder_mod.DiscogsEffnetEmbedder(settings, args.model),
            capture=capture,
        )

    predict_cls = step(
        "import essentia.standard.TensorflowPredictEffnetDiscogs",
        lambda: __import__(
            "essentia.standard", fromlist=["TensorflowPredictEffnetDiscogs"]
        ).TensorflowPredictEffnetDiscogs,
        capture=capture,
    )

    objects: dict[str, object] = {}
    if not args.new_predictor_per_track:
        objects["predictor"] = step(
            "TensorflowPredictEffnetDiscogs init shared",
            lambda: predict_cls(graphFilename=str(model_path), output=embedder_mod.MODEL_OUTPUTS[args.model]),
            capture=capture,
        )

    for index, current_audio_path in enumerate(audio_paths, start=1):
        emit(f"=== TRACK {index}/{len(audio_paths)} {current_audio_path} ===", force=QUIET_STDOUT)
        if args.loader == "ffmpeg":
            audio = step(
                f"track {index} ffmpeg load",
                lambda p=current_audio_path: embedder_mod.load_audio_with_ffmpeg(p),
                capture=capture,
            )
        elif args.loader == "essentia":
            loader_objects: dict[str, object] = {}
            loader_objects["loader"] = step(
                f"track {index} MonoLoader init",
                lambda p=current_audio_path: mono_loader_cls(
                    filename=str(p), sampleRate=16000, resampleQuality=4
                ),
                capture=capture,
            )
            audio = step(f"track {index} MonoLoader call", loader_objects["loader"], capture=capture)
            step(
                f"track {index} MonoLoader delete+gc",
                lambda ns=loader_objects: delete_and_collect("loader", ns),
                capture=capture,
            )
        else:
            audio = step(
                f"track {index} DiscogsEffnetEmbedder._load_audio",
                lambda p=current_audio_path: app_embedder._load_audio(p),
                capture=capture,
            )

        emit(f"audio_shape={getattr(audio, 'shape', None)} dtype={getattr(audio, 'dtype', None)}")
        if args.new_predictor_per_track:
            objects["predictor"] = step(
                f"track {index} TensorflowPredictEffnetDiscogs init",
                lambda: predict_cls(
                    graphFilename=str(model_path),
                    output=embedder_mod.MODEL_OUTPUTS[args.model],
                ),
                capture=capture,
            )

        embeddings = step(
            f"track {index} TensorflowPredictEffnetDiscogs call",
            lambda: objects["predictor"](audio),
            capture=capture,
        )
        emit(f"embeddings_shape={getattr(embeddings, 'shape', None)} dtype={getattr(embeddings, 'dtype', None)}")
        vector = step(
            f"track {index} pool_and_normalize",
            lambda: embedder_mod.pool_and_normalize(embeddings),
            capture=capture,
        )
        emit(f"vector_shape={vector.shape} dtype={vector.dtype} norm={(vector ** 2).sum() ** 0.5:.6f}")

        if args.new_predictor_per_track:
            step(
                f"track {index} predictor delete+gc",
                lambda: delete_and_collect("predictor", objects),
                capture=capture,
            )

    if not args.new_predictor_per_track:
        step("predictor shared delete+gc", lambda: delete_and_collect("predictor", objects), capture=capture)
    if app_embedder is not None:
        app_objects = {"embedder": app_embedder}
        step(
            "DiscogsEffnetEmbedder shared delete+gc",
            lambda: delete_and_collect("embedder", app_objects),
            capture=capture,
        )
    emit("probe complete")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Probe which Essentia operation emits the streaming Network warning."
    )
    parser.add_argument("audio", nargs="?", type=Path, help="Audio file to load and analyze")
    parser.add_argument("--model", default="discogs_multi", help="Model key from app.config")
    parser.add_argument(
        "--loader",
        choices=["app", "essentia", "ffmpeg"],
        default="app",
        help="Audio loading path to probe",
    )
    parser.add_argument(
        "--no-capture",
        action="store_true",
        help="Do not capture C/C++ stderr per step; let warnings stream directly",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Analyze the same audio path this many times with one predictor instance",
    )
    parser.add_argument(
        "--audio-list",
        type=Path,
        help="Text file with one audio path per line. Overrides positional audio after config checks.",
    )
    parser.add_argument(
        "--new-predictor-per-track",
        action="store_true",
        help="Create and delete TensorflowPredictEffnetDiscogs for each track instead of reusing one instance.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        help="Write the full probe transcript to this file and keep terminal output short.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full transcript to terminal even when --log-file is used.",
    )
    parser.add_argument(
        "--summarize-log",
        type=Path,
        help="Parse an existing probe log and print a compact summary, then exit.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.summarize_log:
        return summarize_log_file(args.summarize_log)
    if args.audio is None:
        parser.error("audio is required unless --summarize-log is used")

    global LOG_FILE, QUIET_STDOUT
    QUIET_STDOUT = args.log_file is not None and not args.verbose
    try:
        if args.log_file:
            args.log_file.parent.mkdir(parents=True, exist_ok=True)
            LOG_FILE = args.log_file.open("w", encoding="utf-8")
            emit(f"writing full probe log to {args.log_file}", force=True)
        status = run_probe(args)
        if args.log_file:
            LOG_FILE.flush()
            print(format_summary(parse_probe_log(args.log_file.read_text(encoding="utf-8", errors="replace"))))
        return status
    finally:
        if LOG_FILE is not None:
            LOG_FILE.close()


if __name__ == "__main__":
    raise SystemExit(main())
