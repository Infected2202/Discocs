from bot.storage.models import Album


def format_album_header(album: Album) -> str:
    year = f" · {album.year}" if album.year else ""
    return f"{album.artist}\n{album.title}{year}"
