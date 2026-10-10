"""Второй проход describe — по лейблам, о которых основной ничего не написал («не найден»).

Основной конвейер (research.Researcher) не меняется: второй проход — его наследник с двумя отличиями.

1. Лейбл, который в библиотеке есть только на сборниках (Kraftek, 1605 — треки из «Beatport Top 100»), основной
   проход узнать не может: он ищет на странице наших артистов, а их у такого лейбла нет. Здесь страница
   засчитывается по названию — если она явно про лейбл: карточка Discogs/RA/Beatport/Bandcamp, Википедия,
   название лейбла в заголовке или адресе. Хвосты «Holland», «Records» при сравнении отбрасываются.
2. Фактов не хватило на полный текст — короткая справка в одно-два предложения из того, что есть: чей лейбл,
   откуда, что выпускает. В note итога — «short: …», по этой пометке справки находятся потом.
"""
from __future__ import annotations

import re
from dataclasses import replace

from research import (
    SYSTEM,
    WRITE,
    Fact,
    Researcher,
    Subject,
    _NOT_AN_ARTIST,
    _clean_discogs_names,
    _dedupe_facts,
    _domain,
    _fix_links,
    _fix_typos,
    _norm,
    _sources,
)
from web import Page

LIBRARY = "library"
SHORT_FACTS = 14
# Хвосты названия, без которых лейбл называют на страницах: «Armada Music Holland» — это Armada Music.
_SUFFIX = re.compile(
    r"\s+(?:holland|nederland|netherlands|uk|usa|us|germany|deutschland|france|italy|spain|japan|russia"
    r"|records?|recordings|music|label|imprint|ltd\.?|limited|inc\.?|llc|gmbh|b\.?v\.?)$",
    re.I,
)
# Страницы, которые заведомо про лейбл, а не про артиста или трек: на них хватает и короткого названия.
_LABEL_PAGE = re.compile(
    r"discogs\.com/(?:\w{2}/)?label/|ra\.co/labels/|beatport\.com/(?:\w{2}/)?label/|\.bandcamp\.com"
    r"|wikipedia\.org/wiki/.*(?:record_label|\(label\))|junodownload\.com/labels/|labelsbase\.net/"
    r"|traxsource\.com/label/|soundcloud\.com/",
    re.I,
)
_LABEL_WORD = re.compile(r"\b(?:label|records|recordings|лейбл)\b", re.I)


def compilations_only(subject: Subject) -> bool:
    """В библиотеке и каталоге нет ни одного артиста лейбла — только сборники Various Artists."""
    return not any(not _NOT_AN_ARTIST.search(a) for a in subject.artists + subject.catalog_artists)


def name_variants(name: str) -> list[str]:
    """«Armada Music Holland» → armada music holland, armada music, armada."""
    variants = [_norm(name)]
    while True:
        shorter = _SUFFIX.sub("", variants[-1]).strip()
        if not shorter or shorter == variants[-1]:
            return variants
        variants.append(shorter)


def _words(text: str) -> str:
    return f" {' '.join(re.findall(r'\w+', _norm(text.replace('%20', ' '))))} "


def names_label(subject: Subject, page: Page) -> bool:
    """Страница про лейбл с таким названием: оно в заголовке или адресе. Короткое одно слово («1605», «IAMT»)
    засчитывается только на странице лейбла (карточка, Википедия, слово «label» в заголовке) — иначе это
    может быть год, трек или однофамилец."""
    where = _words(page.title) + _words(page.url)
    for variant in name_variants(subject.name):
        if f" {' '.join(re.findall(r'\w+', variant))} " not in where:
            continue
        if len(variant.split()) >= 2 or len(variant) >= 8:
            return True
        if _LABEL_PAGE.search(page.url) or _LABEL_WORD.search(page.title):
            return True
    return False


def own_artist(subject: Subject) -> str | None:
    """Артист, чьё имя стоит в названии лейбла (Gremlinz Archives — Gremlinz)."""
    label = _words(subject.name)
    for artist in subject.artists:
        if len(_norm(artist)) >= 3 and _words(artist) in label and _words(artist) != label:
            return artist
    return None


def short_prompt(subject: Subject, facts: list[Fact]) -> str:
    listing = "\n".join(
        f"[{i + 1}] ({'our library' if f.url == LIBRARY else _domain(f.url)}) {f.text}"
        + (f"\n    source words: «{f.quote}»" if f.quote else "")
        for i, f in enumerate(facts)
    )
    library = ", ".join(subject.artists) or "none"
    return f"""Label: {subject.name}
Artists of this label in our library (spelling for [a=...] links): {library}

Facts about the label:
{listing}

Little is known about this label, so write a short reference about it in Russian for the label page of a music
library app: one or two sentences, at most 350 characters. Say what the label is: whose label it is, where it is
based, what music it releases, which artists release on it — whatever of this the facts give.

Rules:
- Use only the facts above, add nothing from your own memory.
- Facts from "our library" only tell which artists and releases of this label we have — not when the label was
  founded or how big it is. Say «лейбл [a=X]» (the artist's own label) only if a fact says so or the label is named
  after the artist.
- At most two artist names; no catalogue numbers, no lists of releases, no track titles.
- Wrap every artist name in the form [a=Name], spelled as in our library list if it is there. Names stay in the
  original spelling, never transliterated; a Latin name is not declined, and never half-translated
  («Аланом Wills» → «Alan Wills»).
- Describe the sound in words, not as a row of shop genre tags.
- Natural Russian, encyclopedic tone, no praise, no mention of facts, sources, websites or our library
  («в нашей библиотеке…»).
- If the facts say nothing about the label beyond a single release, answer an empty string."""


class SecondPass(Researcher):
    def _facts(self, subject: Subject, page: Page, *, trusted: bool = False) -> list[Fact]:
        if not trusted and compilations_only(subject) and names_label(subject, page):
            self.log(f"    label page by its name ← {page.url}")
            return super()._facts(subject, page, trusted=True)
        return super()._facts(subject, page, trusted=trusted)

    def _write(self, subject: Subject, facts: list[Fact]) -> tuple[str, str]:
        description, note = super()._write(subject, facts)
        if description:
            return description, note
        short = self._short(subject, facts)
        if short:
            return short, f"short: {note}"
        return "", note

    def _short(self, subject: Subject, facts: list[Fact]) -> str:
        web = [replace(f, text=_clean_discogs_names(f.text)) for f in _dedupe_facts(facts)]
        # Содержательные факты первыми, строки каталога — после: из них справка берёт только «кто выпускается».
        web = sorted(web, key=lambda f: f.kind == "catalog")[:SHORT_FACTS]
        owner = own_artist(subject)
        if not web and not owner:
            self.log("  short: nothing to write from")
            return ""
        library = []
        if subject.artists:
            library.append(Fact(f"Our library has releases on this label by: {', '.join(subject.artists[:3])}.",
                                "", LIBRARY))
        if owner:
            library.append(Fact(f"The label is named after the artist {owner}, whose releases it puts out.", "",
                                LIBRARY))
        given = web + library
        self.used = web
        answer = self.llm.json("short", SYSTEM, short_prompt(subject, given), WRITE, think=False,
                               temperature=0.3, max_tokens=1500)
        text = _fix_typos(answer["description"].strip(), _sources(given))
        if not text:
            self.log("  short: model wrote nothing")
            return ""
        unsupported, style = self._check(text, given)
        if unsupported or style:
            self.log(f"  short unsupported: {unsupported}; style: {style}")
            prompt = (short_prompt(subject, given) + f"\n\nYour previous version:\n{text}\n\nRemove or fix these "
                      "statements, they are not supported by the facts or break the rules:\n"
                      + "\n".join(f"- {s}" for s in unsupported + style))
            text = _fix_typos(self.llm.json("short", SYSTEM, prompt, WRITE, think=False, temperature=0.3,
                                            max_tokens=1500)["description"].strip(), _sources(given))
            if not text or self._check(text, given)[0]:
                self.log("  short: still unsupported, dropped")
                return ""
        return self._proofread(_fix_links(text, subject))
