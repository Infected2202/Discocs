# tools

Локальные инструменты вокруг библиотеки и discocs — запускаются на рабочей машине вручную.

- Не часть приложения: не входят в образы (`.dockerignore`), в Sonar и в тесты CI.
- Коммит, который меняет только `tools/`, CI не собирает и не деплоит (проверка в стадии `Prepare`
  Jenkinsfile).
- Секреты и данные инструмента — рядом с ним, но не в git: у каждого свой `.gitignore`
  (пропускает только код), образец настроек — `config.example.json`.

| инструмент | что делает |
|---|---|
| [music-fill](music-fill/README.md) | добирает недостающее в библиотеку: план загрузок deemix по истории прослушиваний, дискографиям и каталогам лейблов; чего нет на Deezer — из Soulseek |
| [library-tags](library-tags/README.md) | тип релиза и ID Deezer в теги библиотеки: релизы deemix сопоставляются с Deezer по штрихкоду |
| [describe](describe/README.md) | описания лейблов на русском: локальная модель (LM Studio) ищет в вебе через SearXNG, собирает факты с цитатами и пишет текст |
| [jenkins-agent](jenkins-agent/README.md) | Jenkins-агент `pc` на ПК: изолированный WSL-дистрибутив со своим Docker, основная нода для `discocs_build` |
| [agent](agent/README.md) | воркер на ПК: берёт задачи из раздела Tools админки discocs — describe, запуск и остановка music-fill (см. `docs/tools.md`) |
