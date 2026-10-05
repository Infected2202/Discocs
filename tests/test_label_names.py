"""Названия лейблов: юридическая обёртка, ключ склейки, строка из нескольких лейблов, самиздат."""
from __future__ import annotations

from app.label_names import is_self_released, label_parts, merge_key, pick_part, strip_legal


def test_legal_wrappers_are_cut_down_to_the_label_name():
    assert strip_legal("Universal Music Division Decca Records France") == "Decca Records France"
    assert strip_legal("Universal Music Division Label Fontana Distribution Deal") == "Fontana"
    assert strip_legal("Music Super Circus under exclusive license from Embark Studios") == "Music Super Circus"
    assert strip_legal("The Null Corporation under exclusive license to Interscope Records") == "The Null Corporation"
    assert strip_legal("Method 808, distributed by gamma.") == "Method 808"
    assert strip_legal("Nice Girl World via Alternate Side and Many Hats Distribution") == "Nice Girl World"
    assert strip_legal("(p) Enter Shikari") == "Enter Shikari"
    assert strip_legal("©SNK") == "SNK"
    assert strip_legal('ЗАО "Си Ди Лэнд+"') == "Си Ди Лэнд+"
    assert strip_legal('OOO "Kvadro-Publishing"') == "Kvadro-Publishing"
    # Настоящие названия, похожие на обёртку, не трогаются.
    assert strip_legal("Division Recordings") == "Division Recordings"
    assert strip_legal("Universal Music Domestic Division") == "Universal Music Domestic Division"
    assert strip_legal("Sound Division") == "Sound Division"


def test_merge_key_joins_spellings_scripts_and_suffixes_of_one_label():
    assert merge_key("ТРИП") == merge_key("trip recordings") == merge_key("Trip") == "trip"
    assert merge_key("СТВОЛ") == merge_key("STVOL")
    assert merge_key("Eat Brain") == merge_key("Eatbrain")
    assert merge_key("Fe-Chrome") == merge_key("Fe Chrome")
    assert merge_key("Wind‐up") == merge_key("Wind-Up")
    assert merge_key("Ultra Records, LLC") == merge_key("Ultra Music") == merge_key("Ultra")
    assert merge_key("Universal Music Division Decca Records France") == merge_key("Decca Records France")
    # Цифровой саб-лейбл — отдельный лейбл.
    assert merge_key("Harthouse Digital") != merge_key("Harthouse")
    assert merge_key("Samurai Music") != merge_key("Samurai Red Seal")


def test_several_labels_in_one_string_give_the_one_with_more_releases():
    releases = {"atlantic": 22, "owsla": 3}.get

    def count(part: str) -> int:
        return releases(merge_key(part), 0)

    assert label_parts("OWSLA/Atlantic") == ["OWSLA", "Atlantic"]
    assert label_parts("Beyond The Groove / Blue Note Records") == ["Beyond The Groove", "Blue Note Records"]
    assert label_parts("Warp") == []
    # Через запятую в тегах пишут издателей и копирайт — это не список лейблов.
    assert label_parts("Truelove Publishing, WARNER/CHAPPELL MUSIC LTD (PRS), Copyright Control") == [
        "Truelove Publishing, WARNER", "CHAPPELL MUSIC LTD (PRS), Copyright Control",
    ]
    assert label_parts("Domino,Double Six") == []
    assert pick_part(label_parts("OWSLA/Atlantic"), count) == "Atlantic"
    # Ни одна часть не известна — строка остаётся целой: «Ki/oon», «20/20 Vision», «KR/LF».
    assert pick_part(label_parts("Ki/oon"), count) is None
    assert pick_part(label_parts("20/20 Vision Recordings"), count) is None
    # Одно название на двух языках — первое, даже если лейбла ещё нет.
    assert pick_part(label_parts("ШТЕКЕР / SHTEKER"), count) == "ШТЕКЕР"


def test_self_released_names_are_recognised():
    assert is_self_released("919813 Records DK")
    assert is_self_released("Independent")
    assert is_self_released("Self-released")
    assert is_self_released("Not On Label (Moby Self-released)")
    assert is_self_released("Unsigned, January 10, 2010")
    assert not is_self_released("Independent Records Ltd")
    assert not is_self_released("2020Vision")
    assert not is_self_released("100% Records Back Catalogue")
