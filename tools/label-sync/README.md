# label-sync

Локальный инструмент: картинки, описания и ссылки лейблов для полки «Лейблы» в discocs
(см. [plans/labels-shelf.md](../../plans/labels-shelf.md)). Разовая синхронизация, повторно —
когда появятся новые лейблы.

Работает на рабочей машине, не в CI и не в образах discocs (см. [../README.md](../README.md)).

## Как ищется

Лейбл находится **через релиз, а не по названию**: по названию Discogs на «Trip» отдаёт
американский TRIP, а не «Трип».

1. Список лейблов — из discocs (`GET /api/v1/labels`).
2. Штрихкоды релизов этого лейбла — из тегов (`../library-tags/cache/scan.json`).
3. Штрихкод → релиз на Beatport (`/catalog/releases/?upc=`) и на Discogs (поиск по `barcode`) →
   лейбл релиза. Несколько штрихкодов голосуют за лейбл.
4. Нет штрихкодов (не deemix) — поиск по названию, но кандидат принимается, только если у него
   нашёлся релиз из библиотеки discocs.

| | откуда |
|---|---|
| картинка | Beatport → Discogs; заглушка Beatport не считается картинкой |
| описание | ru Wikipedia → en Wikipedia (Wikidata по id лейбла Discogs) → Discogs profile → Beatport bio |
| ссылки | Discogs `urls` + страницы лейбла на Discogs и Beatport |

Разметка Discogs сводится к тексту; упоминания артистов остаются как `[a=Имя]` — discocs делает
из них ссылки на артистов из библиотеки.

Результат уходит в discocs: `PUT /api/v1/labels/metadata` (картинка — base64 в JSON).

## Запуск

```bash
cp config.example.json config.json   # адрес discocs; service_token — если включён вход (DISCOCS_AUTH_ENABLED)
python label_sync.py --dry-run --limit 20
python label_sync.py
```

`--only "Ninja Tune"` — один лейбл, `--force` — отправить заново уже отправленные,
`--workers N` — сколько лейблов обрабатывать параллельно (по умолчанию 3). В один поток Discogs
загружен на треть своего лимита (~21 из 60 запросов в минуту): пока скрипт ждёт Beatport или
Википедию, Discogs простаивает. Больше потоков лимит не превысят — модуль Discogs из music-fill
шлёт запросы по одному и притормаживает по заголовку `X-Discogs-Ratelimit-Remaining`, на 429 ждёт.

Нужны Python 3.11+ с `requests` и рабочий [../music-fill](../music-fill/README.md): вход в Beatport
(`python beatport_login.py`) и ключ Discogs берутся из его `config.json`, ответы Beatport/Discogs
кэшируются там же.

## Данные (не в git)

| | |
|---|---|
| `config.json` | адрес discocs и сервисный токен |
| `cache/web.json` | ответы Wikidata и Wikipedia |
| `cache/images/` | скачанные картинки |
| `out/state.json` | что уже отправлено (повторный запуск пропускает) |
| `out/report.txt` | сводка: источники картинок/описаний, ненайденные лейблы |
