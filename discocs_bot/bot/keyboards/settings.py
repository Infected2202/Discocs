from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from bot.storage.user_prefs import AUDIO_PROFILES

PROFILE_ORDER = ("mp3:192", "mp3:256", "mp3:320", "opus:192", "opus:256", "flac")
BTN_SAVE = "💾 Сохранить"


def profile_button_label(profile: str, current_profile: str) -> str:
    label = str(AUDIO_PROFILES[profile]["label"])
    if profile == current_profile:
        return f"✓ {label}"
    return label


def settings_menu_keyboard(current_profile: str) -> ReplyKeyboardMarkup:
    rows: list[list[KeyboardButton]] = []
    row: list[KeyboardButton] = []
    for profile in PROFILE_ORDER:
        row.append(KeyboardButton(profile_button_label(profile, current_profile)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([KeyboardButton(BTN_SAVE)])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        is_persistent=True,
    )


def profile_from_button(text: str) -> str | None:
    normalized = text.removeprefix("✓ ").strip()
    for profile, spec in AUDIO_PROFILES.items():
        if spec["label"] == normalized:
            return profile
    return None


def is_settings_button(text: str) -> bool:
    if text == BTN_SAVE:
        return True
    return profile_from_button(text) is not None


def settings_keyboard(current_profile: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for profile in PROFILE_ORDER:
        label = AUDIO_PROFILES[profile]["label"]
        if profile == current_profile:
            label = f"✓ {label}"
        row.append(InlineKeyboardButton(label, callback_data=f"pref:{profile}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)
