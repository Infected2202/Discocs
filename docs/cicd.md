# CI/CD

Автосборка и деплой на домашний сервер `192.168.1.41`. Вся инфраструктура (Jenkins,
Nexus, Gitea, целевой хост) — одна машина `.41`.

## Поток

Стадии выстроены так, чтобы всё независимое шло параллельно, а последовательными
оставались только настоящие зависимости: Sonar ждёт coverage из тестов, `trivy image` —
собранных образов, деплой — общего успеха.

```
push в Gitea ──webhook──> Jenkins
   └─ Checks        4 ветки parallel, стадия стоит максимум из них, а не сумму:
   │                 backend  — pytest -n auto (deploy/ci/Dockerfile.test)
   │                 бот      — pytest (deploy/ci/Dockerfile.bot-test)
   │                 фронт    — vitest run --coverage (deploy/ci/Dockerfile.ui-test)
   │                 Deps CVE — trivy fs по uv.lock/pnpm-lock (Dockerfile.trivy-fs)
   │                 каждый тестовый образ — BuildKit + --mount=type=cache;
   │                 coverage- и junit-отчёты достаются docker cp'ом, junit
   │                 публикуется в своей же ветке (Test Result Trend)
   └─ Analyze&Build 2 ветки parallel (сборка от Sonar не зависит и наоборот):
   │                 Sonar      — отчёт в SonarQube, без Quality Gate (билд не блокируется)
   │                 Build&Push — Docker Login, затем сборка backend/frontend/bot
   │                              (ещё 3 вложенные ветки) → Nexus (docker-dev @ :5000),
   │                              теги: :<git-sha> (всегда) + :latest (только с main)
   └─ Security Scan одно обновление БД Trivy, затем 3 ветки parallel по образам
   │                 с отдельным in-memory scan cache:
   │                 полный отчёт → HTML-вкладка (publishHTML) → блокирующий гейт
   └─ Deploy        [post/success, только main] по SSH на TARGET_SERVER:
                       scp compose в TARGET_DIR → docker compose pull && up -d --force-recreate
                       backend и frontend должны пройти healthcheck;
                       (.env на хосте CI не трогает — заводится и правится вручную)
```

Все параллельные ветки делят один воркспейс агента (`agent any` наследуется, отдельных
`node` нет) — поэтому `coverage.xml`/`lcov.info`, извлечённые в ветках `Checks`, видны
сканеру в следующей стадии ровно как раньше. `failFast` нигде не включён: упавшая
ветка не обрывает соседние, иначе почти доехавшие junit/coverage снова терялись бы.

Файлы:

| Файл | Роль |
|---|---|
| `Jenkinsfile` | описание пайплайна |
| `deploy/ci/Dockerfile.test` | образ для прогона тестов backend (`app`) в CI |
| `deploy/ci/Dockerfile.bot-test` | образ для прогона тестов бота (`discocs_bot`) в CI |
| `deploy/ci/Dockerfile.ui-test` | образ для прогона тестов фронтенда (`ui`, vitest) в CI |
| `deploy/ci/Dockerfile.trivy-fs` | образ для Trivy fs-скана зависимостей в CI |
| `uv.lock` / `discocs_bot/uv.lock` | закреплённые версии Python-зависимостей (backend / бот) |
| `deploy/prod/docker-compose.yml` | прод-стек: тянет готовые образы из Nexus |
| `deploy/prod/.env.example` | шаблон прод-окружения (реальный `.env` — секрет, не в гит) |

## Реестр образов

- Nexus UI/API: `http://192.168.1.41:8081`, hosted-репо `docker-dev`.
- Docker-эндпоинт (push/pull): **`192.168.1.41:5000`**.
- Имена образов: `192.168.1.41:5000/discocs/{backend,frontend,bot}`.
- Пул анонимный, push — под `tank_nexus_user_pass` (Username/Password, общий для всех джоб).
- Репо работает по HTTP → на хосте нужен `insecure-registries` (уже настроено):

  ```json
  // /etc/docker/daemon.json
  { "insecure-registries": ["192.168.1.41:5000"] }
  ```

  В Nexus у hosted-репо должно быть включено **Allow redeploy** — иначе `:latest` не перезапишется.

## Разовая настройка

### 1. Credentials в Jenkins

| ID | Тип | Назначение |
|---|---|---|
| `tank_nexus_user_pass` | Username/Password | логин в Nexus для push *(уже есть, общий для всех джоб)* |
| `HS_SSH_KEY` | SSH Username with private key | деплой-ключ на `TARGET_SERVER` *(уже есть)* |
| `sonar_token` | Secret text | токен для `sonar-scanner` *(уже есть, общий для всех джоб)* |

`.env` в CI больше не участвует: заполни `deploy/prod/.env.example` реальными значениями
(минимум `DISCOCS_MUSIC_DIR` и `DISCOCS_STATE_DIR`) и один раз положи как `TARGET_DIR/.env`
прямо на хосте (`192.168.1.41`). Дальше CI его не трогает — правишь на месте, когда нужно
поменять переменную (профиль бота, токен, URL Navidrome и т.п.), без пересборки/редеплоя.

### 2. Агент Jenkins (контейнер)

- смонтирован `/var/run/docker.sock` (нужен только для сборки/пуша образов);
- внутри есть `docker` CLI;
- деплой не требует docker socket в агенте — идёт по SSH на `TARGET_SERVER`
  (см. env-блок `Jenkinsfile`: `TARGET_SERVER`, `TARGET_USER`, `TARGET_PORT`, `TARGET_DIR`);
  на целевом хосте должен быть настроен `insecure-registries` для `192.168.1.41:5000` (см. выше)
  и создана директория `TARGET_DIR` (`/home/infected2202/docker/discocs`).

### 3. Job + триггер (Gitea)

1. Установить плагин **Gitea** в Jenkins.
2. Создать job:
   - **Multibranch Pipeline** (рекомендуется — строит все ветки/PR, деплоит только `main`), или
   - **Pipeline** → *Pipeline script from SCM* → Git `http://192.168.1.41:3064/HS/discocs`, ветка `main`, Script Path `Jenkinsfile`.
3. Вебхук в Gitea: репо → Settings → Webhooks → тип **Gitea**, URL
   `http://192.168.1.41:8077/gitea-webhook/post`.

### 4. Git remotes

`origin` — GitHub. Чтобы код попадал ещё и в Gitea (и триггерил сборку), добавь
Gitea вторым push-URL к `origin` — один `git push` уедет в оба:

```bash
git remote set-url --add --push origin git@github.com:Infected2202/Discocs.git
git remote set-url --add --push origin http://192.168.1.41:3064/HS/discocs.git
```

Проверить: `git remote -v` (должно быть два `(push)`).

## Деплой и откат

Деплой автоматический на успешной сборке `main`: Jenkins по SSH заливает
`deploy/prod/docker-compose.yml` в `TARGET_DIR` на `TARGET_SERVER` (`.env` там уже лежит,
CI его не перезаписывает) и там же гоняет `pull` + `up -d --force-recreate --wait`
с явным `TAG=latest`. Это важно: временный rollback-`TAG` в серверном `.env`
не должен навсегда заморозить backend или frontend на старом образе после
успешного нового pipeline.

Вручную на хосте команды по-прежнему учитывают `TAG` из `.env`:

```bash
cd /home/infected2202/docker/discocs   # TARGET_DIR, там же лежит .env
docker compose -p discocs --env-file .env pull
docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans --wait --wait-timeout 120
```

`--wait` — это и есть post-deploy smoke-check: блокирует команду, пока Docker
healthcheck'и сервисов (уже описаны в `deploy/prod/docker-compose.yml`, у backend —
`curl`/`urlopen` на `/health`) не подтвердят `healthy`, и возвращает ненулевой
код, если за `--wait-timeout` (120с) это не произошло — тогда SSH-шаг в
Jenkins падает и стадия `Deploy` считается зафейленной. До этого `up -d`
считался успехом сразу после старта контейнера, падение через несколько
секунд после деплоя было не видно в CI. Требует Docker Compose ≥ v2.17 на
`TARGET_SERVER` — проверить: `docker compose version`.

**Откат** на конкретный билд — поставь его sha в `.env` и повтори up:

```bash
TAG=a1b2c3d   # значение из тега образа / номера коммита
# правишь TAG в TARGET_DIR/.env на сервере, затем:
docker compose -p discocs --env-file .env up -d --force-recreate
```

Следующий успешный pipeline `main` автоматически вернёт `latest`. После проверки
отката удали `TAG` из `.env`, чтобы ручные команды тоже снова брали `latest`.

## Бот

Стартует только при `COMPOSE_PROFILES=bot` в `.env` (плюс заполненный `BOT_TOKEN`
и прочие переменные). Пока не готов — просто не выставляй профиль, backend и
frontend поднимаются без него. Образ бота при этом всё равно собирается и пушится.

Сеть площадки режет боту прямой исходящий трафик до Telegram API, поэтому вместе
с ботом (тот же профиль `bot`) поднимается сайдкар `awg` — весь сетевой стек бота
(`network_mode: service:awg`) идёт через amneziawg-туннель. Конфиг туннеля и
`start-awg.sh` — секреты, кладутся руками в `${DISCOCS_STATE_DIR}/discocs_awg/`
(не коммитятся, см. `deploy/prod/.env.example`). `backend` по-прежнему резолвится
по имени, т.к. `awg` сидит в той же compose-сети.

## Порты на хосте

| Порт | Сервис | Назначение |
|---|---|---|
| `${DISCOCS_HTTP_PORT:-80}` | frontend | новый UI (основной вход) |
| `8711` | backend | приватный LAN-контур: старая админка + worker API; firewall не должен пускать интернет |

`backend` порт публикуется намеренно (`ports: ["8711:7752"]`) — площадка внутренняя,
доступа снаружи LAN нет, поэтому нет смысла прятать API за одним только nginx.

## SonarQube

Стадия `Sonar` в `Jenkinsfile` — ветка параллельной стадии `Analyze & Build`
(вторая ветка — `Build & Push`), идёт после `Checks`, откуда берёт coverage. Гоняет
`sonar-scanner` (см. `sonar-project.properties`) против сервера
`http://192.168.1.41:9077` под токеном `sonar_token`. Это только отчёт —
`waitForQualityGate` не используется, результат анализа не может завалить
билд, смотреть метрики (code smells, дублирование, security hotspots) —
в вебке Sonar по `projectKey=discocs`. Если качество анализа окажется
полезным, можно добавить `waitForQualityGate` (потребует настроить вебхук
SonarQube → Jenkins).

Jenkins задаёт `SONAR_SCANNER_JAVA_OPTS=-Xmx2g` (для SonarScanner CLI 6+):
полный анализ Python, TypeScript и
Docker/IaC превысил стандартный heap scanner JRE и завершался
`OutOfMemoryError` до публикации отчёта. Это лимит только процесса scanner,
не SonarQube-сервера.

Покрыты все части кодовой базы: `sonar.sources=app,discocs_bot/bot,ui/src,deploy`
(бэкенд, бот, фронтенд, CI/Dockerfile'ы), `sonar.tests` — соответствующие каталоги
тестов. `deploy` добавлен ради встроенных в Community Build анализаторов **Secrets**
и **Docker/IaC** — они бесплатны и не требуют отдельного софта (gitleaks/hadolint
не нужны), просто раньше `deploy` не попадал в область сканирования.

Для фронтенда отдельный отчёт линтера не подключён: `ui/eslint.config.js`
использует только рекомендованные пресеты (eslint/typescript-eslint/react-hooks),
встроенный TS/JS-анализатор Sonar покрывает то же самое сам — подключать
`sonar.eslint.reportPaths` стоит, только если появятся кастомные правила.

`sonar-scanner` на Jenkins-агенте — версия `8.0.1.6346` (`/opt/sonar-scanner`,
симлинк `/usr/local/bin/sonar-scanner`), несёт собственную JRE 21. Это важно
держать в актуальном состоянии — при апгрейде SonarQube-сервера сканер может
перестать понимать протокол/движок нового сервера (`UnsupportedClassVersionError`)
или новые опции `tsconfig.json` фронтенда (`Unknown compiler option` от
TS/JS-анализатора) — обновлять сканер вместе с сервером, версия и ссылка на
скачивание — https://docs.sonarsource.com/sonarqube-server/analyzing-source-code/scanners/sonarscanner.

Coverage подключён для всех трёх частей — ветки стадии `Checks` собирают по образу
и достают из каждого свой отчёт через `docker cp` (агент — docker-outside-of-docker,
bind-mount воркспейса недоступен хостовому демону):

- backend (`discocs-test`) — `pytest --cov=app --cov-report=xml` → `coverage.xml`
- бот (`discocs-bot-test`) — `pytest --cov=bot --cov-report=xml` → `bot-coverage.xml`
- фронт (`discocs-ui-test`) — `vitest run --coverage` (provider v8, reporter lcov) → `ui/coverage/lcov.info`

Оба Python-отчёта подхватываются `sonar.python.coverage.reportPaths=coverage.xml,bot-coverage.xml`,
JS-отчёт — `sonar.javascript.lcov.reportPaths=ui/coverage/lcov.info`.
UI-тесты лежат в двух местах: `ui/tests` и рядом с кодом в
`ui/src/**/*.test.{ts,tsx}`. Поэтому `ui/src` указан и в `sonar.sources`, и в
`sonar.tests`: co-located тесты выбираются через `sonar.test.inclusions`, а из
production sources исключаются через `sonar.exclusions`.

Раньше тесты бота и фронтенда вообще не запускались в CI (только числились
в `sonar.tests`), хотя лежали в репозитории — это был реальный пробел, а не
просто нехватка coverage-метрики: `ui/package.json`'s `test` был на `node --test`
вместо `vitest` (4 файла в `ui/tests` импортировали `test` из `node:test`,
из-за чего vitest их не подхватывал), а `discocs_bot/pyproject.toml` не
объявлял `pytest` даже как dev-зависимость.

### JUnit-отчёты (Test Result Trend)

Каждый из трёх тестовых прогонов пишет ещё и JUnit XML — `pytest --junitxml=`
(backend/бот) и `vitest`'ный встроенный `junit`-репортер (фронт, настроен в
`ui/vite.config.ts`: `reporters: ["default", "junit"]`). Каждый из трёх отчётов
(`junit-backend.xml`, `junit-bot.xml`, `junit-ui.xml`) достаётся `docker cp`'ом и
публикуется встроенным шагом `junit` (ядро Jenkins, отдельный плагин не нужен)
прямо в своей ветке `Checks` — даёт нативный **Test Result Trend** на странице
джобы и список конкретно упавших тестов, без грепа консоли. Публикация именно
поветочная, а не общим `junit 'junit-*.xml'` после стадии: параллельные ветки
падают независимо, и общий шаг после стадии подхватил бы отчёты только
полностью зелёного прогона.

Важно: скрипт извлечения coverage/junit специально не использует `set -e` —
раньше (см. историю, билд #53) первый же упавший набор тестов обрывал весь
скрипт до `docker cp`, и при реальном падении тестов не оставалось вообще
никакого отчёта (ни coverage, ни junit) — приходилось лезть в консоль руками.
Теперь код выхода `docker start -a` трекается вручную (`RC`), `docker cp` всегда
выполняется, а ветка фейлится по `exit $RC` уже после того, как отчёты извлечены;
`junit` публикуется в `post { always { ... } }` ветки, а не только при успехе.
По той же причине у параллельных стадий не включён `failFast`: упавший backend
не должен обрывать бота и фронт на полпути.

## Python-зависимости (uv)

Backend (`app`) и бот (`discocs_bot`) ставятся через `uv sync --frozen` вместо
`pip install -e .` — во всех четырёх Dockerfile'ах, что их используют
(`deploy/backend/Dockerfile`, `deploy/bot/Dockerfile`, `deploy/ci/Dockerfile.test`,
`deploy/ci/Dockerfile.bot-test`). `--frozen` ставит строго из `uv.lock` /
`discocs_bot/uv.lock`, без пересчёта резолвера — то же самое закрепление
версий, что видит `trivy fs` (см. ниже). Бинарник `uv` берётся многостадийным
`COPY --from=ghcr.io/astral-sh/uv:latest`, отдельно ставить не нужно.

Lock-файлы обновляются вручную командой `uv lock` (в корне — для `app`,
в `discocs_bot/` — для бота) при изменении зависимостей в `pyproject.toml`;
CI их не регенерирует и не проверяет свежесть (можно добавить `uv lock --check`
отдельной стадией, если резолвинг начнёт расползаться незамеченным).

### Порядок слоёв

Во всех четырёх Dockerfile'ах зависимости ставятся **до** копирования исходников,
двумя шагами:

```dockerfile
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --extra essentia   # тяжёлый слой, ключ — uv.lock
COPY app ./app
RUN uv sync --frozen --inexact --extra essentia              # только сам проект
```

Раньше `COPY app` стоял перед единственным `uv sync`, и любая правка кода —
то есть почти каждый коммит — переустанавливала весь зависимый стек, включая
`essentia-tensorflow` и umap. Теперь тяжёлый слой инвалидируется только сменой
lock-файла.

`--inexact` в финальном sync у backend обязателен: `umap-learn`/`numpy<2`
ставятся отдельным `uv pip install` мимо lock-файла (их там нет), а обычный
`uv sync` приводит venv в точное соответствие локу и снёс бы их — карта
коллекции упала бы на ленивом импорте уже в проде.

## Очистка образов на агенте

`post/always` в `Jenkinsfile` гоняет `docker image prune -a -f --filter "until=48h"`.
Агент — постоянный LXC-контейнер, а не эфемерный воркер: без этого шага тестовые/scan-образы
(`discocs-test`, `discocs-bot-test`, `discocs-ui-test`, `discocs-trivy-fs`) и прод-теги
по `GIT_SHA` копились бы на диске бесконечно (раньше чистка была только на
`TARGET_SERVER`, агента не касалась). `until=48h` — компромисс: свежие билды
ещё можно подебажить руками, старое чистится само.

## Trivy (security scan)

Образ сканера для `trivy fs` закреплён на `aquasec/trivy:0.72.0`, чтобы
одинаковый коммит давал воспроизводимый результат сборки. База уязвимостей
по-прежнему обновляется независимо через кэш Trivy. Версию самого сканера
обновляем явно отдельным коммитом после просмотра release notes.

Trivy разнесён по двум стадиям — по тому, что именно ему нужно на вход:

- `trivy fs` — CVE в `pnpm-lock.yaml` (фронтенд) и в `uv.lock`/`discocs_bot/uv.lock`
  (backend/бот — точный резолвинг версий, см. ниже). Читает только воркспейс,
  поэтому живёт веткой `Deps CVE` в параллельной стадии `Checks`, вместе с тестами;
- `trivy image` — CVE в уже собранных образах `backend`/`frontend`/`bot`, через
  `docker.sock` (не bind-mount воркспейса — тут монтируется только сокет, это
  работает). Стадия `Security Scan`, после `Analyze & Build`: ветка на сервис,
  внутри ветки — полный отчёт, `publishHTML`, затем гейт.

В каждой ветке `Security Scan` сначала всегда формируется полный HTML-отчёт,
включая уязвимости без доступного исправления. После публикации отчёта
отдельный gate повторяет скан с `--ignore-unfixed --severity HIGH,CRITICAL
--exit-code 1`: сборка и деплой останавливаются только на HIGH/CRITICAL, для
которых upstream уже выпустил исправленную версию. Один инструмент закрывает то,
что Sonar Community не умеет бесплатно (SCA — уязвимости в зависимостях).

БД уязвимостей обновляется ровно один раз, отдельным `trivy image
--download-db-only` до разветвления, а сами сканы идут с `--skip-db-update`:
три параллельные ветки на общем volume `trivy-db-cache` иначе полезли бы качать
и распаковывать ~150+ МБ конкурентно в один и тот же каталог. При этом mutable
scan cache каждой ветки работает с `--cache-backend memory`: общий filesystem
cache Trivy использует BoltDB lock и не допускает параллельные процессы. Таким
образом vulnerability DB остаётся общей, а конфликтующий layer cache изолирован.

Прод-образы собираются с `docker build --pull`. Все три сервиса получают
`SECURITY_REFRESH`, который дополнительно инвалидирует слой обновления
системных пакетов, даже когда Dockerfile не менялся: backend и bot выполняют
`apt-get upgrade` (Debian), frontend — `apk upgrade` поверх
`nginx-unprivileged:alpine` (базовый образ отстаёт от alpine-репозитория, из-за
чего Trivy валил фиксабельные HIGH в `curl`/`libcurl`, напр. CVE-2026-5773 /
CVE-2026-6276). Это не даёт успешной сборке переиспользовать старый слой с уже
исправленными CVE.

Ключ этой инвалидации — **дата** (`date -u +%Y-%m-%d`, вычисляется в стадии
`Prepare`), а не `GIT_SHA`. На `GIT_SHA` слой пересобирался в каждом билде и
тянул за собой всё, что ниже него в Dockerfile: замеры билда #239 —
`apt-get update && upgrade && install` стоил **69.6с у backend** и **50.1с у
бота**, плюс переустановка `uv sync --extra essentia` и umap, хотя в системных
пакетах между двумя коммитами обычно не меняется ничего. Свежесть при этом не
теряется: гейт Trivy по-прежнему валит билд на фиксабельных HIGH/CRITICAL,
просто исправленный пакет приезжает с первым билдом суток, а не с первым
билдом после коммита. Нужно раньше — достаточно перезапустить джобу на
следующий день или временно передать другой `--build-arg`.

БД уязвимостей Trivy (~150+ МБ) кэшируется, а не качается заново на каждый билд:
для `trivy fs` — через `--mount=type=cache` прямо в `RUN` сборки `Dockerfile.trivy-fs`
(скан идёт во время `docker build`, не отдельным `docker run` после — так кэш
BuildKit реально переиспользуется между билдами); для `trivy image` — через
именованный volume `trivy-db-cache` (не трогается `docker image prune`,
живёт отдельно от образов).

Секреты и Dockerfile/IaC-анализ Trivy не делает — это уже покрыто встроенными
анализаторами Sonar (см. выше), дублировать не стали.

### HTML-отчёт

Раньше находки `trivy image` были видны только в консоли билда — приходилось
искать глазами по логу. Теперь на странице билда есть три отдельные вкладки
через `publishHTML` (плагин **HTML Publisher**, установлен на Jenkins вручную,
в репозитории не отражается): **Trivy: backend**, **Trivy: frontend**,
**Trivy: bot**.

Ключевое: образ разбирается **ровно один раз**, в JSON
(`trivy image --format json -o /report.json`), а всё остальное — это
`trivy convert` над готовым файлом:

| Что | Команда | Стоимость |
|---|---|---|
| таблица в консоль | `trivy convert /report.json` | доли секунды |
| HTML-вкладка | `trivy convert --format template --template "@contrib/html.tpl"` | доли секунды |
| блокирующий гейт | `trivy convert --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1` | доли секунды |

`convert` только переформатирует готовый отчёт: он не читает слои образа и не
обращается к БД уязвимостей (гейту поэтому не нужны ни `docker.sock`, ни volume
с БД — на входе только JSON, который заезжает в контейнер через `docker cp`).
Побочный плюс — гейт судит ровно по тем находкам, что попали в опубликованный
отчёт, а не по результату отдельного скана.

Раньше на каждый образ приходилось три полных скана (обычный + HTML + гейт).
Пока scan cache был общим, повторы были почти бесплатны, но `--cache-backend
memory` (см. выше, он обязателен для параллельных веток) отнял переиспользование
кэша — и каждый скан стал разбирать образ с нуля: на билде #239 только backend
стоил 30.6 + 24.0 + 22.7 = 77с из 80с всей стадии.

## Траблшутинг

| Симптом | Причина / фикс |
|---|---|
| `http: server gave HTTP response to HTTPS client` | нет `insecure-registries` для `192.168.1.41:5000` на хосте |
| push `:latest` не перезаписывается | в Nexus hosted-репо выключен *Allow redeploy* |
| `docker: command not found` в пайплайне | в Jenkins-контейнер не проброшен сокет / нет docker CLI |
| бинд-маунт музыки падает на `up` | `DISCOCS_MUSIC_DIR` в `.env` указывает на несуществующий путь |
