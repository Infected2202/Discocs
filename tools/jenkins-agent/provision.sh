#!/usr/bin/env bash
# Готовит WSL-дистрибутив jenkins-agent: свой Docker Engine, sonar-scanner и
# inbound-агент Jenkins как systemd-сервис. Запускается от root из setup.ps1
# (скрипт приходит через stdin — диски Windows в дистрибутиве отключены),
# повторный запуск безопасен: обновляет конфиги и пакеты, ничего не ломает.
set -euo pipefail

JENKINS_URL='http://192.168.1.41:8077/'
NODE_NAME='pc'
REGISTRY='192.168.1.41:5000'
SONAR_SCANNER_VERSION='7.2.0.5079'
AGENT_HOME='/var/lib/jenkins-agent'

export DEBIAN_FRONTEND=noninteractive

# Изоляция от Windows: без дисков C:/D: (automount) и без запуска .exe (interop),
# агент видит только этот дистрибутив и его dockerd. systemd — для docker и агента.
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true

[automount]
enabled=false
mountFsTab=false

[interop]
enabled=false
appendWindowsPath=false

[user]
default=root
EOF

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl git openssh-client unzip openjdk-21-jre-headless

# Docker Engine из официального репозитория. buildx обязателен: пайплайн собирает
# с DOCKER_BUILDKIT=1, без плагина docker build падает сразу.
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
. /etc/os-release
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
apt-get upgrade -y

# Подсети явно: дефолтные 172.17+/192.168.x пересекаются с NAT-сетью WSL и
# домашней 192.168.1.0/24. GC BuildKit-кэша — иначе VHDX дистрибутива растёт без
# предела (post/always в Jenkinsfile чистит только образы, не кэш сборки).
install -d /etc/docker
cat > /etc/docker/daemon.json <<EOF
{
  "insecure-registries": ["${REGISTRY}"],
  "bip": "10.230.0.1/24",
  "default-address-pools": [{ "base": "10.231.0.0/16", "size": 24 }],
  "builder": { "gc": { "enabled": true, "defaultKeepStorage": "60GB" } },
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF

# sonar-scanner: zip linux-x64 со своим JRE, ставим рядом по версии и ссылкой в PATH.
SONAR_DIR="/opt/sonar-scanner-${SONAR_SCANNER_VERSION}"
if [ ! -x "${SONAR_DIR}/bin/sonar-scanner" ]; then
  tmp=$(mktemp -d)
  curl -fsSL -o "${tmp}/scanner.zip" \
    "https://binaries.sonarsource.com/Distribution/sonar-scanner-cli/sonar-scanner-cli-${SONAR_SCANNER_VERSION}-linux-x64.zip"
  unzip -q "${tmp}/scanner.zip" -d "${tmp}"
  rm -rf "${SONAR_DIR}"
  mv "${tmp}/sonar-scanner-${SONAR_SCANNER_VERSION}-linux-x64" "${SONAR_DIR}"
  rm -rf "${tmp}"
fi
ln -sfn "${SONAR_DIR}/bin/sonar-scanner" /usr/local/bin/sonar-scanner

# Пользователь агента; домашний каталог = Remote root directory ноды в Jenkins.
id jenkins >/dev/null 2>&1 || useradd --system --create-home --home-dir "${AGENT_HOME}" --shell /bin/bash jenkins
usermod -aG docker jenkins
install -d -m 0750 -o root -g jenkins /etc/jenkins-agent

# agent.jar качается с контроллера при каждом старте — версия remoting всегда
# совпадает с Jenkins. Не скачался (контроллер лежит) — стартуем со старым, а
# если и его нет, java упадёт и systemd повторит через RestartSec.
cat > /etc/systemd/system/jenkins-agent.service <<EOF
[Unit]
Description=Jenkins inbound agent ${NODE_NAME}
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service
StartLimitIntervalSec=0

[Service]
User=jenkins
WorkingDirectory=${AGENT_HOME}
ExecStartPre=-/bin/sh -c 'curl -fsSL -o ${AGENT_HOME}/agent.jar.new ${JENKINS_URL}jnlpJars/agent.jar && mv ${AGENT_HOME}/agent.jar.new ${AGENT_HOME}/agent.jar'
ExecStart=/usr/bin/java -jar ${AGENT_HOME}/agent.jar -url ${JENKINS_URL} -name ${NODE_NAME} -secret @/etc/jenkins-agent/secret -webSocket -workDir ${AGENT_HOME}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# enable работает и без запущенного systemd (первый прогон идёт до перезапуска
# дистрибутива с systemd=true) — это просто симлинки.
systemctl enable docker.service containerd.service jenkins-agent.service

echo "provision: ok"
