# Discocs Bot

Telegram-бот для доступа к библиотеке Navidrome с рекомендациями [Discocs](https://github.com/Infected2202/Discocs).

## Требования

- Python 3.11+
- ffmpeg и ffprobe в PATH
- Navidrome (Subsonic API)
- Discocs API на `:8711` с выполненным `navidrome-sync` и построенным индексом

## Установка

```bash
cp .env.example .env
# заполнить .env
run.bat
```

Или вручную:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
```

Заполнить `.env`:

| Переменная | Описание |
|---|---|
| `BOT_TOKEN` | токен от [@BotFather](https://t.me/BotFather) |
| `ALLOWED_TELEGRAM_USER_IDS` | Telegram user ID через запятую |
| `NAVIDROME_*` | URL и креды Navidrome |
| `DISCOCS_BASE_URL` | например `http://192.168.1.41:8711` |

Узнать свой Telegram ID: [@userinfobot](https://t.me/userinfobot).

## Проверка без Telegram

```bash
python scripts/smoke_test.py
```

Проверяет: Navidrome ping, Discocs `/health`, поиск, `/navidrome/similar`, скачивание и транскод.

## Запуск

```bat
run.bat
```

Скрипт сам создаёт `.venv`, ставит зависимости при первом запуске и запускает бота.

Остановить бота: `stop.bat` (или закрыть окно терминала).

## Команды

- `/start`, `/help` — справка
- `/search <query>` — поиск в Navidrome
- `/random` — случайный трек

Под каждым треком: **Отправить** (MP3 в чат, с кэшем `file_id`) и **Радио** (Discocs `/navidrome/similar`).

Ссылка в чате (YouTube, SoundCloud, Bandcamp и другие источники yt-dlp) или
присланный аудиофайл — карточка трека с кнопками **Скачать MP3** и **Радио**
(похожее из библиотеки по звуку самого трека). Если трек уже есть в библиотеке,
бот покажет его карточку вместо скачивания. Подробности и переменные окружения:
[docs/external-links.md](../docs/external-links.md).

## Discocs API

Бот использует тот же контракт, что и Navidrome-плагин Discocs:

```http
GET /navidrome/similar?item_id=<navidrome_song_id>&count=10
```

Модель, фильтры и прочие параметры рекомендаций берутся из **настроек Discocs**, бот передаёт только ID трека и желаемое количество результатов.
