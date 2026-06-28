from bot.storage.models import Track


def format_track_list(tracks: list[Track], *, header: str) -> str:
    if not tracks:
        return header

    lines = [header, ""]
    for index, track in enumerate(tracks, start=1):
        lines.append(f"{index}. {track.display_line}")
        lines.append(f"   {track.subtitle}")
        lines.append("")
    return "\n".join(lines).rstrip()
