# External seeds in the bot

Send the bot a link — YouTube, SoundCloud, Bandcamp, or any other source
yt-dlp supports — or an audio file, and it answers with a card for the track
behind it. From there the audio can be delivered as MP3, or used as a radio
seed against the library. Nothing is fetched until a button is pressed.

## Flow

```text
link in chat
  -> validate URL (scheme, address, known extractor)
  -> yt-dlp metadata, no download
  -> already in the library?  -> library card: [📥 Получить] [📻 Радио] [🔗 Всё равно скачать]
  -> otherwise card: cover, "Artist — Title", duration, [🎵 Скачать MP3] [📻 Радио]
       -> 🎵  cached file_id? resend instantly; cached download? reuse it;
              otherwise download, prepare mp3, upload
       -> 📻  download, POST the bytes to /api/v1/similar/by-audio, show the
              results as the usual track carousel
```

```text
audio file in chat
  -> card: "Artist — Title", duration, [🔎 Поиск по тегам] [📻 Радио по звуку]
```

Metadata costs one cheap request; a download costs traffic and tens of seconds.
Guessing which of the two someone wanted would be wrong about half the time, so
the card asks. It also shows what the link actually resolved to before anything
is spent on it.

## Radio from something outside the library

The button posts the audio to the backend, which embeds it in memory and
answers with library tracks that sound like it — see
[external-audio.md](external-audio.md), including the guarantee that this never
writes to the catalog. Results render as the same carousel as radio from a
library track, so every card underneath keeps its own **Получить** and
**Радио**.

There is no next page: the seed lives outside the library, so paging would mean
uploading and embedding the same audio again for results already asked for. One
request returns as many tracks as the instant-mix settings allow.

Radio from a file someone sent works the same way, minus the download —
except that Telegram only hands a bot files up to 20 MB. Above that the card
offers tag search alone and says why.

## Already in the library

Before offering a download, the bot searches Navidrome for the linked track and
shows the library card instead when it finds it. Two reasons: nothing has to be
fetched, and radio then starts from an embedding computed from the original
file rather than from a re-encoded copy off YouTube.

Matching is strict — normalized titles must agree outright, and artists too
when the link has one (a title alone has to be at least eight characters, so
"Intro" never matches). A wrong match would send a different song than the one
someone pasted, which is worse than one unnecessary download; the library card
still carries **🔗 Всё равно скачать по ссылке** for when it happens anyway.

## Quality

The source decides the bitrate. YouTube and friends serve 128-160k opus or aac;
re-encoding that to 320k mp3 grows the file without improving what it sounds
like. So:

* an mp3 source is passed through untouched — no re-encode, no generation loss;
* anything else is encoded to mp3 **at the source bitrate**, capped by
  `EXTERNAL_MAX_BITRATE_KBPS`;
* when the source stream does not declare a bitrate, it is estimated from file
  size and duration.

mp3 at the same bitrate as an opus source is audibly worse than that source —
mp3 is the less efficient codec, and this is a lossy-to-lossy conversion.
`EXTERNAL_BITRATE_HEADROOM` multiplies the target bitrate if you want to trade
size for that loss (`1.5` turns a 128k source into a 192k mp3). It defaults to
`1.0`: no headroom, no bitrate inflation.

## Files over 50 MB

A bot upload is capped at 50 MB (`MAX_TELEGRAM_AUDIO_MB`). Longer audio is cut
into equal parts that each fit, and the parts are sent in order with `(2/3)` in
the player title. Cutting is a stream copy at frame boundaries — no re-encode.

Past `EXTERNAL_MAX_PARTS` (default 4, so roughly 200 MB) the bot refuses instead
of flooding the chat. Audio whose duration cannot be determined is refused too:
without a duration there is no way to cut it into parts that fit.

## Caching

Two caches, both keyed by `media_key` — the first 16 hex characters of the
SHA-1 of `extractor:id`, short enough to live in Telegram's 64-byte
`callback_data`.

* **Downloads** stay in `EXTERNAL_CACHE_DIR` under an LRU bound of
  `EXTERNAL_CACHE_MAX_GB` (10 GB). The same link is usually asked for twice —
  send me the file, then build radio from it — and a second download costs both
  traffic and another round with the source. When the directory exceeds its
  bound, least-recently-used files are deleted until it fits.
* **Telegram file ids** live in the bot's SQLite (`external_audio_cache`).
  Re-sending a link that was already delivered uploads nothing at all.

Link metadata is kept in `external_media`, which is also what makes the button
survive a bot restart: the card's `callback_data` carries only the key.

Work files (the prepared mp3, its parts, the thumbnail) live in `TEMP_DIR` and
are deleted by the delivery that made them. A crash in between leaves them
behind, so startup sweeps anything in that directory older than
`TEMP_MAX_AGE_HOURS` and trims the download cache to its bound — a restart is
usually the moment right after such a crash.

## Safety

The bot fetches whatever URL it is given, so the URL is the attack surface.
Before yt-dlp sees anything (`bot/utils/links.py`):

* only `http` and `https`;
* literal private, loopback, link-local, reserved and multicast addresses are
  refused;
* hostnames are resolved and refused if **any** resolved address is internal —
  `nas.local` and `127.0.0.1.nip.io` look like ordinary public names;
* a URL no real extractor claims is refused. yt-dlp's generic extractor accepts
  everything and would happily fetch an arbitrary address and sniff it for
  media, so it is also disabled inside yt-dlp itself
  (`allowed_extractors: ["default", "-generic"]`).

yt-dlp runs as a library, never as a shell command, so no text from a chat
reaches a shell. Downloads are capped by `EXTERNAL_MAX_DOWNLOAD_MB` and
duration by `EXTERNAL_MAX_DURATION_MINUTES`. Playlists and live streams are
refused. The bot's own allow-list (`ALLOWED_TELEGRAM_USER_IDS`) still gates
every handler.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `EXTERNAL_CACHE_DIR` | `data/cache/external` | Where downloads are kept |
| `EXTERNAL_CACHE_MAX_GB` | `10` | LRU bound for that directory |
| `EXTERNAL_MAX_PARTS` | `4` | Most parts one delivery may be split into |
| `EXTERNAL_MAX_DURATION_MINUTES` | `180` | Longest audio the bot will fetch |
| `EXTERNAL_MAX_DOWNLOAD_MB` | `500` | yt-dlp download cap |
| `EXTERNAL_MAX_BITRATE_KBPS` | `320` | Encoding ceiling |
| `EXTERNAL_BITRATE_HEADROOM` | `1.0` | Multiplier over the source bitrate |
| `YTDLP_COOKIES_FILE` | *(empty)* | Cookie jar, if a source starts demanding a login |
| `TEMP_MAX_AGE_HOURS` | `6` | Age at which leftover work files are swept on startup |

In production the cache sits on the bot's existing host bind mount
(`${DISCOCS_STATE_DIR}/discocs_bot`). To put it on another disk, add a bind
mount for it and point `EXTERNAL_CACHE_DIR` at the mount point.

## Keeping it working

Extractors break when sites change; yt-dlp ships fixes within days. The version
is pinned in `discocs_bot/uv.lock`, so updating it means bumping the lock and
rebuilding the image — deliberately, not by auto-updating inside a running
container. Expect to do this occasionally.

In production the bot shares the awg tunnel's network namespace, so requests to
sources leave through the tunnel's exit address. If a source starts asking the
bot to prove it is not a robot, that address is the first thing to suspect, and
`YTDLP_COOKIES_FILE` is the lever.
