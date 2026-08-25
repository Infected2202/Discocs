// CI/CD для discocs.
// Триггер: push в Gitea (http://192.168.1.41:3064/HS/discocs) -> вебхук -> Jenkins.
// Поток: проверки (тесты backend/бот/фронт + CVE в зависимостях — параллельно)
//        -> сборка 3 образов + push в Nexus (docker-dev @ :5000)
//        -> Sonar || Trivy по образам (параллельно)
//        -> деплой по SSH на целевой хост.
//
// Android (Capacitor APK + OTA web-бандл, см. docs/android-app.md) — часть
// ветки 'frontend' в стадии Images, не отдельная джоба: собирается на каждый
// пуш вместе с фронтом и запекается в тот же frontend-образ
// (deploy/nginx/downloads/, см. deploy/nginx/Dockerfile). Раньше был отдельный
// ручной Jenkinsfile.android — убран: раздельные джобы, обе собирающие один
// и тот же frontend-образ, означали, что следующий обычный пуш через основной
// пайплайн пересобрал бы frontend с пустым deploy/nginx/downloads/ (не в
// гите) и тихо стёр бы опубликованный APK/OTA с прода.
//
// Требования к Jenkins-агенту (контейнер):
//   - смонтирован /var/run/docker.sock (сборка/пуш идут через хостовый демон)
//   - установлены docker CLI
//   - на хосте в /etc/docker/daemon.json уже прописан insecure-registries: ["192.168.1.41:5000"]
//
// Credentials в Jenkins:
//   - tank_nexus_user_pass (Username/Password)         — логин в Nexus для push (общий для всех джоб)
//   - HS_SSH_KEY           (SSH Username with private key) — деплой-ключ на TARGET_SERVER
//   - sonar_token          (Secret text)                — токен для sonar-scanner (общий для всех джоб)
//
// .env на TARGET_SERVER (TARGET_DIR/.env) — заводится и правится вручную на хосте,
// CI его не трогает и не деплоит (см. deploy/prod/.env.example для списка переменных).

pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  environment {
    REGISTRY      = '192.168.1.41:5000'
    IMAGE_NS      = 'discocs'
    TARGET_SERVER = '192.168.1.41'
    TARGET_USER   = 'infected2202'
    TARGET_PORT   = '2252'
    TARGET_DIR    = '/home/infected2202/docker/discocs'
    // Python + TS + Docker analysis can exceed the scanner JRE default heap.
    SONAR_SCANNER_JAVA_OPTS = '-Xmx2g'
    // Публичный домен приложения — не секрет (виден в браузере), но не
    // вычисляется автоматически. Запекается в APK на этапе сборки как
    // WebView-origin (server.hostname в ui/capacitor.config.ts), см.
    // docs/android-app.md.
    DISCOCS_PUBLIC_URL = 'https://d.plikinson.org/'
  }

  stages {
    stage('Prepare') {
      steps {
        script {
          env.GIT_SHA = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
          // main для одиночного Pipeline-job (BRANCH_NAME пуст) или ветка main в multibranch
          env.IS_MAIN = (env.BRANCH_NAME == null || env.BRANCH_NAME == 'main') ? 'true' : 'false'
          // Ключ инвалидации слоя системных обновлений — сутки, а не коммит.
          // На GIT_SHA слой `apt-get update && apt-get upgrade` пересобирался
          // в каждом билде и тянул за собой всё, что ниже (uv sync с essentia,
          // umap): замерено на билде #239 — 69.6с у backend и 50.1с у бота,
          // при том что в системных пакетах между коммитами обычно ничего
          // не меняется. Свежесть при этом не теряется: гейт Trivy по-прежнему
          // валит фиксабельные HIGH/CRITICAL, просто новый пакет приезжает
          // с первым билдом суток, а не с первым билдом после коммита.
          env.SECURITY_REFRESH = sh(script: 'date -u +%Y-%m-%d', returnStdout: true).trim()
          echo "commit=${env.GIT_SHA} branch=${env.BRANCH_NAME ?: 'n/a'} deploy=${env.IS_MAIN} security_refresh=${env.SECURITY_REFRESH}"
        }
      }
    }

    // Четыре независимые проверки, раньше стоившие сумму своих длительностей:
    // тесты backend/бота/фронта ничего не знают друг о друге, а trivy fs
    // читает только lock-файлы из воркспейса. Теперь длительность стадии —
    // максимум из четырёх, а не сумма.
    // failFast намеренно не включён: упавший backend не должен обрывать
    // остальные ветки, иначе снова потеряем junit/coverage тех наборов,
    // которые уже почти доехали (см. историю: билд #53).
    // Все ветки живут в одном воркспейсе (agent наследуется, отдельных node
    // нет), поэтому coverage-/junit-файлы видны следующим стадиям как раньше.
    stage('Checks') {
      parallel {
        stage('Tests: backend') {
          steps {
            // BuildKit нужен для --mount=type=cache в Dockerfile.test (кэш uv
            // переживает инвалидацию слоя с исходниками между билдами).
            sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.test -t discocs-test:${GIT_SHA} .'
            // create+start+cp вместо `docker run --rm`: агент собирает через хостовый
            // демон (docker-outside-of-docker), поэтому bind-mount воркспейса недоступен —
            // coverage/junit-отчёты достаём из уже остановленных контейнеров через `docker cp`.
            // set +e и ручной RC — иначе `set -e` прервал бы скрипт на упавшем прогоне,
            // и мы потеряли бы coverage/junit именно тогда, когда они нужнее всего.
            sh '''
              set +e
              CID=$(docker create discocs-test:${GIT_SHA})
              docker start -a "$CID"
              RC=$?
              docker cp "$CID:/app/coverage.xml" coverage.xml
              docker cp "$CID:/app/junit-backend.xml" junit-backend.xml
              docker rm -f "$CID"
              exit $RC
            '''
          }
          post {
            // always, не success — иначе результаты падающих тестов никогда бы не публиковались.
            always { junit 'junit-backend.xml' }
          }
        }

        stage('Tests: bot') {
          steps {
            sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.bot-test -t discocs-bot-test:${GIT_SHA} .'
            sh '''
              set +e
              CID=$(docker create discocs-bot-test:${GIT_SHA})
              docker start -a "$CID"
              RC=$?
              docker cp "$CID:/app/bot-coverage.xml" bot-coverage.xml
              docker cp "$CID:/app/junit-bot.xml" junit-bot.xml
              docker rm -f "$CID"
              exit $RC
            '''
          }
          post {
            always { junit 'junit-bot.xml' }
          }
        }

        stage('Tests: ui') {
          steps {
            sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.ui-test -t discocs-ui-test:${GIT_SHA} .'
            sh '''
              set +e
              CID=$(docker create discocs-ui-test:${GIT_SHA})
              docker start -a "$CID"
              RC=$?
              mkdir -p ui/coverage
              docker cp "$CID:/build/coverage/lcov.info" ui/coverage/lcov.info
              docker cp "$CID:/build/junit-ui.xml" junit-ui.xml
              docker rm -f "$CID"
              exit $RC
            '''
          }
          post {
            always { junit 'junit-ui.xml' }
          }
        }

        stage('Deps CVE') {
          steps {
            // CVE в зависимостях (uv.lock/pnpm-lock) — скан идёт прямо в RUN
            // сборки Dockerfile.trivy-fs (см. комментарий там), вывод виден
            // в логе ветки как обычный docker build output. Собранные образы
            // ему не нужны, поэтому ветка идёт вместе с тестами, а не после сборки.
            sh 'DOCKER_BUILDKIT=1 docker build --progress=plain -f deploy/ci/Dockerfile.trivy-fs -t discocs-trivy-fs:${GIT_SHA} .'
          }
        }
      }
    }

    stage('Build & Push') {
      stages {
        stage('Docker Login') {
          steps {
            // Плагин Docker Pipeline не установлен — используем голый docker CLI
            // (он уже доступен агенту, см. стадию Checks), без глобальной переменной `docker`.
            // tank_nexus_user_pass — тот же credential, что уже рабочий в другой джобе для push в Nexus.
            withCredentials([usernamePassword(credentialsId: 'tank_nexus_user_pass', usernameVariable: 'NEXUS_USER', passwordVariable: 'NEXUS_PASS')]) {
              sh 'echo "$NEXUS_PASS" | docker login "$REGISTRY" -u "$NEXUS_USER" --password-stdin'
            }
          }
        }

        stage('Images') {
          steps {
            script {
              def services = [
                [name: 'backend',  df: 'deploy/backend/Dockerfile', refresh: true],
                [name: 'frontend', df: 'deploy/nginx/Dockerfile', refresh: true],
                [name: 'bot',      df: 'deploy/bot/Dockerfile', refresh: true],
              ]
              // Каждый сервис — своя ветка parallel, поэтому в stage view/Blue Ocean
              // они видны раздельно, а не одной сплошной стадией.
              parallel(services.collectEntries { svc ->
                [(svc.name): {
                  if (svc.name == 'frontend') {
                    // Android APK + OTA web-бандл должны лежать в
                    // deploy/nginx/downloads/ ДО сборки frontend-образа — он
                    // их туда COPY'ит (deploy/nginx/Dockerfile). Поэтому это
                    // внутри ветки frontend, последовательно перед её же
                    // docker build, а не отдельная parallel-ветка: backend/bot
                    // не должны ждать Android, а frontend обязан. Сам
                    // discocs-android образ никуда не пушится — он существует
                    // только для извлечения трёх файлов через docker cp
                    // (тот же паттерн create+cp, что и в Checks — воркспейс
                    // агента недоступен хостовому демону как bind-mount).
                    // Single-quoted Groovy strings (как и весь остальной файл) —
                    // ${VAR}/$VAR ниже раскрывает не Groovy, а сам bash, из
                    // переменных окружения, которые Jenkins уже экспортировал
                    // из environment{}/env.GIT_SHA в процесс sh.
                    sh '''
                      DOCKER_BUILDKIT=1 docker build \
                        --build-arg DISCOCS_PUBLIC_URL="$DISCOCS_PUBLIC_URL" \
                        --build-arg GIT_SHA=${GIT_SHA} \
                        --build-arg ANDROID_VERSION_CODE=${BUILD_NUMBER} \
                        --build-arg ANDROID_VERSION_NAME=${GIT_SHA} \
                        -f deploy/ci/Dockerfile.android -t discocs-android:${GIT_SHA} .
                    '''
                    sh '''
                      set -e
                      mkdir -p deploy/nginx/downloads
                      CID=$(docker create discocs-android:${GIT_SHA})
                      docker cp "$CID:/build/output/discocs.apk" deploy/nginx/downloads/discocs.apk
                      docker cp "$CID:/build/output/discocs-web-${GIT_SHA}.zip" "deploy/nginx/downloads/discocs-web-${GIT_SHA}.zip"
                      docker cp "$CID:/build/output/update-manifest.json" deploy/nginx/downloads/update-manifest.json
                      docker rm -f "$CID"
                    '''
                  }
                  def img = "${REGISTRY}/${IMAGE_NS}/${svc.name}"
                  // BuildKit нужен backend/bot Dockerfile'ам — --mount=type=cache для uv.
                  // --pull обновляет mutable base image. SECURITY_REFRESH сбрасывает
                  // слой обновления системных пакетов даже когда Dockerfile не менялся:
                  // apt-get upgrade у backend/bot, apk upgrade у frontend (иначе Trivy
                  // валит фиксабельные HIGH в curl/libcurl базового nginx-образа).
                  // Ключ — дата, а не коммит (см. Prepare).
                  def refreshArg = svc.refresh ? "--build-arg SECURITY_REFRESH=${SECURITY_REFRESH}" : ""
                  sh "DOCKER_BUILDKIT=1 docker build --pull ${refreshArg} -f ${svc.df} -t ${img}:${GIT_SHA} ."
                  sh "docker push ${img}:${GIT_SHA}"             // :<git-sha> — неизменяемый, для отката
                  if (env.IS_MAIN == 'true') {
                    sh "docker tag ${img}:${GIT_SHA} ${img}:latest"
                    sh "docker push ${img}:latest"               // :latest — перезапись, только с main
                  }
                }]
              })
            }
          }
        }
      }
    }

    // Sonar и Trivy друг от друга не зависят: сканеру кода нужны coverage-отчёты
    // из Checks, Trivy — образы из Build & Push. Раньше Sonar шёл параллельно
    // сборке, но после ускорения сборки (131 -> 22.8с на билде #248) прятать
    // под ней 52-секундный Sonar стало нечем — он держал стадию один. Теперь
    // он перекрывается сканом образов, который длится дольше него.
    stage('Analyze & Scan') {
      parallel {
        stage('Sonar') {
          steps {
            // Только отчёт, без waitForQualityGate — билд не блокируется внешним сервисом.
            // sonar_token — тот же credential, что уже рабочий в другой джобе.
            withCredentials([string(credentialsId: 'sonar_token', variable: 'SONAR_TOKEN')]) {
              sh 'sonar-scanner -Dsonar.host.url=http://192.168.1.41:9077 -Dsonar.login=$SONAR_TOKEN'
            }
          }
        }

        stage('Security Scan') {
          steps {
            script {
              // CVE в уже собранных образах — сокет докера, а не воркспейс,
              // поэтому -v тут работает (см. Docker Login/Images выше).
              // Именованный volume trivy-db-cache — та же причина, что в
              // Dockerfile.trivy-fs: не качать ~150+ МБ БД заново на каждый образ.
              //
              // БД обновляем ровно один раз, до fan-out, и дальше все сканы идут
              // с --skip-db-update: три ветки, стартующие одновременно на общем
              // volume, иначе полезли бы качать/распаковывать БД конкурентно в один
              // и тот же каталог.
              sh 'docker run --rm -v trivy-db-cache:/root/.cache/trivy aquasec/trivy image --download-db-only'
              // Ветка на сервис. Отчётная часть (таблица в консоль + HTML-вкладка)
              // берётся из ОДНОГО разбора образа: скан пишет JSON, а обе формы
              // делает `trivy convert` — переформатирование готового отчёта, без
              // повторного чтения слоёв и без обращения к БД.
              // Раньше на каждый образ приходилось три полных скана, и с
              // --cache-backend memory (он нужен: общий fs-кэш на трёх параллельных
              // ветках даёт "Failed to acquire cache or database lock", билд #238)
              // каждый из них начинал с нуля — только backend стоил 30.6+24.0+22.7с
              // на билде #239. Гейт остаётся отдельным сканом, см. комментарий ниже.
              def mounts = '-v /var/run/docker.sock:/var/run/docker.sock -v trivy-db-cache:/root/.cache/trivy'
              parallel(['backend', 'frontend', 'bot'].collectEntries { svc ->
                [(svc): {
                  def img = "${REGISTRY}/${IMAGE_NS}/${svc}:${GIT_SHA}"
                  def scan = "trivy image --skip-db-update --cache-backend memory --format json -o /report.json ${img}"
                  def html = 'trivy convert --format template --template "@contrib/html.tpl" -o /report.html /report.json'
                  // create+cp вместо `docker run -v <файл>`: воркспейс агента
                  // недоступен хостовому демону как путь (docker-outside-of-docker,
                  // та же причина, что везде в этом файле).
                  sh """
                    set -e
                    mkdir -p trivy-reports/${svc}
                    CID=\$(docker create ${mounts} --entrypoint sh aquasec/trivy -c '${scan} && ${html} && trivy convert /report.json')
                    docker start -a "\$CID"
                    docker cp "\$CID:/report.json" "trivy-${svc}.json"
                    docker cp "\$CID:/report.html" "trivy-reports/${svc}/trivy-${svc}.html"
                    docker rm -f "\$CID"
                  """
                  // Отчёт лежит в собственном каталоге, а не в корне воркспейса:
                  // с reportDir '.' HTML Publisher рекурсивно тарит ВЕСЬ воркспейс
                  // (node_modules, .venv, .scannerwork). На билде #383 это упало
                  // с FATAL NoSuchFileException — Sonar из соседней ветки удалил
                  // свой .sonartmp прямо во время обхода, и билд стал FAILURE
                  // после успеха всех стадий.
                  publishHTML(target: [
                    allowMissing: false,
                    alwaysLinkToLastBuild: true,
                    keepAll: true,
                    reportDir: "trivy-reports/${svc}",
                    reportFiles: "trivy-${svc}.html",
                    reportName: "Trivy: ${svc}",
                  ])
                  // Гейт — строго после публикации отчёта: падение на HIGH/CRITICAL
                  // не должно лишать нас HTML-вкладки с находками.
                  //
                  // Это отдельный скан, а не `convert` над уже готовым JSON:
                  // у `convert` из фильтров только --severity/--exit-code/
                  // --ignore-policy, флага --ignore-unfixed у него нет (проверено
                  // на билде #247: "unknown flag: --ignore-unfixed"). Отфильтровать
                  // unfixed через Rego-политику технически можно, но ошибка в ней
                  // даёт молча пропускающий гейт — для проверки безопасности это
                  // худший вид отказа, поэтому здесь родной флаг и лишние ~20с.
                  //
                  // .trivyignore.yaml переносим в контейнер через docker cp по той
                  // же причине, что и отчёты: воркспейс агента недоступен хостовому
                  // демону как путь. Файл получает только гейт — в HTML-отчёте пусть
                  // остаются видны все находки, включая заигноренные.
                  sh """
                    set -e
                    CID=\$(docker create ${mounts} aquasec/trivy image --skip-db-update --cache-backend memory --ignore-unfixed --severity HIGH,CRITICAL --exit-code 1 --ignorefile /.trivyignore.yaml ${img})
                    docker cp .trivyignore.yaml "\$CID:/.trivyignore.yaml"
                    STATUS=0
                    docker start -a "\$CID" || STATUS=\$?
                    docker rm -f "\$CID" >/dev/null
                    exit \$STATUS
                  """
                }]
              })
            }
          }
        }
      }
    }
  }

  post {
    success {
      script {
        if (env.IS_MAIN == 'true') {
          // Деплой по SSH на целевой хост: заливаем актуальный compose-файл
          // в TARGET_DIR (git — источник правды) и там же pull/up.
          // .env на хосте — не наше дело: заводится и правится вручную,
          // CI его не перезаписывает (см. комментарий в шапке файла).
          // --wait ждёт, пока docker healthcheck'и (уже описаны в
          // deploy/prod/docker-compose.yml) не подтвердят healthy — раньше
          // `up -d` считался успехом сразу после старта контейнера, падение
          // через несколько секунд после деплоя было бы не видно в CI.
          sshagent(['HS_SSH_KEY']) {
            sh '''
              set -e
              scp -P "$TARGET_PORT" -o StrictHostKeyChecking=no deploy/prod/docker-compose.yml "$TARGET_USER@$TARGET_SERVER:$TARGET_DIR/docker-compose.yml"
              ssh -p "$TARGET_PORT" -o StrictHostKeyChecking=no "$TARGET_USER@$TARGET_SERVER" '
                set -e
                cd '"$TARGET_DIR"'
                TAG=latest docker compose -p discocs --env-file .env pull
                TAG=latest docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans --wait --wait-timeout 120
                docker image prune -f
              '
            '''
          }
          echo "Deployed discocs @ ${env.GIT_SHA}"
        } else {
          echo "Ветка ${env.BRANCH_NAME} — образы собраны и запушены по :sha, деплой пропущен"
        }
      }
    }
    failure {
      echo 'Сборка упала — прод не тронут'
    }
    always {
      // Агент — постоянный LXC, не эфемерный воркер: без чистки образы
      // (тестовые, security-scan, прод-теги по GIT_SHA) копятся бесконечно
      // и рано или поздно забьют диск. until=48h — оставляет свежие билды
      // под рукой для дебага, но не даёт расти без ограничения.
      sh 'docker image prune -a -f --filter "until=48h" || true'
    }
  }
}
