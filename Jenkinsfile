// CI/CD для discocs.
// Триггер: push в Gitea (http://192.168.1.41:3064/HS/discocs) -> вебхук -> Jenkins.
// Поток: тесты -> сборка 3 образов -> push в Nexus (docker-dev @ :5000) -> локальный compose up.
//
// Требования к Jenkins-агенту (контейнер):
//   - смонтирован /var/run/docker.sock (сборка/пуш/compose идут через хостовый демон)
//   - установлены docker CLI и docker compose plugin
//   - на хосте в /etc/docker/daemon.json уже прописан insecure-registries: ["192.168.1.41:5000"]
//
// Credentials в Jenkins:
//   - nexus_token       (Username/Password) — логин в Nexus для push
//   - discocs_prod_env  (Secret file)       — прод .env с секретами (см. deploy/prod/.env.example)

pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '20'))
  }

  environment {
    REGISTRY = '192.168.1.41:5000'
    IMAGE_NS = 'discocs'
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
        withCredentials([usernamePassword(credentialsId: 'nexus_token', usernameVariable: 'NEXUS_USER', passwordVariable: 'NEXUS_PASS')]) {
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
          // Деплой на том же хосте: Jenkins-контейнер говорит с хостовым docker через сокет.
          withCredentials([file(credentialsId: 'discocs_prod_env', variable: 'PROD_ENV')]) {
            sh '''
              docker compose -p discocs -f deploy/prod/docker-compose.yml --env-file "$PROD_ENV" pull
              docker compose -p discocs -f deploy/prod/docker-compose.yml --env-file "$PROD_ENV" up -d --remove-orphans
              docker image prune -f
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
  }
}
