"""Описания лейблов: python run.py <labels.json> [--rewrite] [--resume] [--second-pass] [--out=DIR] [--model=…] [--jobs=N] [--write-think] [id ...]
→ out/<id>.json (или DIR/<id>.json).

labels.json — список {id, name, artists, releases, external_ids}. --rewrite — только текст заново,
по фактам из out/<id>.json, без поиска. --resume — пропустить лейблы, у которых уже есть out/<id>.json с фактами
(без фактов — пробуются снова: ночью поиск мог отвалиться). --jobs — сколько лейблов одновременно: модель
в LM Studio должна быть загружена с --parallel не меньше этого числа, иначе запросы просто встанут в очередь.
--second-pass — второй проход по «не найденным» (rescue.py): лейблы со сборниками узнаются по названию, вместо
пустоты — короткая справка.
"""
from __future__ import annotations

import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from llm import LLM
from rescue import SecondPass
from research import Researcher, Subject
from web import Web

HERE = Path(__file__).parent
OUT = HERE / "out"
_print_lock = threading.Lock()


def load_config() -> dict:
    path = HERE / "config.json"
    if not path.exists():
        path = HERE / "config.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def say(line: str) -> None:
    with _print_lock:
        print(line, flush=True)


def describe(config: dict, model: str, label: dict, rewrite: bool, write_think: bool, second: bool = False) -> None:
    subject = Subject(label["id"], label["name"], label.get("artists", []), label.get("releases", []),
                      label.get("external_ids", {}))
    # Свой клиент на лейбл: время по шагам считается отдельно, параллельные лейблы его не путают.
    web = Web(config["searxng_url"], config.get("searxng_engines", ""), config.get("flaresolverr_url", ""))
    researcher = (SecondPass if second else Researcher)(LLM(config["lmstudio_url"], model), web, log=lambda line: say(f"[{subject.name}] {line}"),
                            write_think=write_think)
    say(f"== {subject.name}")
    try:
        if rewrite:
            saved = json.loads((OUT / f"{subject.id}.json").read_text(encoding="utf-8"))
            result = researcher.rewrite(subject, saved)
        else:
            result = researcher.run(subject)
    except Exception as exc:  # noqa: BLE001 — один лейбл не валит весь прогон
        say(f"== {subject.name}: FAILED {exc!r}")
        return
    (OUT / f"{subject.id}.json").write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
    say(f"== {subject.name}: {result.seconds:.0f} s, {len(result.facts)} facts, {result.llm_seconds} {result.note}\n"
        f"{result.description}")


def main() -> None:
    config = load_config()
    labels = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    args = sys.argv[2:]
    rewrite = "--rewrite" in args
    model = next((a.split("=", 1)[1] for a in args if a.startswith("--model=")), config["model"])
    write_think = "--write-think" in args
    second = "--second-pass" in args
    jobs = int(next((a.split("=", 1)[1] for a in args if a.startswith("--jobs=")), "1"))
    wanted = {int(a) for a in args if not a.startswith("--")}
    global OUT
    OUT = HERE / next((a.split("=", 1)[1] for a in args if a.startswith("--out=")), "out")
    OUT.mkdir(exist_ok=True)
    todo = [label for label in labels if not wanted or label["id"] in wanted]
    if "--resume" in args:
        todo = [label for label in todo if not _done(label["id"])]
        say(f"resume: {len(todo)} labels left")
    with ThreadPoolExecutor(jobs) as pool:
        list(pool.map(lambda label: describe(config, model, label, rewrite, write_think, second), todo))


def _done(label_id: int) -> bool:
    path = OUT / f"{label_id}.json"
    return path.exists() and bool(json.loads(path.read_text(encoding="utf-8")).get("facts"))


if __name__ == "__main__":
    main()
