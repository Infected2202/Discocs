# library-tags

Локальный инструмент: дописать в теги библиотеки то, что нужно discocs для структуры релизов, — тип релиза
(album / ep / single / compilation) и ID Deezer, сохранив ответы Deezer целиком (rank, fans и прочее —
чтобы второй раз не ходить).

Работает на рабочей машине, не в CI и не в образах discocs (см. [../README.md](../README.md)).

## Как сопоставляется

Только для того, что скачал deemix: у каждого файла есть `BARCODE`, а `api.deezer.com/album/upc:<barcode>`
отдаёт ровно этот альбом — без поиска по названиям. Файлы группируются по штрихкоду, не по папке (синглы
deemix лежат россыпью в папке артиста). Каждый файл группы сверяется с треклистом альбома: название +
длительность ±3 с.

Остальная библиотека не трогается: `!OLD!` уже размечен Picard (MBID, тип, каталожный номер), а что там не
размечено — нет в MusicBrainz.

## Запуск

```bash
python dry_run.py                         # H:\data\media\music\Deezer; файлы не меняет — отчёт out/dry_run.txt
python apply.py --sample 20               # пробно; --all — все подтверждённые релизы; --undo — откат
python various.py --dry                   # «Różni wykonawcy», «Различные исполнители», «VA»… → «Various Artists»
python ignore_dupes.py                    # скрыть дубли от Navidrome (.ndignore); --undo — убрать
python navidrome.py scan | check          # сканирование Navidrome / видит ли он записанные типы
python fragments.py --dry                 # собрать сборники, раздробленные Navidrome (нет исполнителя альбома)
python resolve.py                         # тип для альбомов без штрихкода: Deezer/Discogs + строгая сверка; --write
python autotag.py album <id> <папка>      # один релиз — так его вызывает music-fill после загрузки
python retype_ep.py [--dry]               # EP, которым раньше записали album/single, — в ep (откат — apply.py --undo)
python popularity.py [--push]             # связи трек ↔ Deezer и rank/fans → база discocs (recs deezer-import)
```

**Популярность — в базе discocs, не в тегах.** rank/fans меняются, а Navidrome свои теги наружу не отдаёт.
В файлах остаются только ID Deezer. `popularity.py` сопоставляет файлы из журнала записи с песнями
Navidrome по пути (родной `/api/song` — путь от корня библиотеки; одним запросом: постранично Navidrome
0.64 отдаёт страницы внахлёст). Затем отдаёт discocs связи и снимки из кэша. Дальше discocs сам освежает
числа раз в неделю. Новые загрузки попадают туда при следующем запуске, после `navidrome-sync`.

**Автоматически после загрузки.** Сервер music-fill, увидев в очереди deemix «completed», отдаёт задание в
`autotag.tag_release` (папка — `extrasPath` задания, файлы — с BARCODE этого альбома): тип, ID Deezer, «Various
Artists». В журнале music-fill — «теги проставлены» / «ТЕГИ: НЕ ПРОСТАВЛЕНЫ». Треки из Soulseek, разложенные в
альбом deemix, наследуют от соседей и тип релиза (`tagger.ALBUM_KEYS`).

Нужны Python 3.11+ с `requests` и `mutagen`. Учётка Navidrome — из `../music-fill/config.json`.

- **Что пишется.** `RELEASETYPE` (album / ep / single / compilation, как у Deezer) — только если у релиза
  типа ещё нет (существующий — из MusicBrainz, точнее). EP, которые Deezer записал в album/single
  («Bangarang EP»), — ep по названию (`dry_run.release_type`); `DEEZER_ALBUM_ID`, `DEEZER_TRACK_ID`. FLAC —
  Vorbis comment, MP3 — TXXX. Пути не меняются — ID треков в Navidrome прежние.
- **Скорость.** Библиотека на SMB-шаре: упор в сетевые задержки, не в диск, — файлы обрабатываются в 8
  потоков (~80 файлов/с вместо ~5).
- **`.ndignore`.** Navidrome превращает шаблон в регулярку: скобки в имени файла становятся группой,
  ведущий `/` не работает — шаблоны писать без скобок, через `*`. Пустой файл скрывает всю папку.

## Данные (не в git)

| | |
|---|---|
| `cache/scan.json` | теги файлов (перечитываются только изменённые — по размеру и mtime) |
| `cache/deezer_albums.json` | ответы Deezer `album/upc:…` целиком, по штрихкоду |
| `out/dry_run.json`, `out/dry_run.txt` | отчёт пробного прогона: по релизам и сводка с проблемами |
| `out/apply_log.jsonl`, `out/various_log.jsonl` | что записано и что было до (для `--undo`) |
| `out/ignored.json` | какие `.ndignore` положены и какая копия оставлена |
| `out/popularity.json` | что `popularity.py` отдаёт в discocs |
