"""Названия лейблов: сравнение, ключ склейки, юридический мусор из тегов, самиздат.

Общее для хранилища (склейка лейблов при скане) и синхронизации лейблов (сравнение с
Beatport/Discogs) — поэтому лежит здесь, а не в ``app/services/label_sync``.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Sequence

# Хвосты, которыми одно и то же название различается в тегах и в каталогах.
_LABEL_SUFFIXES = frozenset({
    "records", "recordings", "recording", "music", "discs", "ltd", "limited", "inc", "llc", "label",
})
# Юридическая форма перед названием: Discogs пишет «ООО "Союз Мьюзик"», теги — «Союз Мьюзик».
_LABEL_PREFIXES = frozenset({"ооо", "оао", "зао", "пао", "llc"})

# Копирайтные и дистрибьюторские обёртки вокруг настоящего названия в тегах.
_LEGAL_PREFIX = re.compile(
    r"^\s*(?:\((?:p|c)\)|℗|©)\s*"                                   # «(p) Enter Shikari», «©SNK»
    r"|^\s*universal\s+music\s+(?:\w+\s+)?division\s+(?:label\s+)?(?=\S)",  # «Universal Music Division Decca…»
    re.I,
)
_LEGAL_SUFFIX = re.compile(
    r"\s+under\s+(?:exclusive\s+)?licen[sc]e\b.*$"                  # «X under exclusive license from Y»
    r"|\s*,?\s+(?:distributed|marketed|manufactured)\s+by\b.*$"     # «Method 808, distributed by gamma.»
    r"|\s+via\s+.*\bdistribution\b.*$"                              # «X via Y and Z Distribution»
    r"|\s+distribution\s+deal$",                                    # «… Fontana Distribution Deal»
    re.I,
)
# Российская юридическая форма с названием в кавычках: «ЗАО "Си Ди Лэнд+"», 'OOO "Kvadro-Publishing"'.
_LEGAL_FORM = re.compile(r"^(?:ооо|оао|зао|пао|ао|ooo)\s+[«\"„]?(.+?)[»\"“]?$", re.I)
# Несколько лейблов одной строкой: «OWSLA/Atlantic». Запятая — нет: через неё в тегах пишут
# издателей и копирайт («Truelove Publishing, WARNER/CHAPPELL…, Copyright Control»).
_PARTS = re.compile(r"\s*/\s*")

# Нет лейбла: автоподстановка DistroKid («919813 Records DK») и явные «без лейбла».
_SELF_RELEASED = re.compile(
    r"^\d{4,}\s+records\s+dk$"
    r"|^(?:independent|self[\s-]*released|no\s+label|not\s+on\s+label\b.*|unsigned(?:,.*)?)$",
    re.I,
)

_TRANSLIT = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z",
    "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "i", "є": "e", "ґ": "g",
})


def norm(text: object) -> str:
    """Как normalize_text в discocs: пробелы схлопнуты, без учёта регистра."""
    return " ".join(str(text or "").split()).casefold()


def clean_name(name: object) -> str:
    """Discogs: 'Hermeth (2)' → 'Hermeth', 'Nina Kraviz*' → 'Nina Kraviz'."""
    return re.sub(r"\s*\(\d+\)$", "", str(name or "").strip()).rstrip("*").strip()


def label_key(name: object) -> str:
    """Ключ сравнения названий лейблов: без «(2)», пунктуации, «ООО» и хвоста Records/Discs/Ltd/LLC."""
    words = re.sub(r"[^\w]+", " ", norm(clean_name(name))).split()
    while len(words) > 1 and words[0] in _LABEL_PREFIXES:
        words.pop(0)
    while len(words) > 1 and words[-1] in _LABEL_SUFFIXES:
        words.pop()
    return " ".join(words)


def strip_legal(name: str) -> str:
    """Название без копирайтной/дистрибьюторской обёртки; ничего не осталось — как было."""
    text = name
    while True:
        stripped = _LEGAL_SUFFIX.sub("", _LEGAL_PREFIX.sub("", text)).strip()
        form = _LEGAL_FORM.match(stripped)
        if form:
            stripped = form.group(1).strip()
        if stripped == text:
            break
        text = stripped
    return text or name.strip()


def merge_key(name: object) -> str:
    """Ключ склейки лейблов: ``label_key`` без пробелов и в латинице.

    «ТРИП» = «trip recordings» = «Trip», «Eat Brain» = «Eatbrain», «Fe-Chrome» = «Fe Chrome».
    """
    key = label_key(strip_legal(str(name or ""))).translate(_TRANSLIT).replace(" ", "")
    return key or norm(name)


def label_parts(name: str) -> list[str]:
    """Части строки с несколькими лейблами; одна часть — это не список."""
    parts = [part for part in _PARTS.split(name) if part.strip()]
    return parts if len(parts) > 1 else []


def pick_part(parts: Sequence[str], releases_of: Callable[[str], int]) -> str | None:
    """Из «A / B» — лейбл, у которого больше релизов в библиотеке; ни одного известного — None.

    «ШТЕКЕР / SHTEKER» — одно название на двух языках: берётся первое. «Ki/oon», «KR/LF»,
    «20/20 Vision» остаются целыми — их частей нет среди лейблов.
    """
    if not parts:
        return None
    if len({merge_key(part) for part in parts}) == 1:
        return parts[0]
    best, best_count = None, 0
    for part in parts:
        count = releases_of(part)
        if count > best_count:
            best, best_count = part, count
    return best


def is_self_released(name: object) -> bool:
    """Релиз вышел без лейбла — внешнего лейбла не найти, и это не «не найден»."""
    return bool(_SELF_RELEASED.search(norm(name)))
