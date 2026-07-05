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
//   - discocs_prod_env     (Secret file)               — прод .env с секретами (см. deploy/prod/.env.example)
//   - HS_SSH_KEY           (SSH Username with private key) — деплой-ключ на TARGET_SERVER

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
        sh 'docker build -f deploy/ci/Dockerfile.test -t discocs-test:${GIT_SHA} .'
        sh 'docker run --rm discocs-test:${GIT_SHA}'
      }
    }

    // --- ЗАДЕЛ под SonarQube (включить позже, см. docs/cicd.md) ---
    // stage('Sonar') {
    //   steps {
    //     withSonarQubeEnv('sonar') {
    //       sh 'sonar-scanner -Dsonar.projectKey=discocs -Dsonar.sources=app'
    //     }
    //   }
    // }

    stage('Build & Push') {
      steps {
        // Плагин Docker Pipeline не установлен — используем голый docker CLI
        // (он уже доступен агенту, см. стадию Test), без глобальной переменной `docker`.
        // tank_nexus_user_pass — тот же credential, что уже рабочий в другой джобе для push в Nexus.
        withCredentials([usernamePassword(credentialsId: 'tank_nexus_user_pass', usernameVariable: 'NEXUS_USER', passwordVariable: 'NEXUS_PASS')]) {
          sh 'echo "$NEXUS_PASS" | docker login "$REGISTRY" -u "$NEXUS_USER" --password-stdin'
          script {
            def services = [
              [name: 'backend',  df: 'deploy/backend/Dockerfile'],
              [name: 'frontend', df: 'deploy/nginx/Dockerfile'],
              [name: 'bot',      df: 'deploy/bot/Dockerfile'],
            ]
            for (svc in services) {
              def img = "${REGISTRY}/${IMAGE_NS}/${svc.name}"
              sh "docker build -f ${svc.df} -t ${img}:${GIT_SHA} ."
              sh "docker push ${img}:${GIT_SHA}"             // :<git-sha> — неизменяемый, для отката
              if (env.IS_MAIN == 'true') {
                sh "docker tag ${img}:${GIT_SHA} ${img}:latest"
                sh "docker push ${img}:latest"               // :latest — перезапись, только с main
              }
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
          // Деплой по SSH на целевой хост: заливаем актуальный compose+.env
          // в TARGET_DIR (git — источник правды) и там же pull/up.
          withCredentials([file(credentialsId: 'discocs_prod_env', variable: 'PROD_ENV')]) {
            sshagent(['HS_SSH_KEY']) {
              sh '''
                set -e
                scp -P "$TARGET_PORT" -o StrictHostKeyChecking=no deploy/prod/docker-compose.yml "$TARGET_USER@$TARGET_SERVER:$TARGET_DIR/docker-compose.yml"
                scp -P "$TARGET_PORT" -o StrictHostKeyChecking=no "$PROD_ENV" "$TARGET_USER@$TARGET_SERVER:$TARGET_DIR/.env"
                ssh -p "$TARGET_PORT" -o StrictHostKeyChecking=no "$TARGET_USER@$TARGET_SERVER" '
                  set -e
                  cd '"$TARGET_DIR"'
                  docker compose -p discocs --env-file .env pull
                  docker compose -p discocs --env-file .env up -d --force-recreate --remove-orphans
                  docker image prune -f
                '
              '''
            }
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
  }
}
