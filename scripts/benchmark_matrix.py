from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path


DEFAULT_PRESETS = [
    "workers1",
    "prefetch",
    "workers4",
    "workers4-tf4",
    "workers4-tf3",
    "workers4-tf2",
    "workers5-tf4",
    "workers5-tf3",
    "workers6-tf4",
    "workers6-tf3",
    "workers6-tf2",
    "workers8-tf3",
    "workers8-tf2",
    "workers4-tf4-ff1",
    "workers6-tf3-ff1",
    "workers8-tf2-ff1",
]


def preset_env(tf_threads: int | None = None, ffmpeg_threads: int | None = None) -> dict[str, str]:
    env = {}
    if tf_threads is not None:
        env.update(
            {
                "TF_NUM_INTRAOP_THREADS": str(tf_threads),
                "TF_NUM_INTEROP_THREADS": "1",
                "OMP_NUM_THREADS": str(tf_threads),
            }
        )
    if ffmpeg_threads is not None:
        env["DISCOCS_FFMPEG_THREADS"] = str(ffmpeg_threads)
    return env


def tf_env(threads: int) -> dict[str, str]:
    return preset_env(tf_threads=threads)


BUILTIN_PRESETS = {
    "default": {},
    "prefetch": {},
    "workers1": {},
    "workers2": {},
    "workers3": {},
    "workers4": {},
    "tf2": tf_env(2),
    "tf4": tf_env(4),
    "tf8": tf_env(8),
    "tf16": tf_env(16),
    "workers2-tf8": tf_env(8),
    "workers2-tf4": tf_env(4),
    "workers3-tf8": tf_env(8),
    "workers3-tf4": tf_env(4),
    "workers4-tf8": tf_env(8),
    "workers4-tf4": tf_env(4),
    "workers4-tf3": tf_env(3),
    "workers4-tf2": tf_env(2),
    "workers5-tf4": tf_env(4),
    "workers5-tf3": tf_env(3),
    "workers6-tf4": tf_env(4),
    "workers6-tf3": tf_env(3),
    "workers6-tf2": tf_env(2),
    "workers8-tf3": tf_env(3),
    "workers8-tf2": tf_env(2),
    "workers4-tf4-ff1": preset_env(tf_threads=4, ffmpeg_threads=1),
    "workers6-tf3-ff1": preset_env(tf_threads=3, ffmpeg_threads=1),
    "workers8-tf2-ff1": preset_env(tf_threads=2, ffmpeg_threads=1),
}


def parse_preset(spec: str) -> tuple[str, dict[str, str]]:
    if ":" not in spec:
        dynamic = re.fullmatch(r"(?:workers|w)(\d+)(?:-tf(\d+))?(?:-ff(\d+))?", spec)
        if dynamic:
            tf_threads = int(dynamic.group(2)) if dynamic.group(2) else None
            ffmpeg_threads = int(dynamic.group(3)) if dynamic.group(3) else None
            return spec, preset_env(tf_threads=tf_threads, ffmpeg_threads=ffmpeg_threads)
        if spec not in BUILTIN_PRESETS:
            known = ", ".join(sorted(BUILTIN_PRESETS))
            raise ValueError(
                f"Unknown preset '{spec}'. Known presets: {known}. "
                "Dynamic presets like workers6-tf3-ff1 are also supported."
            )
        return spec, dict(BUILTIN_PRESETS[spec])

    name, raw_pairs = spec.split(":", 1)
    env = {}
    for pair in raw_pairs.split(","):
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"Invalid preset env pair '{pair}' in '{spec}'")
        key, value = pair.split("=", 1)
        env[key] = value
    return name, env


def preset_workers(name: str, default_workers: int) -> int:
    match = re.match(r"(?:workers|w)(\d+)", name)
    if match:
        return int(match.group(1))
    return default_workers


def preset_pipeline(name: str, default_pipeline: str) -> str:
    if name == "prefetch":
        return "prefetch"
    return default_pipeline


def read_system_cpu() -> tuple[int, int] | None:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
    except OSError:
        return None
    if not fields or fields[0] != "cpu":
        return None
    values = [int(value) for value in fields[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return total, idle


def read_status(pid: int) -> dict[str, int]:
    status = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("PPid:"):
                status["ppid"] = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                status["rss_kb"] = int(line.split()[1])
            elif line.startswith("Threads:"):
                status["threads"] = int(line.split()[1])
    except OSError:
        return {}
    return status


def read_cpu_ticks(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return 0
    end_comm = stat.rfind(")")
    if end_comm == -1:
        return 0
    fields = stat[end_comm + 2 :].split()
    if len(fields) < 13:
        return 0
    return int(fields[11]) + int(fields[12])


def process_descendants(root_pid: int) -> list[int]:
    parent_to_children: dict[int, list[int]] = {}
    for entry in Path("/proc").iterdir() if Path("/proc").exists() else []:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        status = read_status(pid)
        ppid = status.get("ppid")
        if ppid is not None:
            parent_to_children.setdefault(ppid, []).append(pid)

    seen = []
    queue = deque([root_pid])
    while queue:
        pid = queue.popleft()
        if pid in seen:
            continue
        seen.append(pid)
        queue.extend(parent_to_children.get(pid, []))
    return seen


def read_process_tree(root_pid: int) -> dict[str, int]:
    pids = process_descendants(root_pid)
    rss_kb = 0
    threads = 0
    cpu_ticks = 0
    live_pids = 0
    for pid in pids:
        status = read_status(pid)
        if status:
            live_pids += 1
            rss_kb += status.get("rss_kb", 0)
            threads += status.get("threads", 0)
        cpu_ticks += read_cpu_ticks(pid)
    return {
        "pids": live_pids,
        "rss_kb": rss_kb,
        "threads": threads,
        "cpu_ticks": cpu_ticks,
    }


class ResourceMonitor:
    def __init__(self, pid: int, interval: float, output_path: Path):
        self.pid = pid
        self.interval = interval
        self.output_path = output_path
        self.samples: list[dict[str, float | int | None]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=self.interval * 4)

    def _run(self) -> None:
        clk_tck = os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK"))
        previous_time = time.perf_counter()
        previous_proc = read_process_tree(self.pid)
        previous_system = read_system_cpu()
        started = previous_time
        with self.output_path.open("w", encoding="utf-8") as handle:
            while not self._stop.wait(self.interval):
                now = time.perf_counter()
                current_proc = read_process_tree(self.pid)
                current_system = read_system_cpu()
                elapsed = max(now - previous_time, 1e-9)
                proc_delta = current_proc["cpu_ticks"] - previous_proc["cpu_ticks"]
                proc_cpu_percent = (proc_delta / clk_tck / elapsed) * 100
                system_cpu_percent = None
                if previous_system and current_system:
                    total_delta = current_system[0] - previous_system[0]
                    idle_delta = current_system[1] - previous_system[1]
                    if total_delta > 0:
                        system_cpu_percent = ((total_delta - idle_delta) / total_delta) * 100
                sample = {
                    "t": now - started,
                    "process_cpu_percent": proc_cpu_percent,
                    "system_cpu_percent": system_cpu_percent,
                    "rss_mb": current_proc["rss_kb"] / 1024,
                    "threads": current_proc["threads"],
                    "pids": current_proc["pids"],
                }
                self.samples.append(sample)
                handle.write(json.dumps(sample) + "\n")
                handle.flush()
                previous_time = now
                previous_proc = current_proc
                previous_system = current_system


def summarize_resources(samples: list[dict[str, float | int | None]]) -> dict[str, float | int | None]:
    if not samples:
        return {
            "samples": 0,
            "process_cpu_avg": None,
            "process_cpu_max": None,
            "system_cpu_avg": None,
            "system_cpu_max": None,
            "rss_mb_max": None,
            "threads_max": None,
            "pids_max": None,
        }
    process_cpu = [float(sample["process_cpu_percent"]) for sample in samples]
    system_cpu = [
        float(sample["system_cpu_percent"])
        for sample in samples
        if sample["system_cpu_percent"] is not None
    ]
    return {
        "samples": len(samples),
        "process_cpu_avg": sum(process_cpu) / len(process_cpu),
        "process_cpu_max": max(process_cpu),
        "system_cpu_avg": sum(system_cpu) / len(system_cpu) if system_cpu else None,
        "system_cpu_max": max(system_cpu) if system_cpu else None,
        "rss_mb_max": max(float(sample["rss_mb"]) for sample in samples),
        "threads_max": max(int(sample["threads"]) for sample in samples),
        "pids_max": max(int(sample["pids"]) for sample in samples),
    }


def format_matrix_summary(results: list[dict[str, object]]) -> str:
    lines = ["Benchmark matrix summary"]
    ordered = sorted(
        results,
        key=lambda item: float(item["benchmark"].get("tracks_per_min") or 0),
        reverse=True,
    )
    for result in ordered:
        bench = result["benchmark"]
        resources = result["resources"]
        lines.append(
            "- {name}: {tpm:.2f} tracks/min, predict avg {pred:.3f}s, "
            "proc CPU avg {cpu}, RSS max {rss}, pids max {pids}, warnings {warnings}".format(
                name=result["name"],
                tpm=float(bench.get("tracks_per_min") or 0),
                pred=float(bench["predict"]["avg"] or 0),
                cpu=(
                    "n/a"
                    if resources["process_cpu_avg"] is None
                    else f"{float(resources['process_cpu_avg']):.1f}%"
                ),
                rss=(
                    "n/a"
                    if resources["rss_mb_max"] is None
                    else f"{float(resources['rss_mb_max']):.0f}MB"
                ),
                pids=resources.get("pids_max") if resources.get("pids_max") is not None else "n/a",
                warnings=bench.get("warnings"),
            )
        )
    return "\n".join(lines)


def run_preset(args: argparse.Namespace, name: str, env_overrides: dict[str, str]) -> dict[str, object]:
    output_dir = args.out_dir / name
    output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = output_dir / "stdout.log"
    resource_path = output_dir / "resources.jsonl"
    summary_path = output_dir / "summary.json"
    jsonl_path = output_dir / "tracks.jsonl"
    command = [
        sys.executable,
        str(Path(__file__).with_name("benchmark_embeddings.py")),
        "--audio-list",
        str(args.audio_list),
        "--model",
        args.model,
        "--loader",
        args.loader,
        "--warmup",
        str(args.warmup),
        "--workers",
        str(preset_workers(name, args.workers)),
        "--pipeline",
        preset_pipeline(name, args.pipeline),
        "--start-method",
        args.start_method,
        "--jsonl",
        str(jsonl_path),
        "--summary-json",
        str(summary_path),
    ]
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.repeat != 1:
        command.extend(["--repeat", str(args.repeat)])
    if args.no_capture:
        command.append("--no-capture")

    env = os.environ.copy()
    env.update(env_overrides)
    stdout_path.write_text("", encoding="utf-8")
    print(f"\n=== Running preset {name}: {env_overrides or 'default env'} ===", flush=True)
    with stdout_path.open("a", encoding="utf-8") as stdout:
        process = subprocess.Popen(
            command,
            cwd=Path(__file__).resolve().parents[1],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        monitor = ResourceMonitor(process.pid, args.resource_interval, resource_path)
        monitor.start()
        assert process.stdout is not None
        for line in process.stdout:
            stdout.write(line)
            stdout.flush()
            if line.startswith(
                (
                    "Embedding benchmark summary",
                    "- ",
                    "warmup ",
                    "worker ",
                    "benchmark ",
                )
            ):
                print(line, end="", flush=True)
        return_code = process.wait()
        monitor.stop()
    if return_code != 0:
        raise RuntimeError(f"Preset {name} failed with exit code {return_code}. See {stdout_path}")

    benchmark = json.loads(summary_path.read_text(encoding="utf-8"))
    resources = summarize_resources(monitor.samples)
    combined = {
        "name": name,
        "env": env_overrides,
        "benchmark": benchmark,
        "resources": resources,
        "paths": {
            "stdout": str(stdout_path),
            "resources_jsonl": str(resource_path),
            "summary_json": str(summary_path),
            "tracks_jsonl": str(jsonl_path),
        },
    }
    (output_dir / "combined-summary.json").write_text(
        json.dumps(combined, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run embedding benchmark presets sequentially.")
    parser.add_argument("--audio-list", required=True, type=Path)
    parser.add_argument("--model", default="discogs_multi")
    parser.add_argument("--loader", choices=["ffmpeg", "essentia"], default="ffmpeg")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--pipeline", choices=["sequential", "prefetch"], default="sequential")
    parser.add_argument(
        "--start-method",
        choices=["spawn", "fork", "forkserver"],
        default="spawn",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("eval/results/benchmark-matrix"))
    parser.add_argument("--resource-interval", type=float, default=1.0)
    parser.add_argument("--no-capture", action="store_true")
    parser.add_argument(
        "--preset",
        action="append",
        default=None,
        help="Builtin preset name or name:KEY=VALUE,KEY=VALUE. Can be repeated.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    presets = args.preset or DEFAULT_PRESETS
    parsed = [parse_preset(spec) for spec in presets]
    args.out_dir.mkdir(parents=True, exist_ok=True)
    results = [run_preset(args, name, env) for name, env in parsed]
    matrix_summary = {
        "presets": results,
        "ranking": sorted(
            [
                {
                    "name": result["name"],
                    "tracks_per_min": result["benchmark"].get("tracks_per_min"),
                    "predict_avg": result["benchmark"]["predict"]["avg"],
                    "process_cpu_avg": result["resources"]["process_cpu_avg"],
                    "rss_mb_max": result["resources"]["rss_mb_max"],
                    "pids_max": result["resources"]["pids_max"],
                    "warnings": result["benchmark"].get("warnings"),
                }
                for result in results
            ],
            key=lambda item: float(item["tracks_per_min"] or 0),
            reverse=True,
        ),
    }
    (args.out_dir / "matrix-summary.json").write_text(
        json.dumps(matrix_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    text = format_matrix_summary(results)
    (args.out_dir / "matrix-summary.txt").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    print(f"\nWrote matrix summary: {args.out_dir / 'matrix-summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
