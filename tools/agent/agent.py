"""Воркер инструментов на ПК (docs/tools.md): берёт задачи из админки discocs и выполняет их здесь.

    .venv/Scripts/pythonw.exe agent.py        # без окна; .venv — общий с tools/describe

Сам ходит в discocs и ждёт задачу долгим опросом — в простое не тратит ни процессор, ни сеть. Работает с
приоритетом «ниже обычного». Задачи:

- describe — описания лейблов (tools/describe). Модель загружается в LM Studio перед задачей и выгружается
  после. Видеопамять заняла игра — модель выгружается, задача на паузе, пока память не освободится.
- music-fill — запустить или остановить сервер tools/music-fill.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent
DESCRIBE_DIR = TOOLS / "describe"
MUSIC_FILL_DIR = TOOLS / "music-fill"

POLL_WAIT = 55  # секунд держит сервер долгий опрос
PROGRESS_EVERY = 5  # секунд между отчётами о прогрессе
GPU_CHECK_EVERY = 10  # секунд между проверками видеопамяти (nvidia-smi — отдельный процесс)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

log = logging.getLogger("agent")


# --- настройки и окружение ---------------------------------------------------


def load_config() -> dict:
    path = HERE / "config.json"
    if not path.exists():
        path = HERE / "config.example.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config.setdefault("worker_id", socket.gethostname().lower())
    return config


def setup_logging() -> None:
    (HERE / "logs").mkdir(exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(HERE / "logs" / "agent.log", maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()])


def lower_priority() -> None:
    """Приоритет процесса «ниже обычного»: свои задачи на ПК всегда получают процессор первыми."""
    if os.name == "nt":
        import ctypes

        below_normal = 0x00004000
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), below_normal)
    else:
        os.nice(10)


def run(*args: str, timeout: int = 300) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=_NO_WINDOW,
                            encoding="utf-8", errors="replace")
    return result.stdout


def vram_used_mb() -> int | None:
    try:
        out = run("nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits", timeout=20)
        return int(out.strip().splitlines()[0])
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


# --- discocs ------------------------------------------------------------------


class Discocs:
    def __init__(self, config: dict) -> None:
        self.url = config["discocs_url"].rstrip("/")
        self.worker_id = config["worker_id"]
        self.http = requests.Session()
        if config.get("service_token"):
            self.http.headers["X-Discocs-Service-Token"] = config["service_token"]

    def _post(self, path: str, body: dict, timeout: float = 30) -> dict:
        response = self.http.post(f"{self.url}/api/v1{path}", json=body, timeout=timeout)
        response.raise_for_status()
        return response.json()

    def poll(self, tools: list[str], state: dict, wait: int) -> dict | None:
        return self._post(f"/tools/workers/{self.worker_id}/poll", {"tools": tools, "state": state, "wait": wait},
                          timeout=wait + 30)["job"]

    def progress(self, job_id: int, **body: object) -> str:
        return str(self._post(f"/tools/jobs/{job_id}/progress", body).get("control", "run"))

    def next_labels(self, job_id: int, limit: int, exclude: list[int]) -> list[dict]:
        return self._post(f"/tools/jobs/{job_id}/describe/next", {"limit": limit, "exclude": exclude})["labels"]

    def result(self, body: dict) -> str:
        return str(self._post("/tools/describe/results", body).get("outcome"))


# --- LM Studio ----------------------------------------------------------------


class Model:
    """Модель в LM Studio: загрузить перед задачей, выгрузить после или когда видеопамять нужна игре."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.name = config["model"]

    def loaded(self) -> bool:
        try:
            models = requests.get(f"{self.config['lmstudio_api']}/api/v0/models", timeout=10).json()["data"]
        except (requests.RequestException, ValueError, KeyError):
            return False
        return any(m.get("id") == self.name and m.get("state") == "loaded" for m in models)

    def load(self) -> None:
        if self.loaded():
            return
        run("lms", "server", "start", timeout=60)
        log.info("loading %s", self.name)
        run("lms", "load", self.name, "-c", str(self.config["model_context"]), "--parallel",
            str(self.config["jobs"]), "--gpu", "max", "-y", timeout=600)
        if not self.loaded():
            raise RuntimeError(f"LM Studio did not load {self.name}")

    def unload(self) -> None:
        if self.loaded():
            log.info("unloading %s", self.name)
            run("lms", "unload", self.name, timeout=120)


def gpu_busy(config: dict, model_loaded: bool) -> bool:
    """Видеопамять нужна чему-то ещё (игра): с моделью — сверх лимита; без модели — модель уже не влезет."""
    used = vram_used_mb()
    if used is None:
        return False
    if model_loaded:
        return used > config["vram_limit_mb"]
    return used + config["model_vram_mb"] > config["vram_limit_mb"]


# --- describe -----------------------------------------------------------------


class DescribeJob:
    """Лейблы задачи — порциями с сервера, по ``jobs`` одновременно; итог по каждому — сразу на сервер."""

    def __init__(self, config: dict, api: Discocs, model: Model, job: dict) -> None:
        self.config = config
        self.api = api
        self.model = model
        self.job = job
        self.current: dict[int, dict] = {}  # label_id → {name, step}
        self.counts = {"written": 0, "not_found": 0, "failed": 0}
        self.started = time.time()
        self.lock = threading.Lock()

    def run(self) -> None:
        job_id = int(self.job["id"])
        sys.path.insert(0, str(DESCRIBE_DIR))
        from llm import LLM  # noqa: PLC0415 — код describe живёт рядом, а не в пакете
        from research import Researcher, Subject  # noqa: PLC0415
        from web import Web  # noqa: PLC0415

        def describe(label: dict) -> None:
            label_id = int(label["id"])

            def step(line: str) -> None:
                with self.lock:
                    if label_id in self.current:
                        self.current[label_id]["step"] = line.strip()[:160]

            subject = Subject(label_id, label["name"], label.get("artists", []), label.get("releases", []),
                              label.get("external_ids", {}))
            web = Web(self.config["searxng_url"], self.config.get("searxng_engines", ""),
                      self.config.get("flaresolverr_url", ""))
            researcher = Researcher(LLM(self.config["lmstudio_url"], self.model.name), web, log=step)
            try:
                result = researcher.run(subject)
                status = "written" if result.description else "not_found"
                body = {"job_id": job_id, "label_id": label_id, "status": status, "model": self.model.name,
                        "description": result.description or None, "note": result.note[:2000] or None,
                        "sources": [{"url": url} for url in result.sources[:100]]}
            except Exception as exc:  # noqa: BLE001 — один лейбл не валит задачу
                log.exception("describe %s failed", label["name"])
                status = "failed"
                body = {"job_id": job_id, "label_id": label_id, "status": status, "note": repr(exc)[:2000]}
            self.api.result(body)
            with self.lock:
                self.counts[status] += 1
                self.current.pop(label_id, None)
            log.info("%s: %s", label["name"], status)

        pool = ThreadPoolExecutor(int(self.config["jobs"]))
        running: dict[int, Future] = {}
        control, last_report, exhausted, paused_by = "run", 0.0, False, ""
        busy, last_gpu_check = False, 0.0
        try:
            while True:
                for label_id, future in list(running.items()):
                    if future.done():
                        running.pop(label_id)
                        future.exception()  # ошибка уже в журнале
                if time.time() - last_gpu_check >= GPU_CHECK_EVERY:
                    busy = gpu_busy(self.config, self.model.loaded())
                    last_gpu_check = time.time()
                if time.time() - last_report >= PROGRESS_EVERY:
                    status = "paused" if (control == "pause" or busy) and not running else "running"
                    paused_by = "gpu" if busy else ("admin" if control == "pause" else "")
                    control = self.api.progress(job_id, status=status, progress=self._progress(paused_by),
                                                message="GPU is busy" if paused_by == "gpu" else None)
                    last_report = time.time()
                if control == "cancel":
                    if not running:
                        self.api.progress(job_id, status="cancelled", progress=self._progress(""))
                        return
                elif control == "pause" or busy:
                    # Пауза: новых лейблов не брать; когда разобранные доделаны — освободить видеокарту.
                    if not running:
                        self.model.unload()
                elif not exhausted and len(running) < int(self.config["jobs"]):
                    self.model.load()
                    labels = self.api.next_labels(job_id, int(self.config["jobs"]) - len(running), list(running))
                    if not labels and not running:
                        exhausted = True
                    for label in labels:
                        with self.lock:
                            self.current[int(label["id"])] = {"name": label["name"], "step": "starting"}
                        running[int(label["id"])] = pool.submit(describe, label)
                if exhausted and not running:
                    # Ещё раз: перезапущенный воркер мог оставить недоделанные — сервер вернёт их.
                    if not self.api.next_labels(job_id, 1, []):
                        self.api.progress(job_id, status="done", progress=self._progress(""))
                        return
                    exhausted = False
                time.sleep(1)
        finally:
            pool.shutdown(wait=True)
            self.model.unload()

    def _progress(self, paused_by: str) -> dict:
        with self.lock:
            done = sum(self.counts.values())
            hours = max((time.time() - self.started) / 3600, 1 / 60)
            return {
                **self.counts,
                "session_done": done,
                "per_hour": round(done / hours, 1),
                "current": [{"id": label_id, **info} for label_id, info in self.current.items()],
                "paused_by": paused_by,
            }


# --- music-fill -----------------------------------------------------------------


class MusicFill:
    """Сервер tools/music-fill — запущенный воркером процесс; если сервер подняли руками, он виден по порту."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.process: subprocess.Popen | None = None

    def up(self) -> bool:
        try:
            requests.get(self.config["music_fill_url"], timeout=3)
            return True
        except requests.RequestException:
            return False

    def start(self) -> str:
        if self.up():
            return "already running"
        logs = MUSIC_FILL_DIR / "logs"
        logs.mkdir(exist_ok=True)
        out = (logs / "agent-server.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [self.config["music_fill_python"], "review_server.py"], cwd=MUSIC_FILL_DIR, stdout=out,
            stderr=subprocess.STDOUT, creationflags=_NO_WINDOW,
        )
        for _ in range(30):
            if self.up():
                return "started"
            time.sleep(1)
        return "started, not answering yet"

    def stop(self) -> str:
        if self.process is None or self.process.poll() is not None:
            return "not started by the agent" if self.up() else "not running"
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        return "stopped"

    def state(self) -> dict:
        return {"running": self.up(), "url": self.config["music_fill_url"],
                "managed": self.process is not None and self.process.poll() is None}


# --- цикл -----------------------------------------------------------------------


def main() -> None:
    setup_logging()
    lower_priority()
    config = load_config()
    api = Discocs(config)
    model = Model(config)
    music_fill = MusicFill(config)
    tools = ["describe", "music-fill"]
    log.info("agent %s → %s", config["worker_id"], config["discocs_url"])
    while True:
        try:
            state = {"music_fill": music_fill.state(), "model_loaded": model.loaded(), "vram_used_mb": vram_used_mb()}
            job = api.poll(tools, state, POLL_WAIT)
            if job is None:
                continue
            log.info("job %s: %s %s", job["id"], job["tool"], job["action"])
            try:
                if job["tool"] == "describe":
                    DescribeJob(config, api, model, job).run()
                elif job["tool"] == "music-fill":
                    message = music_fill.start() if job["action"] == "start" else music_fill.stop()
                    api.progress(int(job["id"]), status="done", message=message)
            except requests.RequestException:
                raise  # связь пропала — задача останется за воркером и продолжится
            except Exception as exc:  # noqa: BLE001 — воркер не падает из-за одной задачи
                log.exception("job %s failed", job["id"])
                api.progress(int(job["id"]), status="failed", message=repr(exc)[:2000])
        except requests.RequestException as exc:
            log.warning("discocs unreachable: %s", exc)
            time.sleep(15)
        except Exception:  # noqa: BLE001
            log.exception("agent loop")
            time.sleep(15)


if __name__ == "__main__":
    main()
