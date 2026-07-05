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
        // coverage-отчёты достаём из уже остановленных контейнеров через `docker cp`.
        sh '''
          set -e
          CID=$(docker create discocs-test:${GIT_SHA})
          docker start -a "$CID"
          docker cp "$CID:/app/coverage.xml" coverage.xml
          docker rm -f "$CID"

          CID=$(docker create discocs-bot-test:${GIT_SHA})
          docker start -a "$CID"
          docker cp "$CID:/app/bot-coverage.xml" bot-coverage.xml
          docker rm -f "$CID"

          CID=$(docker create discocs-ui-test:${GIT_SHA})
          docker start -a "$CID"
          mkdir -p ui/coverage
          docker cp "$CID:/build/coverage/lcov.info" ui/coverage/lcov.info
          docker rm -f "$CID"
        '''
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
            [name: 'backend',  df: 'deploy/backend/Dockerfile'],
            [name: 'frontend', df: 'deploy/nginx/Dockerfile'],
            [name: 'bot',      df: 'deploy/bot/Dockerfile'],
          ]
          // Каждый сервис — своя ветка parallel, поэтому в stage view/Blue Ocean
          // они видны раздельно, а не одной сплошной стадией.
          parallel(services.collectEntries { svc ->
            [(svc.name): {
              def img = "${REGISTRY}/${IMAGE_NS}/${svc.name}"
              // BuildKit нужен backend/bot Dockerfile'ам — --mount=type=cache для uv.
              sh "DOCKER_BUILDKIT=1 docker build -f ${svc.df} -t ${img}:${GIT_SHA} ."
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
        // Trivy, report-only (--exit-code 0 — никогда не валит билд):
        // 1) CVE в зависимостях (requirements/pnpm-lock) — скан идёт прямо в
        //    RUN сборки Dockerfile.trivy-fs (см. комментарий там), вывод виден
        //    в логе этой стадии как обычный docker build output.
        // 2) CVE в уже собранных образах — сокет докера, а не воркспейс,
        //    поэтому -v тут работает (см. Docker Login/Build & Push выше).
        //    Именованный volume trivy-db-cache — та же причина, что в
        //    Dockerfile.trivy-fs: не качать ~150+ МБ БД заново на каждый образ.
        sh 'DOCKER_BUILDKIT=1 docker build --progress=plain -f deploy/ci/Dockerfile.trivy-fs -t discocs-trivy-fs:${GIT_SHA} .'
        sh '''
          for svc in backend frontend bot; do
            docker run --rm \
              -v /var/run/docker.sock:/var/run/docker.sock \
              -v trivy-db-cache:/root/.cache/trivy \
              aquasec/trivy image --exit-code 0 ${REGISTRY}/${IMAGE_NS}/${svc}:${GIT_SHA}
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
                docker compose -p discocs --env-file .env pull
                docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans --wait --wait-timeout 120
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
