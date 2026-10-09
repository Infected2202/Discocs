"""Итоги прогона describe (out*/<id>.json) → в discocs, как их прислал бы воркер (docs/tools.md).

Для прогонов из командной строки, сделанных до воркера (tools/agent). Два шага:

1. На ПК — собрать итоги в один файл, прогнав тексты через нынешние кодовые чистки (дубли имён, перечни):

       .venv/Scripts/python import_results.py pack out_night results.json

2. На сервере — записать их в базу внутри контейнера бэкенда (та же функция, что у API воркера):

       docker cp results.json discocs-backend-1:/tmp/ && docker cp import_results.py discocs-backend-1:/tmp/
       docker exec discocs-backend-1 python /tmp/import_results.py apply /tmp/results.json

Текст агента сразу становится описанием лейбла (кроме редакционного), прежнее запоминается для отката;
«ничего не нашлось» прежнее описание не стирает.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def pack(out_dir: Path, target: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from research import _artist_list, _drop_sentence, _fix_typos, _sentences, _unique  # noqa: PLC0415

    results = []
    for path in sorted(out_dir.glob("*.json")):
        saved = json.loads(path.read_text(encoding="utf-8"))
        facts = saved.get("facts", [])
        text = (saved.get("description") or "").strip()
        if text:
            text = _fix_typos(text, " ".join(f"{f['text']} {f['quote']}" for f in facts))
            for sentence in _sentences(text):
                if _artist_list(sentence):
                    text = _drop_sentence(text, sentence)
        sources = saved.get("sources") or _unique([f["url"] for f in facts])[:30]
        results.append({
            "label_id": int(saved["id"]),
            "status": "written" if text else "not_found",
            "description": text or None,
            "sources": [{"url": url} for url in sources] if text else [],
            "model": "google/gemma-4-26b-a4b-qat",
            "note": (saved.get("note") or "")[:2000] or None,
        })
    target.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(len(results), "results:", dict(Counter(r["status"] for r in results)))


def apply(source: Path) -> None:
    sys.path.insert(0, "/app")
    from app.config import Settings  # noqa: PLC0415
    from app.store import Store  # noqa: PLC0415

    store = Store(Settings.from_env().db_path)
    outcomes: Counter[str] = Counter()
    for item in json.loads(source.read_text(encoding="utf-8")):
        outcome = store.save_agent_description(
            item["label_id"], job_id=None, status=item["status"], description=item["description"],
            sources=item["sources"], model=item["model"], note=item["note"],
        )
        outcomes[str(outcome)] += 1
    print(dict(outcomes))


if __name__ == "__main__":
    command, *paths = sys.argv[1:]
    if command == "pack":
        pack(Path(paths[0]), Path(paths[1]))
    elif command == "apply":
        apply(Path(paths[0]))
    else:
        raise SystemExit(__doc__)
