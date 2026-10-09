# jenkins-agent

Jenkins-агент `pc` на рабочем ПК: отдельный WSL-дистрибутив `jenkins-agent` со своим
Docker Engine, `sonar-scanner` и inbound-агентом (WebSocket к `http://192.168.1.41:8077/`).
Пока ПК онлайн, `discocs_build` собирается здесь, иначе на LXC `jenkins-agent-01`
(см. «Агенты» в [docs/cicd.md](../../docs/cicd.md)).

Почему отдельный дистрибутив, а не контейнер в Docker Desktop: агенту нужен Docker-демон (вся
сборка — `docker build`/`create`/`run`), а через сокет Docker Desktop джоба может примонтировать
любой путь с `C:\` и видит личные контейнеры. Здесь в `/etc/wsl.conf` выключены `automount` (диски
Windows) и `interop` (запуск `.exe`), агент видит только свой dockerd. Ядро у всех WSL-дистрибутивов
общее, так что это граница от случайностей и чужого кода в Dockerfile, а не песочница уровня ВМ.
Креды джобы (Nexus, Sonar, SSH-ключ деплоя) на время сборки оказываются на агенте — как и на LXC.

## Разовая настройка Jenkins (админ)

1. Плагин **Pipeline Utility Steps** — `Jenkinsfile` выбирает ноду через `nodesByLabel`.
2. Manage Jenkins → Nodes → New node `pc`, Permanent Agent:
   - Number of executors: `3`;
   - Remote root directory: `/var/lib/jenkins-agent`;
   - Labels: пусто (имя ноды само работает как метка);
   - Usage: **Only build jobs with label expressions matching this node** — иначе на ПК поедут
     и чужие джобы с `agent any`;
   - Launch method: Launch agent by connecting it to the controller, галка **Use WebSocket**.
3. На странице ноды скопировать секрет (длинная hex-строка из команды запуска) в файл
   `tools/jenkins-agent/secret` — он в `.gitignore`.

## Установка на ПК

```powershell
powershell -ExecutionPolicy Bypass -File tools\jenkins-agent\setup.ps1
```

Скрипт:

- ставит `Ubuntu-24.04` под именем `jenkins-agent` (`-Location D:\wsl\jenkins-agent`, если VHDX
  нужен не на системном диске), включает разреженный VHDX;
- прогоняет `provision.sh` от root: `wsl.conf`, Docker Engine + buildx, `daemon.json`
  (`insecure-registries` для Nexus, свои подсети, GC кэша BuildKit до 60 ГБ), `sonar-scanner`,
  пользователь `jenkins`, systemd-сервис `jenkins-agent`;
- кладёт секрет в `/etc/jenkins-agent/secret`;
- регистрирует задачу планировщика «jenkins-agent (WSL)» — при входе в Windows держит
  дистрибутив запущенным (без живого процесса WSL его гасит), и показывает статус сервиса.

Повторный запуск — то же самое поверх существующего дистрибутива: так обновляются пакеты и конфиги
после правки `provision.sh` или меняется секрет.

Docker Desktop → Settings → Resources → WSL integration: для `jenkins-agent` интеграция должна
быть **выключена** — иначе Desktop подложит свой docker CLI/сокет поверх местного dockerd.

## Эксплуатация

```powershell
wsl -d jenkins-agent -u root -e systemctl status jenkins-agent
wsl -d jenkins-agent -u root -e journalctl -u jenkins-agent -n 100 --no-pager
wsl -d jenkins-agent -u root -e docker system df
```

Агент поднимается только после входа в Windows (задача на `AtLogOn`). После перезагрузки без
входа нода offline — сборки уходят на `jenkins-agent-01`.

Снести целиком: `Unregister-ScheduledTask 'jenkins-agent (WSL)'`, `wsl --unregister jenkins-agent`,
ноду `pc` в Jenkins удалить или оставить offline.
