# CI/CD

Автосборка и деплой на домашний сервер `192.168.1.41`. Вся инфраструктура (Jenkins,
Nexus, Gitea, целевой хост) — одна машина `.41`.

## Поток

```
push в Gitea ──webhook──> Jenkins
   └─ Test         прогон pytest -n auto в контейнере python:3.11 (deploy/ci/Dockerfile.test),
   │                 сборка образа через BuildKit (--mount=type=cache для pip)
   └─ Sonar        sonar-scanner — отчёт в SonarQube, без Quality Gate (билд не блокируется)
   └─ Build&Push   сборка backend/frontend/bot → Nexus (docker-dev @ :5000)
   │                 теги:  :<git-sha>  (всегда)   +  :latest  (только с main)
   └─ Deploy        [post/success, только main] по SSH на TARGET_SERVER:
                       scp compose в TARGET_DIR → docker compose pull && up -d --force-recreate
                       (.env на хосте CI не трогает — заводится и правится вручную)
```

Файлы:

| Файл | Роль |
|---|---|
| `Jenkinsfile` | описание пайплайна |
| `deploy/ci/Dockerfile.test` | образ для прогона тестов в CI |
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
CI его не перезаписывает) и там же гоняет `pull` + `up -d --force-recreate`. Вручную на хосте:

```bash
cd /home/infected2202/docker/discocs   # TARGET_DIR, там же лежит .env
docker compose -p discocs --env-file .env pull
docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans
```

**Откат** на конкретный билд — поставь его sha в `.env` и повтори up:

```bash
TAG=a1b2c3d   # значение из тега образа / номера коммита
# правишь TAG в TARGET_DIR/.env на сервере, затем:
docker compose -p discocs --env-file .env up -d --force-recreate
```

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
| `8711` | backend | старая админка (`/admin`) + API напрямую, для внутреннего стенда опубликован |

`backend` порт публикуется намеренно (`ports: ["8711:7752"]`) — площадка внутренняя,
доступа снаружи LAN нет, поэтому нет смысла прятать API за одним только nginx.

## SonarQube

Стадия `Sonar` в `Jenkinsfile` — после `Test`, перед `Build & Push`. Гоняет
`sonar-scanner` (см. `sonar-project.properties`) против сервера
`http://192.168.1.41:9077` под токеном `sonar_token`. Это только отчёт —
`waitForQualityGate` не используется, результат анализа не может завалить
билд, смотреть метрики (code smells, дублирование, security hotspots) —
в вебке Sonar по `projectKey=discocs`. Если качество анализа окажется
полезным, можно добавить `waitForQualityGate` (потребует настроить вебхук
SonarQube → Jenkins).

Покрыты все три части кодовой базы: `sonar.sources=app,discocs_bot/bot,ui/src`
(бэкенд, бот, фронтенд), `sonar.tests` — соответствующие каталоги тестов.
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

Python-coverage подключён только для `app` (backend): стадия `Test` гоняет
`pytest --cov=app --cov-report=xml` внутри `discocs-test`, `coverage.xml`
достаётся из контейнера через `docker cp` (агент — docker-outside-of-docker,
bind-mount воркспейса недоступен хостовому демону) и подхватывается
`sonar.python.coverage.reportPaths=coverage.xml`. Бот (`discocs_bot`) и
фронтенд (`ui`) coverage в Sonar не отдают — у бота свой `pytest`-прогон вне
`Dockerfile.test`, у фронтенда `ui/package.json`'s `test` — это `node --test`,
а не `vitest` (хотя `@vitest/coverage-v8` стоит в `devDependencies`), так что
честного JS-coverage сейчас нет в принципе — отдельная задача, если понадобится.

## Траблшутинг

| Симптом | Причина / фикс |
|---|---|
| `http: server gave HTTP response to HTTPS client` | нет `insecure-registries` для `192.168.1.41:5000` на хосте |
| push `:latest` не перезаписывается | в Nexus hosted-репо выключен *Allow redeploy* |
| `docker: command not found` в пайплайне | в Jenkins-контейнер не проброшен сокет / нет docker CLI |
| бинд-маунт музыки падает на `up` | `DISCOCS_MUSIC_DIR` в `.env` указывает на несуществующий путь |
