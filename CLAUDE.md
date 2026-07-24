# discocs

## Работа с проектом

Коммить и пушить **один раз, когда задача полностью выполнена** — не после
каждой отдельной правки. `disableConcurrentBuilds()` в Jenkins не даёт сборкам
идти параллельно: частые пуши копят очередь из полных прогонов пайплайна
(Test → Sonar → Build&Push → Security Scan → Deploy), в основном уже
устаревших к моменту старта. Один коммит/пуш на логическую задачу — это и
осмысленная история, и вменяемая нагрузка на CI.

`git push origin main && git push gitea main` — оба remote'а, gitea триггерит
Jenkins CI.

Используй MCP-инструменты когда они уместны: Playwright для проверки UI в браузере, context7 для актуальной документации библиотек.

Каждая значимая задача:
1. Код + тесты — новый код без теста не считается готовым.
2. Документация в `docs/`, если меняется поведение, API или пайплайн.
3. Коммит + push, когда всё готово (см. выше, не раньше).

## Отладка

Сначала диагностика, потом правка. Не гадать вслепую — не пробовать
правдоподобно звучащий фикс просто потому что он вписывается в
документацию/пример. Прежде чем менять код, добывать конкретное
подтверждение причины (лог, реальные данные ответа/заголовки, воспроизведение) —
особенно когда цикл проверки дорогой (пересборка мобильного приложения +
ручная переустановка на устройстве, а не просто перезапуск теста). Если
первая попытка фикса не помогла — это сигнал добыть больше данных, а не
попробовать угадать другой правдоподобный вариант.

Искать корневую причину, а не костылять симптом — даже если она лежит не в
том файле, что выглядит сломанным, а в архитектуре или смежной подсистеме
(другой части пайплайна, прокси/CDN перед доменом, билд-скрипте и т.п.).

## Формат ответа

Каждое свое сообщение начинай с обращения к пользователю по имени (Саня).

## Tests

Тесты — обязательная часть каждого значительного изменения. **Не гоняй тесты,
сборки и линтеры локально для самопроверки** (`pytest`, `docker build`,
`tsc`, `vitest`, `npm run build` и т.п.) — всё это уже выполняется в Jenkins-
джобе (`Test` → `Sonar` → `Security Scan`, см. `docs/cicd.md`), локальный
прогон только дублирует работу и создаёт риск разъехаться с окружением
CI-контейнеров. Пиши код и тесты, коммить/пушь по завершении задачи (см.
выше) и смотри результат в CI — как именно, см. `## CI results` ниже.

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

Результаты сборки/тестов/скана смотри в Jenkins-джобе, а не гоняй их локально:

- **Качество кода / coverage / security hotspots** — SonarQube MCP
  (`mcp__sonarqube__*`), проект `discocs`. Не через Jenkins API.
- **Прошла ли сборка, статус стадии, тесты, Trivy** — Jenkins API, джоба
  `http://192.168.1.41:8077/job/HS/job/discocs_build` (адрес не менять, даже
  если джоба временно недоступна/упала — это постоянный путь, не разовый).
  Креды (`JENKINS_USER`/`JENKINS_TOKEN`, read-only юзер) — в
  `.claude/settings.local.json` (не в гите, локально на этой машине).

Алгоритм — от дешёвого к дорогому, не тяни лог, если не нужен:

1. `GET lastBuild/api/json?tree=number,building,result` — легчайший запрос,
   поллить пока не увидишь `building:false` на нужном номере (сравнивай
   с номером, который был до пуша).
2. `result == SUCCESS` — обычно этого достаточно, дальше не лезть.
3. `result != SUCCESS` — сначала структурированные источники, не консоль:
   - **Тесты** (backend/бот/фронт) — `GET lastBuild/testReport/api/json?tree=failCount,passCount,skipCount`;
     если `failCount > 0` — `testReport/api/json?tree=suites%5Bcases%5Bname,className,status,errorDetails%5D%5D`
     (квадратные скобки в `tree=` **обязательно** percent-encode `%5B`/`%5D` —
     иначе curl молча возвращает пустой ответ без ошибки, проверено вживую),
     отфильтровать по `status != PASSED`, там же название теста и `errorDetails` —
     не нужно искать по логу вручную (стадия `Test` публикует `junit '*.xml'`, см. `docs/cicd.md`).
   - **Security Scan (Trivy)** — HTML-отчёт по образу читаем прямо curl'ом:
     `lastBuild/Trivy_3a_20backend/` (аналогично `..._frontend`/`..._bot` —
     это реальное имя каталога, под которым HTML Publisher архивирует отчёт
     `Trivy: backend`, проверено на билде #55/#56) — обычная HTML-страница,
     можно грепать таблицу CVE так же, как текст.
   - **Любая другая стадия** (Sonar/Docker Login/Build&Push/Deploy) —
     `GET lastBuild/wfapi/describe`: статус по каждой стадии без консоли вообще.
4. Только если ничего из структурированного не объясняет причину —
   `lastBuild/consoleText` целиком (десятки-сотни КБ, не мегабайты) и искать
   нужный кусок локально (по имени стадии / `ERROR`), не заливать весь лог
   в контекст.

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
- Не вводить тяжёлую инфраструктуру (PostgreSQL, Redis, FAISS, GPU, очереди) до валидации качества рекомендаций. Прикладные библиотеки — можно, если задача их требует; зрелая либа лучше самописного решения.

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
