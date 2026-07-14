// CI/CD для discocs.
// Триггер: push в Gitea (http://192.168.1.41:3064/HS/discocs) -> вебхук -> Jenkins.
// Поток: тесты -> сборка 3 образов -> push в Nexus (docker-dev @ :5000) -> деплой по SSH на целевой хост.
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
  }

  stages {
    stage('Prepare') {
      steps {
        script {
          env.GIT_SHA = sh(script: 'git rev-parse --short HEAD', returnStdout: true).trim()
          // main для одиночного Pipeline-job (BRANCH_NAME пуст) или ветка main в multibranch
          env.IS_MAIN = (env.BRANCH_NAME == null || env.BRANCH_NAME == 'main') ? 'true' : 'false'
          echo "commit=${env.GIT_SHA} branch=${env.BRANCH_NAME ?: 'n/a'} deploy=${env.IS_MAIN}"
        }
      }
    }

    stage('Test') {
      steps {
        // BuildKit нужен для --mount=type=cache в Dockerfile.test (кэш pip
        // переживает инвалидацию слоя с исходниками между билдами).
        // Backend, бот и фронтенд тестируются отдельными образами — раньше
        // реально гонялся только backend, хотя тесты бота/фронта лежали в
        // репо и были прописаны в sonar.tests (баг, тесты никогда не запускались).
        sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.test -t discocs-test:${GIT_SHA} .'
        sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.bot-test -t discocs-bot-test:${GIT_SHA} .'
        sh 'DOCKER_BUILDKIT=1 docker build -f deploy/ci/Dockerfile.ui-test -t discocs-ui-test:${GIT_SHA} .'
        // create+start+cp вместо `docker run --rm`: агент собирает через хостовый
        // демон (docker-outside-of-docker), поэтому bind-mount воркспейса недоступен —
        // coverage/junit-отчёты достаём из уже остановленных контейнеров через `docker cp`.
        // set +e и ручной трекинг FAILED — иначе `set -e` прерывал бы скрипт на первом же
        // упавшем наборе тестов, и мы теряли бы coverage/junit именно тогда, когда они
        // нужнее всего (см. историю: билд #53 упал без единого извлечённого отчёта).
        sh '''
          set +e
          FAILED=0

          CID=$(docker create discocs-test:${GIT_SHA})
          docker start -a "$CID"
          [ $? -ne 0 ] && FAILED=1
          docker cp "$CID:/app/coverage.xml" coverage.xml
          docker cp "$CID:/app/junit-backend.xml" junit-backend.xml
          docker rm -f "$CID"

          CID=$(docker create discocs-bot-test:${GIT_SHA})
          docker start -a "$CID"
          [ $? -ne 0 ] && FAILED=1
          docker cp "$CID:/app/bot-coverage.xml" bot-coverage.xml
          docker cp "$CID:/app/junit-bot.xml" junit-bot.xml
          docker rm -f "$CID"

          CID=$(docker create discocs-ui-test:${GIT_SHA})
          docker start -a "$CID"
          [ $? -ne 0 ] && FAILED=1
          mkdir -p ui/coverage
          docker cp "$CID:/build/coverage/lcov.info" ui/coverage/lcov.info
          docker cp "$CID:/build/junit-ui.xml" junit-ui.xml
          docker rm -f "$CID"

          exit $FAILED
        '''
      }
      post {
        // always, не success — иначе результаты падающих тестов никогда бы не публиковались.
        always {
          junit 'junit-*.xml'
        }
      }
    }

    stage('Sonar') {
      steps {
        // Только отчёт, без waitForQualityGate — билд не блокируется внешним сервисом.
        // sonar_token — тот же credential, что уже рабочий в другой джобе.
        withCredentials([string(credentialsId: 'sonar_token', variable: 'SONAR_TOKEN')]) {
          sh 'sonar-scanner -Dsonar.host.url=http://192.168.1.41:9077 -Dsonar.login=$SONAR_TOKEN'
        }
      }
    }

    stage('Docker Login') {
      steps {
        // Плагин Docker Pipeline не установлен — используем голый docker CLI
        // (он уже доступен агенту, см. стадию Test), без глобальной переменной `docker`.
        // tank_nexus_user_pass — тот же credential, что уже рабочий в другой джобе для push в Nexus.
        withCredentials([usernamePassword(credentialsId: 'tank_nexus_user_pass', usernameVariable: 'NEXUS_USER', passwordVariable: 'NEXUS_PASS')]) {
          sh 'echo "$NEXUS_PASS" | docker login "$REGISTRY" -u "$NEXUS_USER" --password-stdin'
        }
      }
    }

    stage('Build & Push') {
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
              def img = "${REGISTRY}/${IMAGE_NS}/${svc.name}"
              // BuildKit нужен backend/bot Dockerfile'ам — --mount=type=cache для uv.
              // --pull обновляет mutable base image. SECURITY_REFRESH сбрасывает
              // слой обновления системных пакетов даже когда Dockerfile не менялся:
              // apt-get upgrade у backend/bot, apk upgrade у frontend (иначе Trivy
              // валит фиксабельные HIGH в curl/libcurl базового nginx-образа).
              def refreshArg = svc.refresh ? "--build-arg SECURITY_REFRESH=${GIT_SHA}" : ""
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

    stage('Security Scan') {
      steps {
        // Trivy всегда публикует полный отчёт, затем валит билд только при
        // исправимых HIGH/CRITICAL (--ignore-unfixed оставляет видимыми CVE,
        // для которых upstream пока не выпустил обновление).
        // 1) CVE в зависимостях (requirements/pnpm-lock) — скан идёт прямо в
        //    RUN сборки Dockerfile.trivy-fs (см. комментарий там), вывод виден
        //    в логе этой стадии как обычный docker build output.
        // 2) CVE в уже собранных образах — сокет докера, а не воркспейс,
        //    поэтому -v тут работает (см. Docker Login/Build & Push выше).
        //    Именованный volume trivy-db-cache — та же причина, что в
        //    Dockerfile.trivy-fs: не качать ~150+ МБ БД заново на каждый образ.
        sh 'DOCKER_BUILDKIT=1 docker build --progress=plain -f deploy/ci/Dockerfile.trivy-fs -t discocs-trivy-fs:${GIT_SHA} .'
        sh '''
          set -e
          for svc in backend frontend bot; do
            docker run --rm \
              -v /var/run/docker.sock:/var/run/docker.sock \
              -v trivy-db-cache:/root/.cache/trivy \
              aquasec/trivy image --exit-code 0 ${REGISTRY}/${IMAGE_NS}/${svc}:${GIT_SHA}

            # HTML-версия того же скана — для вкладки на билде (publishHTML ниже),
            # чтобы не искать находки по консоли. create+cp вместо `docker run
            # -v <файл>`: воркспейс агента недоступен хостовому демону как путь
            # (docker-outside-of-docker, та же причина, что везде в этом файле).
            CID=$(docker create \
              -v /var/run/docker.sock:/var/run/docker.sock \
              -v trivy-db-cache:/root/.cache/trivy \
              aquasec/trivy image --exit-code 0 \
              --format template --template "@contrib/html.tpl" \
              -o "/trivy-${svc}.html" ${REGISTRY}/${IMAGE_NS}/${svc}:${GIT_SHA})
            docker start -a "$CID"
            docker cp "$CID:/trivy-${svc}.html" "trivy-${svc}.html"
            docker rm -f "$CID"
          done
        '''
        publishHTML(target: [
          allowMissing: false,
          alwaysLinkToLastBuild: true,
          keepAll: true,
          reportDir: '.',
          reportFiles: 'trivy-backend.html',
          reportName: 'Trivy: backend',
        ])
        publishHTML(target: [
          allowMissing: false,
          alwaysLinkToLastBuild: true,
          keepAll: true,
          reportDir: '.',
          reportFiles: 'trivy-frontend.html',
          reportName: 'Trivy: frontend',
        ])
        publishHTML(target: [
          allowMissing: false,
          alwaysLinkToLastBuild: true,
          keepAll: true,
          reportDir: '.',
          reportFiles: 'trivy-bot.html',
          reportName: 'Trivy: bot',
        ])
        sh '''
          set -e
          for svc in backend frontend bot; do
            docker run --rm \
              -v /var/run/docker.sock:/var/run/docker.sock \
              -v trivy-db-cache:/root/.cache/trivy \
              aquasec/trivy image --ignore-unfixed \
              --severity HIGH,CRITICAL --exit-code 1 \
              ${REGISTRY}/${IMAGE_NS}/${svc}:${GIT_SHA}
          done
        '''
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
