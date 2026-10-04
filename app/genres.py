"""Жанры релиза, лейбла и артиста из предсказаний genre_discogs400.

Методика подобрана экспериментом против стилей Discogs (``plans/genres.md``):

* релиз — средняя оценка стиля по его трекам; показываем стили не слабее
  ``RELATIVE_THRESHOLD`` от лучшего и не ниже ``MIN_SCORE``, до ``MAX_RELEASE_GENRES``;
* стили, которые модель ставит систематически мимо (``BLOCKED``), не показываем никогда;
* уточнения (``SUBSTYLE``: Halftime, Jungle, Schranz…) Discogs почти не ставит, вместо них —
  родительский стиль; показываем уточнение, только если родитель тоже среди стилей релиза;
* лейбл и артист — сколько их релизов несут стиль; показываем стили, которые есть хотя бы
  у ``MIN_RELEASE_SHARE`` релизов, до ``MAX_ENTITY_GENRES``.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence

GENRE_MODEL = "genre_discogs400"

RELATIVE_THRESHOLD = 0.25
MIN_SCORE = 0.05
MAX_RELEASE_GENRES = 5
MIN_RELEASE_SHARE = 0.05
MAX_ENTITY_GENRES = 10
# Сколько стилей держать кандидатами: правила ниже отсеивают часть, а показать нужно до 5.
_CANDIDATES = 12

# Уточнение → родители; хотя бы один должен быть среди стилей релиза.
SUBSTYLE: dict[str, frozenset[str]] = {
    "Halftime": frozenset({"Drum n Bass"}),
    "Jungle": frozenset({"Drum n Bass"}),
    "Breakcore": frozenset({"Drum n Bass"}),
    "Deep Techno": frozenset({"Techno"}),
    "Hard Techno": frozenset({"Techno"}),
    "Schranz": frozenset({"Techno", "Hard Techno"}),
    "Minimal Techno": frozenset({"Techno", "Minimal"}),
    "Acid House": frozenset({"House"}),
    "Speedcore": frozenset({"Hardcore"}),
    "Drone": frozenset({"Ambient"}),
    "Glitch": frozenset({"IDM", "Experimental"}),
    "Chillwave": frozenset({"Downtempo"}),
    "Rhythmic Noise": frozenset({"Industrial", "Techno"}),
}

# Стили, которые Discogs почти никогда не подтверждает и которые не уточняют свою ветку:
# на нашей библиотеке Tech Trance показан на 203 релизах и подтверждён один раз.
BLOCKED: frozenset[str] = frozenset({
    "Ballad", "Bassline", "Black Metal", "Bleep", "Brit Pop", "Broken Beat", "Cloud Rap", "Doom Metal",
    "Dream Pop", "EBM", "Electro House", "Ethereal", "Folk", "Folk Metal", "Funk", "Ghetto House",
    "Goth Rock", "Gothic Metal", "Grunge", "Hands Up", "Horrorcore", "Italodance", "K-pop",
    "Melodic Hardcore", "Modern Classical", "Neofolk", "Oi", "Post-Hardcore", "Post-Metal",
    "Progressive Breaks", "Speed Garage", "Synthwave", "Tech Trance", "Technical Death Metal",
    "Tribal House", "Tropical House", "Vaporwave",
})


def style_name(label: str) -> str:
    """``Electronic---Techno`` → ``Techno``."""
    return label.split("---", 1)[-1]


def release_genres(track_scores: Sequence[Mapping[str, float]]) -> list[tuple[str, float]]:
    """Стили релиза по оценкам его треков (метка модели → оценка; нет метки — 0).

    Возвращает ``[(стиль, средняя оценка)]`` от сильного к слабому.
    """
    tracks = [scores for scores in track_scores if scores]
    if not tracks:
        return []
    totals: Counter[str] = Counter()
    for scores in tracks:
        # Один стиль бывает в двух ветках (Electronic/Rock---Experimental): берём сильную.
        per_style: dict[str, float] = {}
        for label, score in scores.items():
            style = style_name(label)
            per_style[style] = max(per_style.get(style, 0.0), float(score))
        totals.update(per_style)
    means = {style: total / len(tracks) for style, total in totals.items()}
    ranked = sorted(means, key=lambda style: (-means[style], style))[:_CANDIDATES]
    best = means[ranked[0]]
    candidates = [s for s in ranked if means[s] >= RELATIVE_THRESHOLD * best and means[s] >= MIN_SCORE]
    present = {s for s in candidates if s not in BLOCKED}
    genres: list[tuple[str, float]] = []
    for style in candidates:
        if style in BLOCKED:
            continue
        parents = SUBSTYLE.get(style)
        if parents is not None and not parents & present:
            continue
        genres.append((style, round(means[style], 4)))
        if len(genres) == MAX_RELEASE_GENRES:
            break
    return genres


def entity_genres(release_genre_lists: Iterable[Sequence[str]]) -> list[tuple[str, int]]:
    """Стили лейбла/артиста: ``[(стиль, сколько релизов его несут)]``.

    Релизы без стилей (не проанализированы) в долю не входят.
    """
    counts: Counter[str] = Counter()
    total = 0
    for genres in release_genre_lists:
        if not genres:
            continue
        total += 1
        counts.update(dict.fromkeys(genres, 1))
    if not total:
        return []
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [(style, n) for style, n in ranked if n >= MIN_RELEASE_SHARE * total][:MAX_ENTITY_GENRES]
