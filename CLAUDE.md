# discocs

## Работа с проектом

Создавай коммит после каждого изменения.

Используй MCP-инструменты когда они уместны: Playwright для проверки UI в браузере, context7 для актуальной документации библиотек.

При каждом изменении:
1. Пиши или обновляй тесты — новый код без теста не считается готовым.
2. Обновляй документацию в `docs/` если меняется поведение, API или пайплайн.

## Формат ответа

Каждое свое сообщение начинай с обращения к пользователю по имени (Саня).

## Tests

Тесты — обязательная часть каждого значительного изменения, не запускай тесты после изменения параметров в конфигах.

Write tests that would fail if the tested logic were removed or inverted. A failing test means the code is broken, not the test. Fix the code, not the test — unless the requirement genuinely changed, in which case update the test first, then fix the code. `tests/conftest.py` поднимает in-memory SQLite и заглушки модели — реальные файлы и Essentia не нужны для unit-тестов.

Ключевые сценарии которые должны быть покрыты:

- SQLite upsert и round-trip эмбеддингов
- Инвалидация при изменении файла (path + mtime + file_size)
- Пулинг векторов и L2-нормализация
- HNSW build/load/query на минимальном каталоге
- Фильтрация в recommender: убрать seed-трек, лимит по артистам, исключить альбом
- FastAPI: health, search, similar — включая пути ошибок

Интеграционные тесты с реальной моделью или Essentia помечать `@pytest.mark.integration`.

## UI Rule

В проекте **два** веб-интерфейса. Всегда редактируй только нужный:

| Интерфейс | Путь | URL | Когда трогать |
|---|---|---|---|
| **Новый UI** (основной) | `ui/src/` | `:5173` / `/app` | **ВСЯ работа по UI по умолчанию** |
| **Старая админка** | `app/ui.html` | `:8711/admin` | Только если пользователь явно сказал «админка» или `/admin` |

## Setup

Use Python 3.11+.

For normal development and tests:

```bash
python -m pip install -e ".[dev]"
```

For real audio embedding extraction:

```bash
python -m pip install -e ".[dev,essentia]"
```

`essentia-tensorflow` is a heavy/fragile dependency. Use Docker or an environment with Essentia installed for real embedding extraction. Keep imports lazy and do not require Essentia for unit tests that do not actually run model inference.

## Runtime Files

Runtime state is intentionally local and ignored by git:

```text
data/app.db
data/index_*_hnsw.bin
models/*.pb
models/*.onnx
eval/results/
```

Do not commit music files, model binaries, generated indexes, SQLite databases, or local evaluation output.

Default model path:

```text
models/discogs_multi_embeddings-effnet-bs64-1.pb
```

## Common Commands

```bash
python -m pytest
python -m compileall app tests
recs --help
recs scan /music
recs analyze --limit 500
recs build-index
recs similar --track-id 1 --k 30
uvicorn app.main:app --host 0.0.0.0 --port 8711
```

## Code Guidelines

- Векторы хранятся как нормализованные `float32`. HNSW использует `space="cosine"`, similarity в UI/API = `1 - distance`.
- Инвалидация эмбеддингов при изменении файла: по `path + mtime + file_size`.
- Анализатор пропускает треки у которых уже есть эмбеддинги для выбранной модели (resume behavior).
- Тяжёлые зависимости (Essentia, TensorFlow) импортируются лениво внутри методов, не на уровне модуля.
- Не вводить PostgreSQL, Redis, FAISS, GPU, очереди задач — до валидации качества рекомендаций.

## Architecture

`Store` собирается из domain-миксинов через множественное наследование:

```text
app/store/
  base.py            — StoreBase (соединение, миграции)
  library.py         — треки, артисты, релизы
  embeddings.py      — векторы
  mixes.py           — сгенерированные миксы
  flow.py            — flow-профили и регионы
  jobs.py, files.py, playback.py, release_aggregates.py
  __init__.py        — собирает Store из всех миксинов
```

Новый метод в Store → добавляй в нужный миксин, не в `__init__.py`.

## Circular import avoidance

Background job functions use lazy imports inside the function body:

```python
def _analyze_job(...) -> None:
    from app.api.deps import context
    ...
```
