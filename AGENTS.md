# discocs

## Работа с проектом

Коммить и пушить **один раз, когда задача полностью выполнена** — не после
каждой отдельной правки. `disableConcurrentBuilds()` в Jenkins не даёт сборкам
идти параллельно: частые пуши копят очередь из полных прогонов пайплайна
(`Test → Sonar → Build&Push → Security Scan → Deploy`). Один коммит/пуш на
логическую задачу сохраняет осмысленную историю и не перегружает CI.

После завершения задачи пушить `main` в оба remote:

```bash
git push origin main && git push gitea main
```

Gitea-триггер запускает Jenkins CI.

Используй MCP-инструменты когда они уместны: Playwright для проверки UI в браузере, context7 для актуальной документации библиотек.

Каждая значимая задача:
1. Код + тесты — новый код без теста не считается готовым.
2. Документация в `docs/`, если меняется поведение, API или пайплайн.
3. Один коммит + push в `origin/main` и `gitea/main`, когда всё готово.

## Формат ответа

Каждое свое сообщение начинай с обращения к пользователю по имени (Саня).

## Tests

Тесты — обязательная часть каждого значительного изменения. **Не запускай
тесты, сборки, typecheck и линтеры локально для самопроверки** (`pytest`,
`docker build`, `tsc`, `vitest`, `npm run build` и т.п.). Это выполняется в
Jenkins (`Test → Sonar → Security Scan`); локальный прогон дублирует CI и может
расходиться с окружением CI-контейнеров. Пиши код и тесты, затем делай один
коммит/push и проверяй CI по правилам ниже.

Write tests that would fail if the tested logic were removed or inverted. A failing test means the code is broken, not the test. Fix the code, not the test — unless the requirement genuinely changed, in which case update the test first, then fix the code. `tests/conftest.py` поднимает in-memory SQLite и заглушки модели — реальные файлы и Essentia не нужны для unit-тестов.

Ключевые сценарии которые должны быть покрыты:

- SQLite upsert и round-trip эмбеддингов
- Инвалидация при изменении файла (path + mtime + file_size)
- Пулинг векторов и L2-нормализация
- HNSW build/load/query на минимальном каталоге
- Фильтрация в recommender: убрать seed-трек, лимит по артистам, исключить альбом
- FastAPI: health, search, similar — включая пути ошибок

Интеграционные тесты с реальной моделью или Essentia помечать `@pytest.mark.integration`.

## CI results

Результаты сборки, тестов и сканов проверяй в Jenkins, а не локальными
прогонами:

- качество кода, coverage и security hotspots — через SonarQube MCP
  (`mcp__sonarqube__*`), проект `discocs`;
- сборка, стадии, тесты и Trivy — Jenkins API, job
  `http://192.168.1.41:8077/job/HS/job/discocs_build`;
- read-only credentials `JENKINS_USER`/`JENKINS_TOKEN` находятся в
  `.claude/settings.local.json` и не коммитятся.

Проверяй CI от дешёвого запроса к дорогому:

1. `GET lastBuild/api/json?tree=number,building,result` — дождаться
   `building:false` для сборки, запущенной текущим push.
2. При `result == SUCCESS` дальнейшие запросы не нужны.
3. При ошибке сначала использовать структурированные источники:
   - тесты: `GET lastBuild/testReport/api/json?tree=failCount,passCount,skipCount`;
   - детали упавших тестов: `testReport/api/json?tree=suites%5Bcases%5Bname,className,status,errorDetails%5D%5D`;
   - Trivy HTML: `lastBuild/Trivy_3a_20backend/` и аналогичные страницы
     `frontend`/`bot`;
   - статусы прочих стадий: `GET lastBuild/wfapi/describe`.
4. `lastBuild/consoleText` читать только если структурированные данные не
   объяснили ошибку.

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
